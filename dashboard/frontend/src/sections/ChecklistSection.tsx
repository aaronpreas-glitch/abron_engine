import type { ChecklistItem, BullReadiness } from '../Terminal'

interface Props {
  checks: ChecklistItem[]
  loading: boolean
  bull?: BullReadiness
  bullLoading: boolean
}

function CheckCard({ item }: { item: ChecklistItem }) {
  const val = typeof item.value === 'number'
    ? (item.id === 'avg_pnl' || item.id === 'ml_accuracy' || item.id === 'simulate_24h'
        ? `${Number(item.value).toFixed(1)}${item.id === 'simulate_24h' ? 'h' : '%'}`
        : String(item.value))
    : String(item.value)

  return (
    <div style={{
      flex: '1 1 160px',
      padding: '10px 12px',
      background: '#0e1521',
      border: `1px solid ${item.pass ? '#0d3d2e' : '#3d1a1a'}`,
      borderRadius: 4,
    }}>
      <div style={{
        fontSize: 10, color: '#4d5a6e', marginBottom: 4,
        textTransform: 'uppercase', letterSpacing: '0.06em',
      }}>
        {item.label}
      </div>
      <div style={{
        fontSize: 16, fontWeight: 700,
        color: item.pass ? '#00d48a' : '#ef4444',
      }}>
        {item.pass ? '✅' : '❌'} {val}
      </div>
      <div style={{ fontSize: 9, color: '#2d4060', marginTop: 2 }}>
        target: {String(item.target)}{item.id === 'simulate_24h' ? 'h' : item.id === 'bull_readiness' ? '' : ''}
      </div>
    </div>
  )
}

interface CompEntry {
  name: string
  pts: number
  max: number
  label: string
}

function BullBar({ comp }: { comp: CompEntry }) {
  const pct = Math.round((comp.pts / comp.max) * 100)
  const color = pct >= 80 ? '#00d48a' : pct >= 50 ? '#f59e0b' : '#ef4444'
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
        <span style={{ color: '#a0aec0', fontSize: 10, textTransform: 'uppercase' }}>{comp.name}</span>
        <span style={{ color, fontSize: 10, fontWeight: 700 }}>{comp.pts}/{comp.max}</span>
      </div>
      <div className="mini-bar-track">
        <div
          className="mini-bar-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <div style={{ color: '#4d5a6e', fontSize: 9, marginTop: 1 }}>{comp.label}</div>
    </div>
  )
}

export function ChecklistSection({ checks, loading, bull, bullLoading }: Props) {
  const passCount = checks.filter(c => c.pass).length

  // Convert components dict to sorted array
  const compList: CompEntry[] = bull?.components
    ? Object.entries(bull.components).map(([name, c]) => ({ name, ...c }))
    : []

  return (
    <div>
      <div className="section-label">GO / NO-GO</div>

      {loading ? (
        <div style={{ color: '#4d5a6e', fontSize: 11, padding: '8px 0' }}>Loading checklist...</div>
      ) : checks.length === 0 ? (
        <div style={{ color: '#4d5a6e', fontSize: 11, padding: '8px 0' }}>No checklist data</div>
      ) : (
        <>
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16,
          }}>
            {checks.map(item => (
              <CheckCard key={item.id} item={item} />
            ))}
          </div>
          <div style={{ color: '#4d5a6e', fontSize: 10, marginBottom: 12 }}>
            {passCount}/{checks.length} checks passing
          </div>
        </>
      )}

      {/* Bull Readiness */}
      <div style={{
        background: '#0e1521', border: '1px solid #1a2332', borderRadius: 4, padding: '12px 16px',
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 12 }}>
          <span style={{ color: '#4d5a6e', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Bull Readiness
          </span>
          {bull && (
            <>
              <span style={{
                fontSize: 22, fontWeight: 700,
                color: bull.score >= 75 ? '#00d48a' : bull.score >= 60 ? '#f59e0b' : '#ef4444',
              }}>
                {bull.score}
              </span>
              <span style={{ color: '#4d5a6e', fontSize: 10 }}>/100</span>
              <span style={{ color: '#a0aec0', fontSize: 11 }}>{bull.label}</span>
            </>
          )}
          {bullLoading && !bull && <span style={{ color: '#4d5a6e', fontSize: 10 }}>Loading...</span>}
        </div>
        {compList.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '8px 24px' }}>
            {compList.map(c => (
              <BullBar key={c.name} comp={c} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
