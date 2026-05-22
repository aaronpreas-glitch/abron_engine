import { useQuery } from '@tanstack/react-query'
import { api } from '../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ValidationTrack {
  scope: string
  total_events: number
  complete_events: number
  pending_events: number
  wr_1h: number | null
  wr_4h: number | null
  wr_24h: number | null
  avg_confluence_score: number | null
}

interface ConfluenceStats {
  total_events: number
  complete_events: number
  pending_events: number
  phase: string
  next_milestone: number | null
  wr_1h: number | null
  wr_4h: number | null
  wr_24h: number | null
  avg_confluence_score: number | null
  recent_dual_48h: number | null
  recent_triple_48h: number | null
  recent_structural_48h: number | null
  last_ts: string | null
  validation_tracks?: {
    true_confluence:     ValidationTrack
    structural_confluence: ValidationTrack
  }
}

interface ConfluenceEvent {
  id: number
  ts_utc: string
  token_symbol: string
  token_mint: string
  whale_score: number | null
  memecoin_score: number | null
  confluence_score: number | null
  market_cap_usd: number | null
  price_at_event: number | null
  return_1h_pct: number | null
  return_4h_pct: number | null
  return_24h_pct: number | null
  outcome_status: string
  alert_sent: number
  sources: string | null          // JSON array: ["whale_watch","memecoin","smart_wallet"]
  confluence_type: string | null  // "DUAL" | "TRIPLE" | "STRUCTURAL_DUAL" | "STRUCTURAL_TRIPLE"
  source_count: number | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const C = '#06b6d4'  // cyan accent

function fmtAge(ts: string): string {
  const diff = (Date.now() - new Date(ts.includes('T') ? ts : ts + 'Z').getTime()) / 1000
  if (diff < 60)    return `${Math.floor(diff)}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function fmtRet(n: number | null): { text: string; color: string } {
  if (n == null) return { text: '—', color: 'var(--dim)' }
  return {
    text: (n > 0 ? '+' : '') + n.toFixed(1) + '%',
    color: n > 0 ? 'var(--green)' : n < 0 ? 'var(--red)' : 'var(--muted)',
  }
}

function fmtMc(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000)     return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toFixed(0)}`
}

function fmtScore(n: number | null): string {
  if (n == null) return '—'
  return n.toFixed(1)
}

// Parse sources JSON and return set of source keys
function parseSources(sources: string | null): Set<string> {
  if (!sources) return new Set(['whale_watch', 'memecoin'])
  try { return new Set(JSON.parse(sources)) }
  catch { return new Set(['whale_watch', 'memecoin']) }
}

// Small signal source badges: W M S
function SignalBadges({ sources }: { sources: string | null }) {
  const srcs = parseSources(sources)
  const badges = [
    { key: 'whale_watch',   label: 'W', color: '#a78bfa', title: 'Whale Watch'   },
    { key: 'memecoin',      label: 'M', color: '#60a5fa', title: 'Meme Scanner'  },
    { key: 'smart_wallet',  label: 'S', color: '#8b5cf6', title: 'Smart Wallet'  },
  ]
  return (
    <div style={{ display: 'flex', gap: 3 }}>
      {badges.map(b => (
        <span key={b.key} title={b.title} style={{
          fontSize: 8, fontWeight: 700,
          fontFamily: 'JetBrains Mono, monospace',
          color:       srcs.has(b.key) ? b.color : 'rgba(255,255,255,0.12)',
          background:  srcs.has(b.key) ? `${b.color}18` : 'transparent',
          border:      `1px solid ${srcs.has(b.key) ? b.color + '44' : 'rgba(255,255,255,0.08)'}`,
          borderRadius: 3, padding: '1px 4px',
          letterSpacing: '0.06em',
        }}>
          {b.label}
        </span>
      ))}
    </div>
  )
}

function wrCell(wr: number | null, total: number) {
  if (wr == null || total < 3) return <span style={{ color: 'var(--dim)' }}>—</span>
  const color = wr >= 50 ? 'var(--green)' : wr >= 35 ? '#f59e0b' : 'var(--red)'
  return <span style={{ color, fontWeight: 700 }}>{wr.toFixed(1)}%</span>
}

// ── Phase progress bar ────────────────────────────────────────────────────────

const PHASES = [
  { min: 0,   max: 20,  label: 'OBSERVE',   desc: 'Logging confluences, building dataset',           color: '#94a3b8' },
  { min: 20,  max: 50,  label: 'ANALYZE',   desc: 'Win rates visible — patterns emerging',            color: '#f59e0b' },
  { min: 50,  max: 100, label: 'VALIDATE',  desc: 'Telegram alerts live — signal integrity check',    color: C        },
  { min: 100, max: Infinity, label: 'INTEGRATE', desc: 'Paper signal: confluence = auto-watch queue', color: '#00d48a' },
]

function currentPhase(total: number) {
  return PHASES.find(p => total < p.max) ?? PHASES[PHASES.length - 1]
}

function PhaseBar({ total, nextMilestone }: { total: number; nextMilestone: number | null }) {
  const phase = currentPhase(total)
  const pct   = nextMilestone ? Math.min(100, (total / nextMilestone) * 100) : 100

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 11, color: phase.color }}>
          {phase.label}
        </span>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--dim)' }}>
          {phase.desc}
        </span>
        {nextMilestone && (
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--muted)' }}>
            {total} / {nextMilestone}
          </span>
        )}
      </div>
      <div style={{
        height: 4, borderRadius: 2,
        background: 'rgba(255,255,255,0.06)',
        overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          width: `${pct}%`,
          background: phase.color,
          borderRadius: 2,
          transition: 'width 0.4s ease',
        }} />
      </div>
    </div>
  )
}

