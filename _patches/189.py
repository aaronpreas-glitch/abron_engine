"""
Patch 189 — Top Buys decision-support panel

Goal:
  Build an operator-facing "Top Buys" panel for memecoins and spot that
  is backed by real system logic and surfaces complete, trustworthy data.

Backend — GET /api/memecoins/top-candidates:
  Read-only replication of _auto_buy_step() gate logic (P121/P135/P183).
  Evaluates each cached scanner signal against ALL gates without executing
  any buy. Returns BUY_NOW / WATCH / BLOCKED status with per-gate blocker
  reasons. Handles empty scan cache explicitly (signals=[]).

  Gate logic replicated:
    MEMECOIN_AUTO_BUY=true            — system switch
    open positions < MEMECOIN_MAX_OPEN — capacity check
    F&G favorable (>25)               — live mode only
    score in active bands (P183 multi_band_mode)  — signal quality
    rug_label == GOOD                 — safety
    buy_pressure >= 55%               — momentum
    mint_revoked == True              — no inflation risk
    vol_acceleration >= tuner min     — P135 tuner gate
    top_holder_pct <= tuner max       — concentration protection

Frontend — TopBuysPanel in HomePage.tsx:
  Compact 2-column panel inserted between system cards and market
  conditions. Left column: top memecoin candidates with status badge +
  first signal blocker. Right column: spot basket tokens ranked by
  portfolio_gap (most underweight first). Both columns surface empty
  state explicitly. Context badges show mode/F&G/bands/position count.

Files changed:
  /root/memecoin_engine/dashboard/backend/routers/memecoins.py
  /root/memecoin_engine/dashboard/frontend/src/sections/HomePage.tsx

Post-deploy manual steps:
  1. systemctl restart memecoin-dashboard
  2. cd /root/memecoin_engine/dashboard/frontend && npm run build
"""
import py_compile

MEME_PATH = "/root/memecoin_engine/dashboard/backend/routers/memecoins.py"
HOME_PATH = "/root/memecoin_engine/dashboard/frontend/src/sections/HomePage.tsx"


# ── A: Append top-candidates endpoint to memecoins.py ─────────────────────────
# Anchor: last 5 lines of memecoins_score_analysis_ep try/except block.
# This is unique because of the function-name-specific log message.

src = open(MEME_PATH).read()

OLD_A = (
    "    try:\n"
    "        return await asyncio.to_thread(_run)\n"
    "    except Exception as exc:\n"
    "        log.warning(\"memecoins_score_analysis_ep error: %s\", exc)\n"
    "        return {\"error\": str(exc)}\n"
)

