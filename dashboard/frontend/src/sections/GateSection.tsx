import type { MemoryEntry } from '../Terminal'

interface Props {
  memoryEntries: MemoryEntry[]
  loading: boolean
}

interface GateStats {
  acceptancePct: string
  executed: number
  total: number
  topBlocks: Array<{ reason: string; count: number }>
  recentSignals: Array<{ symbol: string; reason: string; detail: string; ts: string }>
}

/**
 * Parse GATE_FLOW / GATE_ANALYSIS entries from memory feed.
 *
 * Expected formats from optimizer/data_integrity agents:
 *   GATE_ANALYSIS: 7/659 executed (1.1%) | blocks: LOW_PRED_RET=321 EV_FILTER=197 SWING_ONLY=134
 *   GATE_FLOW | acceptance=1.23% | executed=7/659 | top_blocks: LOW_PRED_RET:321,EV_FILTER:197
 *   SIGNAL_BLOCK: 28 signals rejected in 30m, 0 executed
 */
function parseGateStats(entries: MemoryEntry[]): GateStats {
  let acceptancePct = 'N/A'
  let executed = 0
  let total = 0
  const blockCounts: Record<string, number> = {}

  // Recent signals from MONITORING agent entries that mention skip reasons
  const recentSignals: Array<{ symbol: string; reason: string; detail: string; ts: string }> = []

  for (const e of entries) {
    const msg = e.message

    // GATE_ANALYSIS: 7/659 executed (1.1%) | blocks: LOW_PRED_RET=321 EV_FILTER=197
    if (msg.includes('GATE_ANALYSIS')) {
      const execMatch = msg.match(/(\d+)\/(\d+)\s+executed/)
      const pctMatch = msg.match(/\((\d+\.?\d*%)\)/)
      if (execMatch) {
        executed = parseInt(execMatch[1])
        total = parseInt(execMatch[2])
      }
      if (pctMatch) acceptancePct = pctMatch[1]

      // blocks: LOW_PRED_RET=321 EV_FILTER=197
      const blocksSection = msg.split('blocks:')[1]
      if (blocksSection) {
        const blockMatches = blocksSection.matchAll(/([A-Z_]+)=(\d+)/g)
        for (const m of blockMatches) {
          const prev = blockCounts[m[1]] ?? 0
          if (parseInt(m[2]) > prev) blockCounts[m[1]] = parseInt(m[2])
        }
      }
      break // use the most recent GATE_ANALYSIS entry
    }

    // GATE_FLOW | acceptance=1.23% | executed=7/659 | top_blocks: LOW_PRED_RET:321,...
    if (msg.includes('GATE_FLOW') && !msg.includes('GATE_ANALYSIS')) {
      const pctMatch = msg.match(/acceptance=(\d+\.?\d*%)/)
      const execMatch = msg.match(/executed=(\d+)\/(\d+)/)
      if (pctMatch) acceptancePct = pctMatch[1]
      if (execMatch) { executed = parseInt(execMatch[1]); total = parseInt(execMatch[2]) }

      const topSection = msg.split('top_blocks:')[1]
      if (topSection) {
        for (const block of topSection.split(',')) {
          const parts = block.trim().split(':')
          if (parts.length === 2) {
            const key = parts[0].trim()
            const val = parseInt(parts[1])
            if (!isNaN(val) && key) {
              const prev = blockCounts[key] ?? 0
              if (val > prev) blockCounts[key] = val
            }
          }
        }
      }
      break
    }
  }

  // Parse recent signals from skipped signal lines in memory
  // Format from monitoring: "SKIP ATOM LOW_PRED_RET ml_wp=0.44 pred_ret=-2.77%"
  // or from health_watchdog SIGNAL_BLOCK entries
  const signalEntries = entries
    .filter(e =>
      (e.agent.toUpperCase() === 'MONITORING' || e.agent.toUpperCase() === 'SCALP_SCAN' || e.agent.toUpperCase() === 'TRADING') &&
      (e.message.includes('SKIP') || e.message.includes('LOW_PRED_RET') || e.message.includes('EV_FILTER'))
    )
    .slice(0, 5)

  for (const e of signalEntries) {
    const msg = e.message
    // Try to extract symbol + reason
    const symbolMatch = msg.match(/\b([A-Z]{2,8})\b/)
    const reasonMatch = msg.match(/\b(LOW_PRED_RET|EV_FILTER|SWING_ONLY|ML_GATE|CIRCUIT_BREAKER|LOW_WIN_PROB)\b/)
    const mlMatch = msg.match(/ml_wp=([\d.]+)/)
    const predMatch = msg.match(/pred_ret=([-\d.]+)%?/)

    if (symbolMatch && reasonMatch) {
      const detail = [
        mlMatch ? `ml_wp=${mlMatch[1]}` : '',
        predMatch ? `pred_ret=${predMatch[1]}%` : '',
      ].filter(Boolean).join('  ')

      recentSignals.push({
        symbol: symbolMatch[1],
        reason: reasonMatch[1],
        detail,
        ts: e.ts,
      })
    }
  }

  const topBlocks = Object.entries(blockCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([reason, count]) => ({ reason, count }))

  return { acceptancePct, executed, total, topBlocks, recentSignals }
}

