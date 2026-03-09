"""
Patch 190 — Next Best Move panel

Adds a unified cross-system recommendation engine to the Home tab.
Aggregates PERP buffer health, MEMECOIN gate state, and SPOT portfolio
gaps into a single ranked action for the operator.

Home tab becomes: "What should I do next?"
Other tabs remain: "Why?" / detail / diagnostics.

New endpoint:
  GET /api/home/next-best-move
  → {next_best_move, alternatives, no_action_recommended, generated_at}

Frontend:
  NextBestMovePanel in HomePage.tsx — inserted ABOVE the 4 system cards
  (first visible element after page header — guaranteed above the fold)

Action types (ranked high → low):
  MANAGE  — urgent: perp buffer negative
  BUY     — memecoin signal passes all gates
  DCA     — spot token underallocated + positive gap
  WATCH   — gates open but no signal in band yet
  WAIT    — system gate closed (F&G, AUTO_BUY=false, capacity)
  HOLD    — no action needed, positions healthy

Files changed:
  /root/memecoin_engine/dashboard/backend/routers/home.py
  /root/memecoin_engine/dashboard/frontend/src/sections/HomePage.tsx
"""
import py_compile

HOME_PATH = "/root/memecoin_engine/dashboard/backend/routers/home.py"
HT_PATH   = "/root/memecoin_engine/dashboard/frontend/src/sections/HomePage.tsx"

with open(HOME_PATH) as f:
    home = f.read()

with open(HT_PATH) as f:
    ht = f.read()


# ── Step A: Append next-best-move endpoint to home.py ─────────────────────────

OLD_A = (
    "    except Exception as e:\n"
    "        log.debug(\"whale_summary error: %s\", e)\n"
    "        return {\"total\": 0, \"in_range\": 0, \"scanner_pass\": 0, \"alerts_sent\": 0, \"last_ts\": None}\n"
)