NEW_A = OLD_A + (
    "\n"
    "\n"
    "# ── Patch 189: Top buy candidates — gate replication ──────────────────────────\n"
    "\n"
    "@router.get(\"/top-candidates\")\n"
    "async def memecoins_top_candidates_ep(_: str = Depends(get_current_user)):\n"
    "    \"\"\"\n"
    "    Read-only replication of _auto_buy_step() gate logic — decision support.\n"
    "    Evaluates each cached scanner signal against all gates without executing\n"
    "    any buy. Returns BUY_NOW / WATCH / BLOCKED status with per-gate blocker\n"
    "    reasons. Powers the Top Buys panel in HomePage. P189.\n"
    "    \"\"\"\n"
    "    import json as _j\n"
    "\n"
    "    def _run():\n"
    "        # ── Cached scanner signals ────────────────────────────────────────\n"
    "        try:\n"
    "            from utils.memecoin_scanner import get_cached_signals  # type: ignore\n"
    "            signals = sorted(\n"
    "                get_cached_signals(),\n"
    "                key=lambda s: s.get(\"score\", 0), reverse=True\n"
    "            )\n"
    "        except Exception:\n"
    "            signals = []\n"
    "\n"
    "        # ── Env config ───────────────────────────────────────────────────\n"
    "        auto_buy  = os.getenv(\"MEMECOIN_AUTO_BUY\", \"false\").lower() == \"true\"\n"
    "        dry_run   = os.getenv(\"MEMECOIN_DRY_RUN\",  \"true\").lower()  == \"true\"\n"
    "        max_open  = int(os.getenv(\"MEMECOIN_MAX_OPEN\", \"3\"))\n"
    "        env_score = float(os.getenv(\"MEMECOIN_BUY_SCORE_MIN\", \"65\"))\n"
    "\n"
    "        # ── Tuner thresholds (mirrors _auto_buy_step() loading logic) ────\n"
    "        threshold       = env_score\n"
    "        max_score       = 999\n"
    "        vacc_min        = 5.0\n"
    "        holder_max      = 35.0\n"
    "        bands: list     = []\n"
    "        multi_band_mode = False\n"
    "        try:\n"
    "            from utils.db import get_conn  # type: ignore\n"
    "            with get_conn() as conn:\n"
    "                row = conn.execute(\n"
    "                    \"SELECT value FROM kv_store WHERE key='memecoin_learned_thresholds'\"\n"
    "                ).fetchone()\n"
    "            if row:\n"
    "                lt = _j.loads(row[0])\n"
    "                if lt.get(\"confidence\") in (\"medium\", \"high\"):\n"
    "                    t               = lt.get(\"thresholds\", {})\n"
    "                    threshold       = float(t.get(\"min_score\",            threshold))\n"
    "                    max_score       = float(t.get(\"max_score\",            999))\n"
    "                    vacc_min        = float(t.get(\"min_vol_acceleration\", vacc_min))\n"
    "                    holder_max      = float(t.get(\"max_top_holder_pct\",   holder_max))\n"
    "                    bands           = lt.get(\"bands\", [])\n"
    "                    multi_band_mode = bool(lt.get(\"multi_band_mode\", False))\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "        # ── Open positions ───────────────────────────────────────────────\n"
    "        open_mints: set = set()\n"
    "        open_count = 0\n"
    "        try:\n"
    "            from utils.db import get_conn  # type: ignore\n"
    "            with get_conn() as conn:\n"
    "                rows = conn.execute(\n"
    "                    \"SELECT mint FROM memecoin_trades WHERE status='OPEN'\"\n"
    "                ).fetchall()\n"
    "            open_mints = {r[0] for r in rows}\n"
    "            open_count = len(open_mints)\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "        # ── F&G ──────────────────────────────────────────────────────────\n"
    "        fg_value: int | None = None\n"
    "        fg_favorable         = False\n"
    "        try:\n"
    "            from utils.agent_coordinator import get_fear_greed  # type: ignore\n"
    "            fg           = get_fear_greed()\n"
    "            fg_value     = fg.get(\"value\")\n"
    "            fg_favorable = bool(fg.get(\"favorable\", False))\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "        # ── System-level blockers (apply to every signal) ────────────────\n"
    "        sys_blockers: list = []\n"
    "        if not auto_buy:\n"
    "            sys_blockers.append(\"AUTO_BUY=false\")\n"
    "        if open_count >= max_open:\n"
    "            sys_blockers.append(f\"CAPACITY ({open_count}/{max_open})\")\n"
    "        if not dry_run and not fg_favorable:\n"
    "            sys_blockers.append(f\"F&G={fg_value} <25\")\n"
    "\n"
    "        # ── Evaluate each signal (mirrors P183 multi-band gate logic) ────\n"
    "        candidates: list = []\n"
    "        for sig in signals:\n"
    "            mint       = sig.get(\"mint\", \"\")\n"
    "            score      = float(sig.get(\"score\") or 0)\n"
    "            rug        = sig.get(\"rug_label\", \"UNKNOWN\")\n"
    "            bp         = float(sig.get(\"buy_pressure\") or 50.0)\n"
    "            revoked    = bool(sig.get(\"mint_revoked\", False))\n"
    "            vacc       = float(sig.get(\"vol_acceleration\") or 0.0)\n"
    "            holder_pct = float(sig.get(\"top_holder_pct\") or 0.0)\n"
    "\n"
    "            sig_blockers: list = []\n"
    "            if mint in open_mints:\n"
    "                sig_blockers.append(\"ALREADY_OPEN\")\n"
    "\n"
    "            # Score / band gate — exact mirror of P183 multi-band logic\n"
    "            if multi_band_mode and bands:\n"
    "                _min_lo = min(b[\"lo\"] for b in bands)\n"
    "                if score < _min_lo:\n"
    "                    sig_blockers.append(f\"SCORE_BELOW_BANDS ({score:.0f})\")\n"
    "                elif not any(b[\"lo\"] <= score < b[\"hi\"] for b in bands):\n"
    "                    sig_blockers.append(f\"DEAD_ZONE ({score:.0f})\")\n"
    "            else:\n"
    "                if score < threshold:\n"
    "                    sig_blockers.append(f\"SCORE_LOW ({score:.0f}<{threshold:.0f})\")\n"
    "                elif score > max_score:\n"
    "                    sig_blockers.append(f\"SCORE_HIGH ({score:.0f}>{max_score:.0f})\")\n"
    "\n"
    "            if rug != \"GOOD\":        sig_blockers.append(f\"RUG={rug}\")\n"
    "            if bp < 55:              sig_blockers.append(f\"BP={bp:.0f}%<55%\")\n"
    "            if not revoked:          sig_blockers.append(\"MINT_LIVE\")\n"
    "            if vacc < vacc_min:      sig_blockers.append(f\"VACC={vacc:.1f}<{vacc_min:.1f}\")\n"
    "            if holder_pct > holder_max:\n"
    "                sig_blockers.append(f\"HOLDER={holder_pct:.0f}%>{holder_max:.0f}%\")\n"
    "\n"
    "            all_blockers = sys_blockers + sig_blockers\n"
    "            if not all_blockers:\n"
    "                status = \"BUY_NOW\"\n"
    "            elif not sig_blockers:\n"
    "                status = \"WATCH\"    # signal is clean; only system-level gate blocking\n"
    "            else:\n"
    "                status = \"BLOCKED\"\n"
    "\n"
    "            candidates.append({\n"
    "                \"mint\":             mint,\n"
    "                \"symbol\":           sig.get(\"symbol\", \"?\"),\n"
    "                \"score\":            round(score, 1),\n"
    "                \"rug_label\":        rug,\n"
    "                \"buy_pressure\":     round(bp, 1),\n"
    "                \"mint_revoked\":     revoked,\n"
    "                \"vol_acceleration\": round(vacc, 2),\n"
    "                \"top_holder_pct\":   round(holder_pct, 1),\n"
    "                \"mcap_usd\":         sig.get(\"mcap_usd\"),\n"
    "                \"narrative\":        sig.get(\"narrative\"),\n"
    "                \"scanned_at\":       sig.get(\"scanned_at\"),\n"
    "                \"status\":           status,\n"
    "                \"blockers\":         all_blockers,\n"
    "                \"signal_blockers\":  sig_blockers,\n"
    "            })\n"
    "\n"
    "        return {\n"
    "            \"candidates\":      candidates,\n"
    "            \"signal_count\":    len(signals),\n"
    "            \"open_count\":      open_count,\n"
    "            \"max_open\":        max_open,\n"
    "            \"dry_run\":         dry_run,\n"
    "            \"auto_buy\":        auto_buy,\n"
    "            \"fg_value\":        fg_value,\n"
    "            \"fg_favorable\":    fg_favorable,\n"
    "            \"multi_band_mode\": multi_band_mode,\n"
    "            \"active_bands\":    [{\"lo\": b[\"lo\"], \"hi\": b[\"hi\"], \"wr\": b.get(\"wr\")}\n"
    "                                 for b in bands],\n"
    "            \"sys_blockers\":    sys_blockers,\n"
    "        }\n"
    "\n"
    "    try:\n"
    "        return await asyncio.to_thread(_run)\n"
    "    except Exception as exc:\n"
    "        log.warning(\"memecoins_top_candidates_ep error: %s\", exc)\n"
    "        return {\"error\": str(exc), \"candidates\": [], \"signal_count\": 0}\n"
)

