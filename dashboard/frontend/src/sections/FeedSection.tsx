import type { MemoryEntry } from '../Terminal'

interface Props { entries: MemoryEntry[]; loading: boolean }

const MONO = { fontFamily: 'JetBrains Mono, monospace' }

function entryColor(e: MemoryEntry): string {
  const msg = e.message
  if (msg.includes('ERROR') || msg.includes('STALLED') || msg.includes('ALERT')) return '#ef4444'
  if (msg.includes('WARNING') || msg.includes('SIGNAL_BLOCK') || msg.includes('STALE')) return '#f59e0b'
  if (msg.includes('WINNER_TRAIL') || msg.includes('TRAIL_STOP') || msg.includes('LIVE')) return '#00d48a'
  if (msg.includes('GATE_ANALYSIS') || msg.includes('GATE_FLOW')) return '#7c9fd4'
  switch (e.agent.toUpperCase()) {
    case 'MONITORING':      return '#a0aec0'
    case 'RESEARCH':        return '#9f7aea'
    case 'TRADING':         return '#00d48a'
    case 'WATCHDOG':        return '#f59e0b'
    case 'OPTIMIZER':       return '#7c9fd4'
    case 'HEALTH_WATCHDOG': return '#f59e0b'
    case 'DATA_INTEGRITY':  return '#7c9fd4'
    case 'ALERT':           return '#ef4444'
    default:                return '#5a7a9a'
  }
}

function fmtTs(ts: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(ts.replace(' ', 'T') + 'Z'))
  } catch { return ts.slice(-5) }
}

export function FeedSection({ entries, loading }: Props) {
  const visible = entries.slice(0, 40)

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <span className="section-label">AGENT MEMORY FEED</span>
        <span style={{ color: 'var(--dim)', fontSize: 9, ...MONO }}>
          {visible.length} entries · ET · 15s refresh
        </span>
      </div>

      {loading ? (
        <div style={{ color: 'var(--dim)', fontSize: 10, ...MONO }}>loading feed…</div>
      ) : entries.length === 0 ? (
        <div style={{ color: 'var(--dim)', fontSize: 10, ...MONO }}>no memory entries yet</div>
      ) : (
        <div style={{ maxHeight: 400, overflowY: 'auto' }}>
          {visible.map((e, i) => (
            <div key={i} style={{
              display: 'grid',
              gridTemplateColumns: '44px 80px 1fr',
              gap: 10,
              padding: '5px 0',
              borderBottom: i < visible.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
              alignItems: 'baseline',
            }}>
              <span style={{ color: 'var(--dim)', fontSize: 10, ...MONO, flexShrink: 0 }}>{fmtTs(e.ts)}</span>
              <span style={{
                color: 'var(--muted)', fontSize: 9, letterSpacing: '0.04em',
                textTransform: 'uppercase', ...MONO,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {e.agent}
              </span>
              <span style={{ color: entryColor(e), fontSize: 10, lineHeight: 1.5, ...MONO, wordBreak: 'break-word' }}>
                {e.message}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
