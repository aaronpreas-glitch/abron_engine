import type { Agent, MemoryEntry } from '../Terminal'

interface Props {
  agents: Agent[]
  memoryEntries: MemoryEntry[]
  loading: boolean
}

function healthColor(health: Agent['health']): string {
  switch (health) {
    case 'alive':   return '#00d48a'
    case 'slow':    return '#f59e0b'
    case 'stalled': return '#ef4444'
    default:        return '#4d5a6e'
  }
}

function formatAgo(secs: number | null): string {
  if (secs === null) return 'never'
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`
  return `${Math.round(secs / 3600)}h ago`
}

function lastFinding(agentName: string, entries: MemoryEntry[]): string {
  // Match case-insensitively since MEMORY.md stores agent names in uppercase
  const upper = agentName.toUpperCase().replace(/_/g, ' ')
  const found = entries.find(e =>
    e.agent.toUpperCase().replace(/-/g, '_').replace(/ /g, '_') === agentName.toUpperCase()
    || e.agent.toUpperCase().replace(/_/g, ' ') === upper
  )
  if (!found) return '—'
  // Trim to 80 chars
  return found.message.length > 80 ? found.message.slice(0, 77) + '...' : found.message
}

export function AgentsSection({ agents, memoryEntries, loading }: Props) {
  return (
    <div>
      <div className="section-label">AGENTS</div>

      {loading ? (
        <div style={{ color: '#4d5a6e', fontSize: 11 }}>Loading agents...</div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
          gap: 8,
        }}>
          {agents.map(agent => {
            const finding = lastFinding(agent.name, memoryEntries)
            const color = healthColor(agent.health)
            return (
              <div
                key={agent.name}
                style={{
                  background: '#0e1521',
                  border: `1px solid ${agent.health === 'alive' ? '#0d3d2e' : agent.health === 'stalled' ? '#3d1a1a' : '#2d2d0a'}`,
                  borderRadius: 4,
                  padding: '8px 12px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: color, display: 'inline-block', flexShrink: 0,
                  }} />
                  <span style={{ color: '#c0cfe0', fontWeight: 700, fontSize: 11, flex: 1 }}>
                    {agent.name}
                  </span>
                  <span style={{ color, fontSize: 10 }}>
                    {agent.health.toUpperCase()}
                  </span>
                  <span style={{ color: '#4d5a6e', fontSize: 10 }}>
                    {formatAgo(agent.last_beat_ago_s)}
                  </span>
                </div>
                <div style={{
                  color: '#5a7a9a',
                  fontSize: 10,
                  lineHeight: 1.4,
                  borderLeft: '2px solid #1a2332',
                  paddingLeft: 8,
                  wordBreak: 'break-word',
                }}>
                  {finding}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