assert OLD_A in src, (
    "Anchor A not found — check end of memecoins_score_analysis_ep try/except in memecoins.py. "
    "Expected: log.warning('memecoins_score_analysis_ep error: %s', exc)"
)
src = src.replace(OLD_A, NEW_A, 1)
print("Step A: /api/memecoins/top-candidates endpoint appended to memecoins.py")

with open(MEME_PATH, "w") as f:
    f.write(src)

py_compile.compile(MEME_PATH, doraise=True)
print("memecoins.py — py_compile: OK")


# ── B: Add TopBuysPanel types + component before HomePage function ─────────────
# Anchor: the "// ── Main ──" section divider. Unique in file.

home = open(HOME_PATH).read()

OLD_B = (
    "// ── Main ──────────────────────────────────────────────────────────────────────\n"
    "\n"
    "export function HomePage() {\n"
)

NEW_B = (
    "// ── Top Buys Types ────────────────────────────────────────────────────────────\n"
    "\n"
    "interface TopCandidate {\n"
    "  symbol:          string\n"
    "  score:           number\n"
    "  status:          'BUY_NOW' | 'WATCH' | 'BLOCKED'\n"
    "  blockers:        string[]\n"
    "  signal_blockers: string[]\n"
    "  rug_label:       string\n"
    "  buy_pressure:    number\n"
    "  narrative:       string | null\n"
    "  scanned_at:      string | null\n"
    "}\n"
    "\n"
    "interface TopCandidatesData {\n"
    "  candidates:      TopCandidate[]\n"
    "  signal_count:    number\n"
    "  open_count:      number\n"
    "  max_open:        number\n"
    "  dry_run:         boolean\n"
    "  auto_buy:        boolean\n"
    "  fg_value:        number | null\n"
    "  fg_favorable:    boolean\n"
    "  multi_band_mode: boolean\n"
    "  active_bands:    { lo: number; hi: number; wr: number | null }[]\n"
    "  sys_blockers:    string[]\n"
    "  error?:          string\n"
    "}\n"
    "\n"
    "interface SpotSignalEntry {\n"
    "  signal_type:   string\n"
    "  score:         number\n"
    "  portfolio_gap: number\n"
    "}\n"
    "\n"
    "interface SpotSignalsData {\n"
    "  signals:            Record<string, SpotSignalEntry>\n"
    "  signals_updated_at: string | null\n"
    "  learning: {\n"
    "    total:           number\n"
    "    complete:        number\n"
    "    tuner_threshold: number\n"
    "    complete_pct:    number\n"
    "    confidence:      string\n"
    "  }\n"
    "}\n"
    "\n"
    "// ── Top Buys Panel ────────────────────────────────────────────────────────────\n"
    "\n"
    "const STATUS_COLOR: Record<string, string> = {\n"
    "  BUY_NOW: 'var(--green)',\n"
    "  WATCH:   '#f59e0b',\n"
    "  BLOCKED: 'var(--dim)',\n"
    "}\n"
    "\n"
    "const SPOT_SIGNAL_COLOR: Record<string, string> = {\n"
    "  DCA_NOW: 'var(--green)',\n"
    "  WATCH:   '#f59e0b',\n"
    "  HOLD:    'var(--dim)',\n"
    "  AVOID:   'var(--red)',\n"
    "}\n"
    "\n"
    "function TopBuysPanel({\n"
    "  meme,\n"
    "  spot,\n"
    "}: {\n"
    "  meme: TopCandidatesData | undefined\n"
    "  spot: SpotSignalsData | undefined\n"
    "}) {\n"
    "  const spotEntries = spot?.signals\n"
    "    ? Object.entries(spot.signals)\n"
    "        .map(([sym, s]) => ({ sym, ...s }))\n"
    "        .sort((a, b) => (b.portfolio_gap ?? 0) - (a.portfolio_gap ?? 0))\n"
    "        .slice(0, 6)\n"
    "    : []\n"
    "\n"
    "  const memeCands = (meme?.candidates ?? []).slice(0, 6)\n"
    "\n"
    "  const rowStyle = {\n"
    "    display: 'flex', alignItems: 'center', gap: 6,\n"
    "    padding: '3px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',\n"
    "  }\n"
    "\n"
    "  return (\n"
    "    <div style={{\n"
    "      background:           'rgba(255,255,255,0.015)',\n"
    "      border:               '1px solid rgba(255,255,255,0.08)',\n"
    "      borderTop:            '2px solid rgba(255,255,255,0.08)',\n"
    "      borderRadius:         '0 0 12px 12px',\n"
    "      backdropFilter:       'blur(20px) saturate(160%)',\n"
    "      WebkitBackdropFilter: 'blur(20px) saturate(160%)',\n"
    "      overflow:             'hidden',\n"
    "    }}>\n"
    "\n"
    "      {/* Panel header */}\n"
    "      <div style={{\n"
    "        padding:        '10px 16px 8px',\n"
    "        borderBottom:   '1px solid rgba(255,255,255,0.06)',\n"
    "        display:        'flex',\n"
    "        alignItems:     'center',\n"
    "        justifyContent: 'space-between',\n"
    "      }}>\n"
    "        <span style={{\n"
    "          color:       'var(--text2)',\n"
    "          fontFamily:  'JetBrains Mono, monospace',\n"
    "          fontWeight:  700, fontSize: 10, letterSpacing: '0.14em',\n"
    "        }}>TOP BUYS</span>\n"
    "        <span style={{\n"
    "          color:      'var(--dim)',\n"
    "          fontFamily: 'JetBrains Mono, monospace',\n"
    "          fontSize:   9,\n"
    "        }}>DECISION SUPPORT · P189</span>\n"
    "      </div>\n"
    "\n"
    "      {/* Two-column body */}\n"
    "      <div style={{ display: 'flex', minHeight: 100 }}>\n"
    "\n"
    "        {/* Left: Memecoins */}\n"
    "        <div style={{ flex: 1, padding: '10px 16px', minWidth: 0 }}>\n"
    "          <div style={{\n"
    "            color: '#60a5fa', fontFamily: 'JetBrains Mono, monospace',\n"
    "            fontWeight: 700, fontSize: 9, letterSpacing: '0.12em', marginBottom: 6,\n"
    "          }}>MEMECOINS</div>\n"
    "\n"
    "          {/* Context badges */}\n"
    "          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 6 }}>\n"
    "            {meme && (\n"
    "              <>\n"
    "                <span className=\"badge\" style={{\n"
    "                  fontSize: 8,\n"
    "                  color:      meme.dry_run ? 'var(--amber)' : 'var(--green)',\n"
    "                  background: meme.dry_run ? 'rgba(245,158,11,0.1)' : 'rgba(0,212,138,0.1)',\n"
    "                  border: `1px solid ${meme.dry_run ? 'rgba(245,158,11,0.3)' : 'rgba(0,212,138,0.3)'}`,\n"
    "                }}>{meme.dry_run ? 'PAPER' : 'LIVE'}</span>\n"
    "                <span className=\"badge\" style={{ fontSize: 8, color: 'var(--dim)' }}>\n"
    "                  {meme.open_count}/{meme.max_open} pos\n"
    "                </span>\n"
    "                {meme.fg_value != null && (\n"
    "                  <span className=\"badge\" style={{\n"
    "                    fontSize:   8,\n"
    "                    color:      meme.fg_favorable ? 'var(--green)' : 'var(--red)',\n"
    "                    background: meme.fg_favorable ? 'rgba(0,212,138,0.08)' : 'rgba(239,68,68,0.08)',\n"
    "                    border: `1px solid ${meme.fg_favorable ? 'rgba(0,212,138,0.25)' : 'rgba(239,68,68,0.25)'}`,\n"
    "                  }}>F&G={meme.fg_value}</span>\n"
    "                )}\n"
    "                {meme.multi_band_mode && meme.active_bands.length > 0 && (\n"
    "                  <span className=\"badge\" style={{ fontSize: 8, color: '#60a5fa' }}>\n"
    "                    {meme.active_bands.map(b => `${b.lo}\\u2013${b.hi}`).join(' + ')}\n"
    "                  </span>\n"
    "                )}\n"
    "              </>\n"
    "            )}\n"
    "          </div>\n"
    "\n"
    "          {/* Candidates */}\n"
    "          {!meme && (\n"
    "            <div style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>loading\u2026</div>\n"
    "          )}\n"
    "          {meme?.error && (\n"
    "            <div style={{ color: 'var(--red)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>{meme.error}</div>\n"
    "          )}\n"
    "          {meme && !meme.error && meme.signal_count === 0 && (\n"
    "            <div style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>\n"
    "              scanner cache empty \u2014 waiting for next scan\n"
    "            </div>\n"
    "          )}\n"
    "          {meme && !meme.error && meme.signal_count > 0 && memeCands.length === 0 && (\n"
    "            <div style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>\n"
    "              {meme.signal_count} signal(s) \u2014 none reach candidate threshold\n"
    "            </div>\n"
    "          )}\n"
    "          {memeCands.map(c => (\n"
    "            <div key={c.symbol} style={rowStyle}>\n"
    "              <span style={{\n"
    "                fontFamily: 'JetBrains Mono, monospace', fontSize: 8, fontWeight: 700,\n"
    "                color: STATUS_COLOR[c.status] ?? 'var(--dim)', minWidth: 54, flexShrink: 0,\n"
    "              }}>{c.status}</span>\n"
    "              <span style={{\n"
    "                fontFamily: 'JetBrains Mono, monospace', fontWeight: 600,\n"
    "                fontSize: 12, color: 'var(--text2)', flex: 1,\n"
    "              }}>{c.symbol}</span>\n"
    "              <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--muted)' }}>\n"
    "                {c.score.toFixed(0)}\n"
    "              </span>\n"
    "              {c.signal_blockers.length > 0 && (\n"
    "                <span style={{\n"
    "                  fontFamily: 'JetBrains Mono, monospace', fontSize: 8, color: 'var(--dim)',\n"
    "                  maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',\n"
    "                }}>{c.signal_blockers[0]}</span>\n"
    "              )}\n"
    "            </div>\n"
    "          ))}\n"
    "        </div>\n"
    "\n"
    "        {/* Divider */}\n"
    "        <div style={{ width: 1, background: 'rgba(255,255,255,0.07)', flexShrink: 0 }} />\n"
    "\n"
    "        {/* Right: Spot */}\n"
    "        <div style={{ flex: 1, padding: '10px 16px', minWidth: 0 }}>\n"
    "          <div style={{\n"
    "            color: '#f59e0b', fontFamily: 'JetBrains Mono, monospace',\n"
    "            fontWeight: 700, fontSize: 9, letterSpacing: '0.12em', marginBottom: 6,\n"
    "          }}>SPOT ACCUMULATION</div>\n"
    "\n"
    "          {/* Context badges */}\n"
    "          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 6 }}>\n"
    "            {spot && (\n"
    "              <>\n"
    "                <span className=\"badge\" style={{ fontSize: 8, color: 'var(--amber)' }}>MANUAL BUYS</span>\n"
    "                <span className=\"badge\" style={{ fontSize: 8, color: 'var(--dim)' }}>\n"
    "                  {Object.keys(spot.signals).length} tokens\n"
    "                </span>\n"
    "                {spot.signals_updated_at && (\n"
    "                  <span className=\"badge\" style={{ fontSize: 8, color: 'var(--dim)' }}>\n"
    "                    {fmtAge(spot.signals_updated_at)}\n"
    "                  </span>\n"
    "                )}\n"
    "              </>\n"
    "            )}\n"
    "          </div>\n"
    "\n"
    "          {/* Signals */}\n"
    "          {!spot && (\n"
    "            <div style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>loading\u2026</div>\n"
    "          )}\n"
    "          {spot && spotEntries.length === 0 && (\n"
    "            <div style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>no basket tokens</div>\n"
    "          )}\n"
    "          {spotEntries.map(entry => (\n"
    "            <div key={entry.sym} style={rowStyle}>\n"
    "              <span style={{\n"
    "                fontFamily: 'JetBrains Mono, monospace', fontSize: 8, fontWeight: 700,\n"
    "                color: SPOT_SIGNAL_COLOR[entry.signal_type] ?? 'var(--dim)',\n"
    "                minWidth: 54, flexShrink: 0,\n"
    "              }}>{entry.signal_type}</span>\n"
    "              <span style={{\n"
    "                fontFamily: 'JetBrains Mono, monospace', fontWeight: 600,\n"
    "                fontSize: 12, color: 'var(--text2)', flex: 1,\n"
    "              }}>{entry.sym}</span>\n"
    "              <span style={{\n"
    "                fontFamily: 'JetBrains Mono, monospace', fontSize: 10,\n"
    "                color: entry.portfolio_gap > 0 ? 'var(--green)'\n"
    "                     : entry.portfolio_gap < 0 ? 'var(--red)'\n"
    "                     : 'var(--dim)',\n"
    "              }}>\n"
    "                {entry.portfolio_gap > 0 ? '+' : ''}{entry.portfolio_gap.toFixed(1)}%\n"
    "              </span>\n"
    "            </div>\n"
    "          ))}\n"
    "        </div>\n"
    "      </div>\n"
    "    </div>\n"
    "  )\n"
    "}\n"
    "\n"
    "// ── Main ──────────────────────────────────────────────────────────────────────\n"
    "\n"
    "export function HomePage() {\n"
)