// ── Stats cards ───────────────────────────────────────────────────────────────

function StatCard({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div style={{
      background: 'rgba(6,182,212,0.04)',
      border: '1px solid rgba(6,182,212,0.18)',
      borderRadius: 8, padding: '12px 16px',
      minWidth: 100, flex: 1,
    }}>
      <div style={{ fontSize: 9, fontFamily: 'JetBrains Mono, monospace', color: 'var(--muted)', letterSpacing: '0.1em', marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 18, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: C }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 9, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dim)', marginTop: 4 }}>
          {sub}
        </div>
      )}
    </div>
  )
}

// ── Proof track panel ─────────────────────────────────────────────────────────

const STRUCT_COLOR = '#a78bfa'  // purple — structural / weaker class

function TrackPanel({
  label, track, accent, earlyNote,
}: {
  label: string
  track: ValidationTrack
  accent: string
  earlyNote?: string
}) {
  const MONO = { fontFamily: 'JetBrains Mono, monospace' }
  const hasWr = track.complete_events >= 3
  return (
    <div style={{
      flex: 1, minWidth: 220,
      background: `${accent}05`,
      border: `1px solid ${accent}22`,
      borderLeft: `3px solid ${accent}60`,
      borderRadius: '0 6px 6px 0',
      padding: '10px 14px',
    }}>
      {/* Track label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ ...MONO, fontSize: 9, fontWeight: 700, color: accent, letterSpacing: '0.10em' }}>
          {label}
        </span>
        {earlyNote && (
          <span style={{
            ...MONO, fontSize: 7, fontWeight: 700, letterSpacing: '0.08em',
            color: accent,
            background: `${accent}12`,
            border: `1px solid ${accent}30`,
            borderRadius: 3, padding: '1px 5px',
          }}>
            {earlyNote}
          </span>
        )}
      </div>
      {/* Scope */}
      <div style={{ ...MONO, fontSize: 8, color: 'var(--dim)', marginBottom: 8, letterSpacing: '0.06em' }}>
        {track.scope}
      </div>
      {/* Counts */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ ...MONO, fontSize: 9 }}>
          <span style={{ color: 'var(--dim)' }}>total </span>
          <span style={{ color: accent, fontWeight: 700 }}>{track.total_events}</span>
        </span>
        <span style={{ ...MONO, fontSize: 9 }}>
          <span style={{ color: 'var(--dim)' }}>complete </span>
          <span style={{ color: track.complete_events > 0 ? 'var(--green)' : 'var(--dim)', fontWeight: 700 }}>{track.complete_events}</span>
        </span>
        {track.pending_events > 0 && (
          <span style={{ ...MONO, fontSize: 9 }}>
            <span style={{ color: 'var(--dim)' }}>pending </span>
            <span style={{ color: '#f59e0b', fontWeight: 700 }}>{track.pending_events}</span>
          </span>
        )}
      </div>
      {/* Win rates */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
        {(['wr_1h', 'wr_4h', 'wr_24h'] as const).map((k, i) => {
          const labels = ['WR 1H', 'WR 4H', 'WR 24H']
          const val = track[k]
          const color = !hasWr ? 'var(--dim)'
            : val == null ? 'var(--dim)'
            : val >= 50 ? 'var(--green)' : val >= 35 ? '#f59e0b' : 'var(--red)'
          return (
            <span key={k} style={{ ...MONO, fontSize: 8 }}>
              <span style={{ color: 'var(--dim)' }}>{labels[i]} </span>
              <span style={{ color, fontWeight: 700 }}>
                {hasWr && val != null ? `${val.toFixed(1)}%` : '—'}
              </span>
            </span>
          )
        })}
      </div>
      {/* Avg score */}
      <div style={{ ...MONO, fontSize: 8 }}>
        <span style={{ color: 'var(--dim)' }}>avg score </span>
        <span style={{ color: accent, fontWeight: 700 }}>
          {track.avg_confluence_score != null ? track.avg_confluence_score.toFixed(1) : '—'}
        </span>
        {!hasWr && (
          <span style={{ color: 'var(--dim)', marginLeft: 6 }}>· need 3+ complete for WR</span>
        )}
      </div>
    </div>
  )
}