NEW_A = OLD_A + '''

# ── Patch 190: Next Best Move — unified cross-system recommendation ────────────

@router.get("/next-best-move")
def get_next_best_move(_user=Depends(get_current_user)):
    """
    Cross-system 'what to do next' recommendation. P190.
    Aggregates PERP buffer health, MEMECOIN gate state, and SPOT portfolio gap
    into a single ranked action + 2 alternatives.
    Decision support only — no auto-trading changes.

    Actions: MANAGE (urgent) > BUY (meme signal) > DCA (spot gap) >
             WATCH (gates open, no signal) > WAIT (gate closed) > HOLD (nothing to do)
    """
    import sys
    import json as _j
    from datetime import datetime, timezone

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    from utils.db import get_conn  # type: ignore

    candidates = []

    # ── 1. Perp — profit buffer health ────────────────────────────────────────
    try:
        import sqlite3
        _db = os.path.join(root, "data_storage", "engine.db")
        _cp = sqlite3.connect(_db)
        _cp.row_factory = sqlite3.Row
        from utils.tier_manager import get_profit_buffer  # type: ignore
        _buf  = get_profit_buffer(_cp)
        _npos = _cp.execute(
            "SELECT COUNT(*) FROM perp_positions WHERE status=\'OPEN\' AND notes LIKE \'%TIER%\'"
        ).fetchone()[0]
        _cp.close()

        if _buf < 0:
            candidates.append({
                "_rank": 100, "action": "MANAGE", "system": "PERP", "symbol": None,
                "priority": "URGENT",
                "reason": (
                    f"Profit buffer is negative (${_buf:.0f}). "
                    "Check open positions — consider reducing exposure or adding collateral."
                ),
                "blockers": [f"BUFFER=${_buf:.0f}"], "confidence": "high",
            })
        elif _npos > 0:
            candidates.append({
                "_rank": 5, "action": "HOLD", "system": "PERP", "symbol": None,
                "priority": "LOW",
                "reason": f"{_npos} open position(s), buffer=${_buf:.0f}. Positions healthy — hold.",
                "blockers": [], "confidence": "high",
            })
    except Exception as exc:
        log.debug("next_best_move perp: %s", exc)

    # ── 2. Memecoin — gate state ───────────────────────────────────────────────
    try:
        _auto_buy = os.getenv("MEMECOIN_AUTO_BUY", "false").lower() == "true"
        _dry_run  = os.getenv("MEMECOIN_DRY_RUN",  "true").lower()  == "true"
        _max_open = int(os.getenv("MEMECOIN_MAX_OPEN", "3"))
        _fg_val    = None
        _open_cnt  = 0
        _bands     = []
        _multi     = False

        with get_conn() as _cm:
            _fg_row = _cm.execute(
                "SELECT value FROM kv_store WHERE key=\'shared_fear_greed\'"
            ).fetchone()
            if _fg_row:
                try:
                    _fg_val = _j.loads(_fg_row[0]).get("value")
                except Exception:
                    pass

            _open_cnt = _cm.execute(
                "SELECT COUNT(DISTINCT token_mint) FROM memecoin_signal_outcomes WHERE status=\'OPEN\'"
            ).fetchone()[0]

            _lt_row = _cm.execute(
                "SELECT value FROM kv_store WHERE key=\'memecoin_learned_thresholds\'"
            ).fetchone()
            if _lt_row:
                try:
                    _lt    = _j.loads(_lt_row[0])
                    _bands = _lt.get("bands", [])
                    _multi = bool(_lt.get("multi_band_mode", False))
                except Exception:
                    pass

        _fg_thr = 35 if not _dry_run else 25
        _fg_ok  = _fg_val is not None and _fg_val > _fg_thr
        _cap_ok = _open_cnt < _max_open

        _m_blk = []
        if not _auto_buy:
            _m_blk.append("AUTO_BUY=false")
        if not _fg_ok:
            _m_blk.append(f"F&G={_fg_val or \'?\'} (need >{_fg_thr})")
        if not _cap_ok:
            _m_blk.append(f"CAPACITY {_open_cnt}/{_max_open}")

        _best_sig = None
        try:
            from utils.memecoin_scanner import get_cached_signals  # type: ignore
            _sigs = sorted(
                get_cached_signals(), key=lambda s: s.get("score", 0), reverse=True
            )
            if _sigs:
                _s0 = _sigs[0]
                _sc = _s0.get("score", 0)
                if _multi and _bands:
                    _in = any(b["lo"] <= _sc < b["hi"] for b in _bands)
                else:
                    _in = _sc >= float(os.getenv("MEMECOIN_BUY_SCORE_MIN", "65"))
                if _in:
                    _best_sig = _s0
        except Exception:
            pass

        if not _m_blk and _best_sig:
            candidates.append({
                "_rank": 60, "action": "BUY", "system": "MEMECOINS",
                "symbol": _best_sig.get("symbol"), "priority": "NORMAL",
                "reason": (
                    f"All gates pass — {_best_sig.get(\'symbol\')} "
                    f"score={_best_sig.get(\'score\', 0):.0f} in active band. Auto-buy would fire."
                ),
                "blockers": [], "confidence": "high",
            })
        elif not _m_blk:
            candidates.append({
                "_rank": 20, "action": "WATCH", "system": "MEMECOINS", "symbol": None,
                "priority": "LOW",
                "reason": "All system gates open — no signal in active band right now. Check at next scan.",
                "blockers": [], "confidence": "medium",
            })
        elif not _fg_ok:
            _bstr = ""
            if _bands:
                _bstr = " Active bands: " + " + ".join(
                    f"{b[\'lo\']}-{b[\'hi\']}" for b in _bands[:3]
                ) + "."
            candidates.append({
                "_rank": 15, "action": "WAIT", "system": "MEMECOINS", "symbol": None,
                "priority": "LOW",
                "reason": (
                    f"F&G={_fg_val or \'?\'} — below {\'pilot\' if not _dry_run else \'paper\'} "
                    f"gate (>{_fg_thr}). Extreme fear — wait for recovery.{_bstr}"
                ),
                "blockers": _m_blk, "confidence": "high",
            })
        elif not _auto_buy:
            candidates.append({
                "_rank": 8, "action": "WAIT", "system": "MEMECOINS", "symbol": None,
                "priority": "LOW",
                "reason": "MEMECOIN_AUTO_BUY=false — scanner in advisory mode, no buys execute.",
                "blockers": _m_blk, "confidence": "high",
            })
        else:
            candidates.append({
                "_rank": 10, "action": "HOLD", "system": "MEMECOINS", "symbol": None,
                "priority": "LOW",
                "reason": f"Position capacity full ({_open_cnt}/{_max_open}). Wait for resolutions.",
                "blockers": _m_blk, "confidence": "high",
            })
    except Exception as exc:
        log.debug("next_best_move meme: %s", exc)

    # ── 3. Spot — portfolio gap ────────────────────────────────────────────────
    try:
        with get_conn() as _cs:
            _sr = _cs.execute(
                "SELECT value FROM kv_store WHERE key=\'spot_current_signals\'"
            ).fetchone()
        if _sr:
            _spot_map = _j.loads(_sr[0])
            _dca = sorted(
                [(sym, d) for sym, d in _spot_map.items() if (d.get("portfolio_gap") or 0) > 0],
                key=lambda x: x[1].get("portfolio_gap", 0), reverse=True,
            )
            if _dca:
                _sym2, _d2 = _dca[0]
                _gap2 = _d2.get("portfolio_gap", 0)
                _sig2 = _d2.get("signal_type", "WATCH")
                candidates.append({
                    "_rank": 35 if _sig2 == "DCA_NOW" else 12,
                    "action": "DCA", "system": "SPOT", "symbol": _sym2,
                    "priority": "NORMAL" if _sig2 == "DCA_NOW" else "LOW",
                    "reason": (
                        f"{_sym2} is {_gap2:+.1f}% under target allocation. "
                        f"Signal: {_sig2}. Manual buy at discretion."
                    ),
                    "blockers": ["MANUAL_ONLY"], "confidence": "medium",
                })
    except Exception as exc:
        log.debug("next_best_move spot: %s", exc)

    # ── Rank + assemble ────────────────────────────────────────────────────────
    candidates.sort(key=lambda c: c["_rank"], reverse=True)
    for c in candidates:
        c.pop("_rank", None)

    no_action = not candidates or candidates[0]["action"] == "HOLD"

    if not candidates:
        best = {
            "action": "HOLD", "system": None, "symbol": None, "priority": "LOW",
            "reason": "All systems healthy — no immediate action required. Monitor positions.",
            "blockers": [], "confidence": "medium",
        }
        alts = []
    else:
        best = candidates[0]
        alts = candidates[1:3]

    return {
        "next_best_move":        best,
        "alternatives":          alts,
        "no_action_recommended": no_action,
        "generated_at":          datetime.now(timezone.utc).isoformat(),
    }
'''