assert OLD_B in home, (
    "Anchor B not found — check '// ── Main ──' section divider in HomePage.tsx"
)
home = home.replace(OLD_B, NEW_B, 1)
print("Step B: TopBuysPanel types + component added to HomePage.tsx")


# ── C: Add topCandQ + spotSigsQ queries inside HomePage ───────────────────────
# Anchor: the fundingQ query block. Unique inside HomePage().

OLD_C = (
    "  const fundingQ = useQuery<FundingData>({\n"
    "    queryKey: ['funding-current'],\n"
    "    queryFn:  () => api.get('/funding/current').then(r => r.data),\n"
    "    refetchInterval: 120_000,\n"
    "    staleTime: 60_000,\n"
    "  })\n"
)

NEW_C = OLD_C + (
    "\n"
    "  const topCandQ = useQuery<TopCandidatesData>({\n"
    "    queryKey: ['meme-top-candidates'],\n"
    "    queryFn:  () => api.get('/memecoins/top-candidates').then(r => r.data),\n"
    "    refetchInterval: 30_000,\n"
    "    staleTime:       15_000,\n"
    "  })\n"
    "\n"
    "  const spotSigsQ = useQuery<SpotSignalsData>({\n"
    "    queryKey: ['spot-signals'],\n"
    "    queryFn:  () => api.get('/spot/signals').then(r => r.data),\n"
    "    refetchInterval: 60_000,\n"
    "    staleTime:       30_000,\n"
    "  })\n"
)