// ── Events table ──────────────────────────────────────────────────────────────

const TH: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 9, fontWeight: 700,
  color: 'var(--muted)', letterSpacing: '0.1em',
  padding: '6px 10px', textAlign: 'left',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
  whiteSpace: 'nowrap',
}

const TD: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 10, color: 'var(--muted)',
  padding: '7px 10px',
  borderBottom: '1px solid rgba(255,255,255,0.04)',
  whiteSpace: 'nowrap',
}

function EventsTable({ events }: { events: ConfluenceEvent[] }) {
  if (!events.length) {
    return (
      <div style={{
        textAlign: 'center', padding: '40px 20px',
        fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
        color: 'var(--dim)',
      }}>
        No confluences detected yet — engine scanning every 5 min
      </div>
    )
  }

  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' as any }}>
      <table style={{ width: '100%', minWidth: 700, borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={TH}>TIME</th>
            <th style={TH}>TOKEN</th>
            <th style={TH}>SIGNALS</th>
            <th style={TH}>MC</th>
            <th style={TH}>WHALE $</th>
            <th style={TH}>MEME</th>
            <th style={{ ...TH, color: C }}>CONF</th>
            <th style={TH}>1H</th>
            <th style={TH}>4H</th>
            <th style={TH}>24H</th>
            <th style={TH}>STATUS</th>
          </tr>
        </thead>
        <tbody>
          {events.map(ev => {
            const r1  = fmtRet(ev.return_1h_pct)
            const r4  = fmtRet(ev.return_4h_pct)
            const r24 = fmtRet(ev.return_24h_pct)
            const whaleStr = ev.whale_score != null
              ? ev.whale_score >= 1000 ? `$${(ev.whale_score / 1000).toFixed(0)}K`
              : `$${ev.whale_score.toFixed(0)}`
              : '—'
            // Structural rows are dimmed to signal weaker evidentiary class
            const isStructural = ev.confluence_type?.startsWith('STRUCTURAL') ?? false
            const symColor  = isStructural ? STRUCT_COLOR : (ev.confluence_type === 'TRIPLE' ? '#f59e0b' : C)
            const confColor = isStructural ? STRUCT_COLOR : C

            return (
              <tr key={ev.id} style={{
                transition: 'background 0.1s',
                background: isStructural ? 'rgba(167,139,250,0.025)' : undefined,
                opacity:    isStructural ? 0.80 : 1,
              }}>
                <td style={{ ...TD, color: 'var(--dim)', fontSize: 9 }}>{fmtAge(ev.ts_utc)}</td>
                <td style={{ ...TD, fontSize: 11 }}>
                  <div style={{ color: symColor, fontWeight: isStructural ? 500 : 700 }}>{ev.token_symbol}</div>
                  {ev.confluence_type && (
                    <div style={{
                      fontSize: 7, fontWeight: 700, letterSpacing: '0.07em', marginTop: 2,
                      color: ev.confluence_type === 'TRIPLE'         ? '#f59e0b'
                           : ev.confluence_type === 'DUAL'           ? C
                           : STRUCT_COLOR,
                    }}>
                      {ev.confluence_type}
                      {isStructural && <span style={{ color: 'var(--dim)', fontWeight: 400, marginLeft: 3 }}>· early</span>}
                    </div>
                  )}
                </td>
                <td style={{ ...TD, padding: '7px 8px' }}><SignalBadges sources={ev.sources} /></td>
                <td style={TD}>{fmtMc(ev.market_cap_usd)}</td>
                <td style={TD}>{whaleStr}</td>
                <td style={{ ...TD, color: 'var(--muted)' }}>{fmtScore(ev.memecoin_score)}</td>
                <td style={{ ...TD, color: confColor, fontWeight: isStructural ? 400 : 700 }}>{fmtScore(ev.confluence_score)}</td>
                <td style={{ ...TD, color: r1.color }}>{r1.text}</td>
                <td style={{ ...TD, color: r4.color }}>{r4.text}</td>
                <td style={{ ...TD, color: r24.color }}>{r24.text}</td>
                <td style={{ ...TD }}>
                  <span style={{
                    fontSize: 9, fontWeight: 700,
                    color: ev.outcome_status === 'COMPLETE' ? 'var(--green)' : 'var(--dim)',
                  }}>
                    {ev.outcome_status}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function ConfluencePage() {
  const statsQ = useQuery<ConfluenceStats>({
    queryKey: ['confluence-stats'],
    queryFn: () => api.get('/confluence/stats').then(r => r.data),
    refetchInterval: 30_000,
  })

  const eventsQ = useQuery<{ events: ConfluenceEvent[] }>({
    queryKey: ['confluence-events'],
    queryFn: () => api.get('/confluence/events?limit=50').then(r => r.data),
    refetchInterval: 30_000,
  })

  const stats  = statsQ.data
  const events = eventsQ.data?.events ?? []
  const total  = stats?.total_events ?? 0

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '16px 20px' }}>

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="glass-card" style={{
        border: `1px solid rgba(6,182,212,0.22)`,
        marginBottom: 16,
        padding: '18px 22px',
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 18 }}>
          <div>
            <span className="section-label" style={{ color: C }}>CONFLUENCE ENGINE</span>
            <span style={{
              marginLeft: 12, fontSize: 9,
              fontFamily: 'JetBrains Mono, monospace',
              color: 'var(--dim)', letterSpacing: '0.08em',
            }}>
              WHALE WATCH × MEMECOIN × SMART WALLETS
            </span>
          </div>
          <span style={{
            fontSize: 9, fontFamily: 'JetBrains Mono, monospace',
            color: 'var(--dim)',
          }}>
            48H WINDOW · 5MIN POLL · OBSERVE MODE
          </span>
        </div>

        <PhaseBar total={total} nextMilestone={stats?.next_milestone ?? 20} />
      </div>

      {/* ── Stats row ──────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <StatCard
          label="TOTAL CONFLUENCES"
          value={total}
          sub={`${stats?.complete_events ?? 0} complete`}
        />
        <StatCard
          label="WR @ 1H"
          value={wrCell(stats?.wr_1h ?? null, total)}
          sub={total < 3 ? 'need 3+ complete' : undefined}
        />
        <StatCard
          label="WR @ 4H"
          value={wrCell(stats?.wr_4h ?? null, total)}
        />
        <StatCard
          label="WR @ 24H"
          value={wrCell(stats?.wr_24h ?? null, total)}
        />
        <StatCard
          label="AVG CONF SCORE"
          value={stats?.avg_confluence_score != null
            ? <span>{stats.avg_confluence_score.toFixed(1)}</span>
            : <span style={{ color: 'var(--dim)' }}>—</span>
          }
          sub="0–100 composite"
        />
      </div>

      {/* ── Recent 48H pulse + Proof tracks ────────────────────────────────── */}
      {stats && (
        <>
          {/* 48H pulse strip */}
          <div style={{
            display: 'flex', gap: 16, alignItems: 'center',
            fontFamily: 'JetBrains Mono, monospace', fontSize: 9,
            color: 'var(--dim)', letterSpacing: '0.07em',
            background: 'rgba(6,182,212,0.03)',
            border: '1px solid rgba(6,182,212,0.10)',
            borderRadius: 6, padding: '6px 14px',
            marginBottom: 8,
          }}>
            <span style={{ color: 'var(--dim)', letterSpacing: '0.10em' }}>RECENT 48H</span>
            <span>
              <span style={{ color: 'var(--dim)' }}>TRUE </span>
              <strong style={{ color: (stats.recent_dual_48h ?? 0) + (stats.recent_triple_48h ?? 0) > 0 ? C : 'var(--dim)' }}>
                {(stats.recent_dual_48h ?? 0) + (stats.recent_triple_48h ?? 0)}
              </strong>
              <span style={{ color: 'var(--dim)', marginLeft: 4, fontSize: 8 }}>
                (dual {stats.recent_dual_48h ?? 0} · triple {stats.recent_triple_48h ?? 0})
              </span>
            </span>
            <span>
              <span style={{ color: 'var(--dim)' }}>STRUCTURAL </span>
              <strong style={{ color: (stats.recent_structural_48h ?? 0) > 0 ? STRUCT_COLOR : 'var(--dim)' }}>
                {stats.recent_structural_48h ?? 0}
              </strong>
            </span>
            {stats.last_ts && (
              <span style={{ marginLeft: 'auto', color: 'var(--dim)', fontSize: 8 }}>
                last event {fmtAge(stats.last_ts)}
              </span>
            )}
          </div>

          {/* Proof tracks — side-by-side */}
          {stats.validation_tracks && (
            <div style={{ marginBottom: 12 }}>
              <div style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                color: 'var(--dim)', letterSpacing: '0.10em',
                marginBottom: 6,
              }}>
                PROOF TRACKS
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <TrackPanel
                  label="TRUE CONFLUENCE"
                  track={stats.validation_tracks.true_confluence}
                  accent={C}
                />
                <TrackPanel
                  label="STRUCTURAL CONFLUENCE"
                  track={stats.validation_tracks.structural_confluence}
                  accent={STRUCT_COLOR}
                  earlyNote="WEAKER CLASS"
                />
              </div>
            </div>
          )}
        </>
      )}

      {/* ── Events table ───────────────────────────────────────────────────── */}
      <div className="glass-card" style={{ border: `1px solid rgba(6,182,212,0.14)`, padding: '16px 0 8px' }}>
        <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span className="section-label" style={{ color: C }}>EVENTS</span>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: 'var(--dim)' }}>
            {events.length} shown · auto-refresh 30s
          </span>
        </div>

        {eventsQ.isLoading ? (
          <div style={{ padding: '30px', textAlign: 'center', color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
            Loading…
          </div>
        ) : (
          <EventsTable events={events} />
        )}
      </div>

      {/* ── How it works ───────────────────────────────────────────────────── */}
      <div className="glass-card" style={{ border: '1px solid rgba(255,255,255,0.06)', marginTop: 12, padding: '14px 18px' }}>
        <div className="section-label" style={{ marginBottom: 10, color: 'var(--dim)' }}>HOW IT WORKS</div>
        <div className="grid-auto-2">
          {[
            ['Detection', 'Every 5 min: cross-reference Whale Watch (W), Memecoin Scanner (M), and Smart Wallets (S) on the same token_mint within a 48h window. DUAL = 2 sources, TRIPLE = all 3. STRUCTURAL_DUAL/TRIPLE = same sources but weaker score thresholds — valid signal, less proven.'],
            ['Confluence Score', 'Composite of whale buy size (normalised to $10K → 100), memecoin scanner score (0–100), plus smart wallet boost (+2 per SOL spent, cap +20). Higher = stronger.'],
            ['Outcome Tracking', '1h / 4h / 24h price returns tracked via DexScreener. COMPLETE once 24h return is recorded.'],
            ['Phases', 'OBSERVE (0–19): data only. ANALYZE (20–49): win rates visible. VALIDATE (50–99): Telegram alerts. INTEGRATE (100+): paper signal queue.'],
          ].map(([title, body]) => (
            <div key={title}>
              <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9, color: C, fontWeight: 700, marginBottom: 4 }}>{title}</div>
              <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--dim)', lineHeight: 1.6 }}>{body}</div>
            </div>
          ))}
        </div>
      </div>

    </div>
  )
}