assert OLD_A in home, "Anchor A not found — check whale_summary error block in home.py"
home = home.replace(OLD_A, NEW_A, 1)
print("Step A: next-best-move endpoint appended to home.py")

with open(HOME_PATH, "w") as f:
    f.write(home)

py_compile.compile(HOME_PATH, doraise=True)
print(f"home.py — py_compile: OK")


# ── Step B: Insert NextBestMovePanel types + component into HomePage.tsx ───────
# Insert before the existing Top Buys Types block.

OLD_B = "// " + "\u2500\u2500" + " Top Buys Types " + "\u2500" * 60 + "\n"  # 62 U+2500 total

NEW_B = (
    "// ── Next Best Move Types ──────────────────────────────────────────────────────\n"
    "\n"
    "interface NBMCandidate {\n"
    "  action:     string\n"
    "  system:     string | null\n"
    "  symbol:     string | null\n"
    "  priority:   string\n"
    "  reason:     string\n"
    "  blockers:   string[]\n"
    "  confidence: string\n"
    "}\n"
    "\n"
    "interface NBMData {\n"
    "  next_best_move:        NBMCandidate\n"
    "  alternatives:          NBMCandidate[]\n"
    "  no_action_recommended: boolean\n"
    "  generated_at:          string | null\n"
    "  error?:                string\n"
    "}\n"
    "\n"
    "// ── Next Best Move colors ─────────────────────────────────────────────────────\n"
    "\n"
    "const ACTION_COLOR: Record<string, string> = {\n"
    "  MANAGE: 'var(--red)',\n"
    "  BUY:    'var(--green)',\n"
    "  DCA:    '#f59e0b',\n"
    "  WATCH:  '#60a5fa',\n"
    "  WAIT:   'var(--dim)',\n"
    "  HOLD:   'var(--dim)',\n"
    "}\n"
    "\n"
    "const PRIORITY_COLOR: Record<string, string> = {\n"
    "  URGENT: 'var(--red)',\n"
    "  NORMAL: 'var(--green)',\n"
    "  LOW:    'var(--dim)',\n"
    "}\n"
    "\n"
    "const SYS_COLOR: Record<string, string> = {\n"
    "  PERP:      'var(--green)',\n"
    "  MEMECOINS: '#60a5fa',\n"
    "  SPOT:      '#f59e0b',\n"
    "}\n"
    "\n"
    "// ── Next Best Move Panel ──────────────────────────────────────────────────────\n"
    "\n"
    "function NextBestMovePanel({ data, loading }: { data: NBMData | undefined; loading: boolean }) {\n"
    "  const nbm    = data?.next_best_move\n"
    "  const alts   = data?.alternatives ?? []\n"
    "  const aColor = nbm ? (ACTION_COLOR[nbm.action] ?? 'rgba(255,255,255,0.15)') : 'rgba(255,255,255,0.15)'\n"
    "\n"
    "  return (\n"
    "    <div style={{\n"
    "      background:           'rgba(255,255,255,0.02)',\n"
    "      border:               '1px solid rgba(255,255,255,0.10)',\n"
    "      borderTop:            `2px solid ${aColor}`,\n"
    "      borderRadius:         '0 0 12px 12px',\n"
    "      backdropFilter:       'blur(20px) saturate(160%)',\n"
    "      WebkitBackdropFilter: 'blur(20px) saturate(160%)',\n"
    "      overflow:             'hidden',\n"
    "    }}>\n"
    "\n"
    "      {/* Header */}\n"
    "      <div style={{\n"
    "        padding: '9px 16px 7px',\n"
    "        borderBottom: '1px solid rgba(255,255,255,0.06)',\n"
    "        display: 'flex', alignItems: 'center', justifyContent: 'space-between',\n"
    "      }}>\n"
    "        <span style={{\n"
    "          color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace',\n"
    "          fontWeight: 700, fontSize: 10, letterSpacing: '0.14em',\n"
    "        }}>NEXT BEST MOVE</span>\n"
    "        <span style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 9 }}>\n"
    "          {data?.generated_at ? fmtAge(data.generated_at.slice(0, 19)) : (loading ? '\u2026' : '\u2014')} \u00b7 P190\n"
    "        </span>\n"
    "      </div>\n"
    "\n"
    "      {/* Top recommendation */}\n"
    "      {loading && !nbm && (\n"
    "        <div style={{ padding: '12px 16px', color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>\n"
    "          loading\u2026\n"
    "        </div>\n"
    "      )}\n"
    "      {data?.error && (\n"
    "        <div style={{ padding: '12px 16px', color: 'var(--red)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>\n"
    "          {data.error}\n"
    "        </div>\n"
    "      )}\n"
    "      {nbm && !data?.error && (\n"
    "        <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'flex-start', gap: 14, flexWrap: 'wrap' }}>\n"
    "\n"
    "          {/* Action */}\n"
    "          <span style={{\n"
    "            fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 18,\n"
    "            color: aColor, flexShrink: 0, minWidth: 64, lineHeight: 1,\n"
    "            paddingTop: 1,\n"
    "          }}>\n"
    "            {nbm.action}\n"
    "          </span>\n"
    "\n"
    "          {/* Detail block */}\n"
    "          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 5 }}>\n"
    "\n"
    "            {/* System \u00b7 symbol \u00b7 priority chips */}\n"
    "            <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>\n"
    "              {nbm.system && (\n"
    "                <span className=\"badge\" style={{\n"
    "                  fontSize: 8,\n"
    "                  color: SYS_COLOR[nbm.system] ?? 'var(--dim)',\n"
    "                  background: `${SYS_COLOR[nbm.system] ?? 'var(--dim)'}18`,\n"
    "                  border: `1px solid ${SYS_COLOR[nbm.system] ?? 'var(--dim)'}44`,\n"
    "                }}>\n"
    "                  {nbm.system}\n"
    "                </span>\n"
    "              )}\n"
    "              {nbm.symbol && (\n"
    "                <span style={{\n"
    "                  fontFamily: 'JetBrains Mono, monospace', fontWeight: 600,\n"
    "                  fontSize: 12, color: 'var(--text2)',\n"
    "                }}>\n"
    "                  {nbm.symbol}\n"
    "                </span>\n"
    "              )}\n"
    "              <span className=\"badge\" style={{\n"
    "                fontSize: 8,\n"
    "                color: PRIORITY_COLOR[nbm.priority] ?? 'var(--dim)',\n"
    "                background: `${PRIORITY_COLOR[nbm.priority] ?? 'rgba(255,255,255,0.05)'}18`,\n"
    "                border: `1px solid ${PRIORITY_COLOR[nbm.priority] ?? 'var(--dim)'}44`,\n"
    "              }}>\n"
    "                {nbm.priority}\n"
    "              </span>\n"
    "            </div>\n"
    "\n"
    "            {/* Reason */}\n"
    "            <span style={{\n"
    "              fontFamily: 'JetBrains Mono, monospace', fontSize: 10,\n"
    "              color: 'var(--text2)', lineHeight: 1.55,\n"
    "            }}>\n"
    "              {nbm.reason}\n"
    "            </span>\n"
    "\n"
    "            {/* Blocker pills */}\n"
    "            {nbm.blockers.length > 0 && (\n"
    "              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>\n"
    "                {nbm.blockers.map(b => (\n"
    "                  <span key={b} className=\"badge\" style={{\n"
    "                    fontSize: 8,\n"
    "                    color: 'var(--red)',\n"
    "                    background: 'rgba(239,68,68,0.07)',\n"
    "                    border: '1px solid rgba(239,68,68,0.22)',\n"
    "                  }}>{b}</span>\n"
    "                ))}\n"
    "              </div>\n"
    "            )}\n"
    "          </div>\n"
    "        </div>\n"
    "      )}\n"
    "\n"
    "      {/* Alternatives row */}\n"
    "      {alts.length > 0 && (\n"
    "        <div style={{\n"
    "          borderTop: '1px solid rgba(255,255,255,0.05)',\n"
    "          padding: '5px 16px 8px',\n"
    "          display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap',\n"
    "        }}>\n"
    "          <span style={{\n"
    "            fontFamily: 'JetBrains Mono, monospace', fontSize: 8,\n"
    "            color: 'var(--dim)', letterSpacing: '0.1em', flexShrink: 0,\n"
    "          }}>ALT</span>\n"
    "          {alts.map((alt, i) => (\n"
    "            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>\n"
    "              <span style={{\n"
    "                fontFamily: 'JetBrains Mono, monospace', fontWeight: 700,\n"
    "                fontSize: 10, color: ACTION_COLOR[alt.action] ?? 'var(--dim)',\n"
    "              }}>\n"
    "                {alt.action}\n"
    "              </span>\n"
    "              {alt.system && (\n"
    "                <span style={{\n"
    "                  fontFamily: 'JetBrains Mono, monospace', fontSize: 9,\n"
    "                  color: SYS_COLOR[alt.system] ?? 'var(--dim)',\n"
    "                }}>\n"
    "                  {alt.system}\n"
    "                </span>\n"
    "              )}\n"
    "              {alt.symbol && (\n"
    "                <span style={{\n"
    "                  fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: 'var(--text2)',\n"
    "                }}>\n"
    "                  {alt.symbol}\n"
    "                </span>\n"
    "              )}\n"
    "              <span style={{\n"
    "                fontFamily: 'JetBrains Mono, monospace', fontSize: 8,\n"
    "                color: 'var(--dim)',\n"
    "                maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',\n"
    "              }}>\n"
    "                \u2014 {alt.reason}\n"
    "              </span>\n"
    "            </div>\n"
    "          ))}\n"
    "        </div>\n"
    "      )}\n"
    "    </div>\n"
    "  )\n"
    "}\n"
    "\n"
    + OLD_B
)

