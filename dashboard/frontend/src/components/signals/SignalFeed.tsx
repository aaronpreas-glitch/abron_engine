import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { signalSocket } from '../../ws'
import type { Signal } from '../../types'
import { SignalCard } from './SignalCard'
import { SnapshotBar } from './SnapshotBar'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'

const DECISION_OPTIONS = [
  { label: 'All', value: null },
  { label: 'Alert', value: 'ALERT' },
  { label: 'Dry Run', value: 'DRY_RUN' },
  { label: 'Watch', value: 'WATCHLIST' },
  { label: 'Scan', value: 'SCAN_BEST' },
]

const CONVICTION_OPTIONS = [
  { label: 'Any', value: null },
  { label: 'A', value: 3 },
  { label: 'B', value: 2 },
  { label: 'C', value: 1 },
]

export function SignalFeed() {
  const [live, setLive] = useState<Signal[]>([])
  const [wsConnected, setWsConnected] = useState(false)

  // Filter state
  const [filterDecision, setFilterDecision] = useState<string | null>(null)
  const [filterMinScore, setFilterMinScore] = useState<number>(0)
  const [filterConviction, setFilterConviction] = useState<number | null>(null)
  const [filterHours, setFilterHours] = useState<number | null>(null)
  const [showFilters, setShowFilters] = useState(false)

  const { data: initial, isLoading } = useQuery<Signal[]>({
    queryKey: ['signals-recent'],
    queryFn: () => api.get('/signals/recent?limit=100').then(r => r.data),
  })

  useEffect(() => {
    signalSocket.connect()
    const unsub = signalSocket.subscribe((msg: unknown) => {
      const m = msg as { type: string; data?: Signal }
      if (m.type === 'connected') setWsConnected(true)
      if (m.type === 'signal' && m.data) {
        setLive(prev => [m.data!, ...prev].slice(0, 100))
      }
    })
    return () => { unsub(); signalSocket.disconnect() }
  }, [])

  const allSignals = live.length > 0
    ? [...live.filter(l => !(initial || []).some(i => i.id === l.id)), ...(initial || [])]
    : (initial || [])

  // Apply filters
  const filtered = useMemo(() => {
    let sigs = allSignals
    if (filterDecision) {
      sigs = sigs.filter(s => s.decision.includes(filterDecision))
    }
    if (filterMinScore > 0) {
      sigs = sigs.filter(s => s.score_total != null && s.score_total >= filterMinScore)
    }
    if (filterConviction != null) {
      sigs = sigs.filter(s => s.conviction === filterConviction)
    }
    if (filterHours != null) {
      const cutoff = Date.now() - filterHours * 3_600_000
      sigs = sigs.filter(s => new Date(s.ts_utc + 'Z').getTime() >= cutoff)
    }
    return sigs
  }, [allSignals, filterDecision, filterMinScore, filterConviction, filterHours])

  const hasFilters = filterDecision != null || filterMinScore > 0 || filterConviction != null || filterHours != null

  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '3px 10px', fontSize: 11, cursor: 'pointer',
    background: active ? 'var(--surface2)' : 'transparent',
    border: `1px solid ${active ? 'var(--green)' : 'var(--border)'}`,
    color: active ? 'var(--green)' : 'var(--muted)',
    borderRadius: 3, fontFamily: 'monospace',
  })

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>⚡ SIGNAL FEED</h2>
        <div style={{
          width: 8, height: 8, borderRadius: '50%',
          background: wsConnected ? 'var(--green)' : 'var(--red)',
          boxShadow: wsConnected ? '0 0 6px var(--green)' : 'none',
        }} />
        <span style={{ color: 'var(--muted)', fontSize: 11 }}>
          {wsConnected ? 'LIVE' : 'connecting...'}
        </span>
        <span style={{ color: 'var(--muted)', fontSize: 11 }}>
          {hasFilters ? `${filtered.length} / ${allSignals.length}` : `${allSignals.length}`} signals
        </span>

        <button
          onClick={() => setShowFilters(f => !f)}
          style={{
            marginLeft: 'auto',
            padding: '3px 10px', fontSize: 11, cursor: 'pointer',
            background: (showFilters || hasFilters) ? 'var(--surface2)' : 'transparent',
            border: `1px solid ${hasFilters ? 'var(--amber)' : showFilters ? 'var(--green)' : 'var(--border)'}`,
            color: hasFilters ? 'var(--amber)' : showFilters ? 'var(--green)' : 'var(--muted)',
            borderRadius: 3, fontFamily: 'monospace',
          }}
        >
          ⚙ {hasFilters ? 'Filtered' : 'Filter'}
        </button>

        {hasFilters && (
          <button
            onClick={() => {
              setFilterDecision(null)
              setFilterMinScore(0)
              setFilterConviction(null)
              setFilterHours(null)
            }}
            style={{ ...btnStyle(false), color: 'var(--red)', borderColor: 'var(--red)44' }}
          >
            ✕ Clear
          </button>
        )}
      </div>

      {/* Filter controls */}
      {showFilters && (
        <div className="card" style={{ marginBottom: 12, padding: '12px 14px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start' }}>

            {/* Decision type */}
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 6, fontWeight: 700 }}>DECISION TYPE</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {DECISION_OPTIONS.map(opt => (
                  <button
                    key={opt.label}
                    style={btnStyle(filterDecision === opt.value)}
                    onClick={() => setFilterDecision(opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Min score */}
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 6, fontWeight: 700 }}>
                MIN SCORE: <span style={{ color: filterMinScore > 0 ? 'var(--amber)' : 'var(--muted)' }}>
                  {filterMinScore > 0 ? `${filterMinScore}+` : 'off'}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 4 }}>
                {[0, 60, 70, 75, 80, 85].map(s => (
                  <button
                    key={s}
                    style={btnStyle(filterMinScore === s)}
                    onClick={() => setFilterMinScore(s)}
                  >
                    {s === 0 ? 'All' : `${s}+`}
                  </button>
                ))}
              </div>
            </div>

            {/* Conviction */}
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 6, fontWeight: 700 }}>CONVICTION</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {CONVICTION_OPTIONS.map(opt => (
                  <button
                    key={opt.label}
                    style={btnStyle(filterConviction === opt.value)}
                    onClick={() => setFilterConviction(opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Time window */}
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 6, fontWeight: 700 }}>TIME WINDOW</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {[
                  { label: 'All', value: null },
                  { label: '1h', value: 1 },
                  { label: '4h', value: 4 },
                  { label: '12h', value: 12 },
                  { label: '24h', value: 24 },
                ].map(opt => (
                  <button
                    key={opt.label}
                    style={btnStyle(filterHours === opt.value)}
                    onClick={() => setFilterHours(opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Snapshot overview bar */}
      <SnapshotBar />

      {isLoading && <LoadingSpinner />}
      {!isLoading && filtered.length === 0 && (
        <EmptyState message={hasFilters ? 'No signals match your filters.' : 'No signals yet.'} />
      )}
      {filtered.map(sig => <SignalCard key={sig.id} sig={sig} />)}
    </div>
  )
}