function formatTs(ts: string): string {
  // ts: "2026-03-01 00:43:00" (UTC) → convert to ET
  try {
    const d = new Date(ts.replace(' ', 'T') + 'Z')
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(d)
  } catch {
    return ts.slice(-5)
  }
}

export function GateSection({ memoryEntries, loading }: Props) {
  if (loading) return (
    <div>
      <div className="section-label">GATE INTELLIGENCE</div>
      <div style={{ color: '#4d5a6e', fontSize: 11 }}>Loading gate data...</div>
    </div>
  )

  const stats = parseGateStats(memoryEntries)

  // Find last SIGNAL_BLOCK entry from health_watchdog
  const signalBlockEntry = memoryEntries.find(e =>
    e.agent.toUpperCase() === 'HEALTH_WATCHDOG' && e.message.includes('SIGNAL_BLOCK')
  )

  return (
    <div>
      <div className="section-label">GATE INTELLIGENCE</div>

      {/* Stats row */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, marginBottom: 12, alignItems: 'baseline' }}>
        <div>
          <span style={{ color: '#4d5a6e', fontSize: 10 }}>Acceptance  </span>
          <span style={{
            fontSize: 16, fontWeight: 700,
            color: parseFloat(stats.acceptancePct) < 2 ? '#ef4444'
              : parseFloat(stats.acceptancePct) < 10 ? '#f59e0b'
              : '#00d48a',
          }}>
            {stats.acceptancePct}
          </span>
          {stats.total > 0 && (
            <span style={{ color: '#4d5a6e', fontSize: 10 }}>
              {' '}({stats.executed}/{stats.total} executed)
            </span>
          )}
        </div>
        {signalBlockEntry && (
          <div style={{ fontSize: 10, color: '#f59e0b' }}>
            ⚠ {signalBlockEntry.message}
          </div>
        )}
      </div>

      {/* Top blocks */}
      {stats.topBlocks.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          <span style={{ color: '#4d5a6e', fontSize: 10, alignSelf: 'center' }}>Top blocks:</span>
          {stats.topBlocks.map(b => (
            <div key={b.reason} style={{
              background: '#1a0e0e', border: '1px solid #3d1a1a',
              borderRadius: 3, padding: '2px 8px', fontSize: 10, color: '#ef4444',
            }}>
              {b.reason} ({b.count})
            </div>
          ))}
        </div>
      )}

      {/* Recent signals */}
      {stats.recentSignals.length > 0 ? (
        <div>
          <div style={{ color: '#4d5a6e', fontSize: 9, textTransform: 'uppercase', marginBottom: 4 }}>
            Recent blocked signals
          </div>
          {stats.recentSignals.map((s, i) => (
            <div key={i} style={{
              display: 'flex', gap: 12, padding: '4px 0',
              borderBottom: '1px solid #0e1521', alignItems: 'center',
            }}>
              <span style={{ color: '#a0aec0', fontWeight: 700, width: 50, fontSize: 11 }}>{s.symbol}</span>
              <span style={{ color: '#ef4444', fontSize: 10, width: 140 }}>{s.reason}</span>
              <span style={{ color: '#4d5a6e', fontSize: 10, flex: 1 }}>{s.detail}</span>
              <span style={{ color: '#2d4060', fontSize: 9 }}>{formatTs(s.ts)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: '#4d5a6e', fontSize: 10 }}>
          {memoryEntries.length === 0
            ? 'No gate data — memory feed empty'
            : 'No recent blocked signals in memory feed. Check Feed section below.'}
        </div>
      )}
    </div>
  )
}