assert OLD_B in ht, "Anchor B not found — check '// ── Top Buys Types' in HomePage.tsx"
ht = ht.replace(OLD_B, NEW_B, 1)
print("Step B: NextBestMovePanel types + component inserted into HomePage.tsx")


# ── Step C: Insert nbmQ query before topCandQ ─────────────────────────────────

OLD_C = (
    "  const topCandQ = useQuery<TopCandidatesData>({\n"
    "    queryKey: ['meme-top-candidates'],\n"
    "    queryFn:  () => api.get('/memecoins/top-candidates').then(r => r.data),\n"
    "    refetchInterval: 30_000,\n"
    "    staleTime:       15_000,\n"
    "  })\n"
)

NEW_C = (
    "  const nbmQ = useQuery<NBMData>({\n"
    "    queryKey: ['home-next-best-move'],\n"
    "    queryFn:  () => api.get('/home/next-best-move').then(r => r.data),\n"
    "    refetchInterval: 30_000,\n"
    "    staleTime:       15_000,\n"
    "  })\n"
    "\n"
    + OLD_C
)

assert OLD_C in ht, "Anchor C not found — check topCandQ query block in HomePage.tsx"
ht = ht.replace(OLD_C, NEW_C, 1)
print("Step C: nbmQ query inserted into HomePage.tsx")