assert OLD_C in home, (
    "Anchor C not found — check fundingQ useQuery block in HomePage"
)
home = home.replace(OLD_C, NEW_C, 1)
print("Step C: topCandQ + spotSigsQ queries added to HomePage")


# ── D: Insert <TopBuysPanel> before <FundingPanel> in render ──────────────────
# Anchor: Market Conditions comment + FundingPanel line. Unique in render.

OLD_D = (
    "      {/* ── Market Conditions ────────────────────────────────────────────── */}\n"
    "      <FundingPanel data={fundingQ.data} loading={fundingQ.isLoading} />\n"
)

NEW_D = (
    "      {/* ── Top Buys ─────────────────────────────────────────────────────── */}\n"
    "      <TopBuysPanel meme={topCandQ.data} spot={spotSigsQ.data} />\n"
    "\n"
    "      {/* ── Market Conditions ────────────────────────────────────────────── */}\n"
    "      <FundingPanel data={fundingQ.data} loading={fundingQ.isLoading} />\n"
)

assert OLD_D in home, (
    "Anchor D not found — check Market Conditions comment in HomePage render"
)
home = home.replace(OLD_D, NEW_D, 1)
print("Step D: <TopBuysPanel> inserted before <FundingPanel> in HomePage render")

with open(HOME_PATH, "w") as f:
    f.write(home)

print("HomePage.tsx — written OK")
print("\nPatch 189 applied successfully.")
print("  A. GET /api/memecoins/top-candidates — gate replication, BUY_NOW/WATCH/BLOCKED per signal")
print("  B. TopBuysPanel + types — 2-col: memecoins (BUY_NOW/WATCH/BLOCKED) + spot (signal+gap)")
print("  C. topCandQ (30s) + spotSigsQ (60s) queries in HomePage")
print("  D. <TopBuysPanel> inserted between system cards and FundingPanel")
print()
print("Post-deploy manual steps:")
print("  1. systemctl restart memecoin-dashboard")
print("  2. cd /root/memecoin_engine/dashboard/frontend && npm run build")