# ── Step D: Insert <NextBestMovePanel> before system cards in render ───────────

OLD_D = "      {/* " + "\u2500\u2500" + " 4 System Cards " + "\u2500" * 48 + " */}\n"  # 50 U+2500 total

NEW_D = (
    "      {/* " + "\u2500\u2500" + " Next Best Move " + "\u2500" * 49 + " */}\n"  # 51 U+2500 total
    + "      <NextBestMovePanel data={nbmQ.data} loading={nbmQ.isLoading} />\n"
    "\n"
    + OLD_D
)

assert OLD_D in ht, "Anchor D not found — check '{/* ── 4 System Cards */}' comment in HomePage.tsx"
ht = ht.replace(OLD_D, NEW_D, 1)
print("Step D: <NextBestMovePanel> inserted above 4 system cards in render")


with open(HT_PATH, "w") as f:
    f.write(ht)

print("\nPatch 190 applied successfully.")
print("  GET /api/home/next-best-move added to home.py router")
print("  NextBestMovePanel inserted ABOVE 4 system cards in HomePage.tsx")
print("  Actions: MANAGE > BUY > DCA > WATCH > WAIT > HOLD")
print("  Blocker pills, alternatives row, 30s refetch")
print("")
print("Post-deploy:")
print("  systemctl restart memecoin-dashboard")
print("  cd /root/memecoin_engine/dashboard/frontend && npm run build")
