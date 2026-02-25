import { useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import type { WatchCard } from '../../types'
import { WatchCard as WC } from './WatchCard'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'

export function WatchlistPanel() {
  const prevStatusRef = useRef<Record<string, string>>({})
  const prevStatusSnap = useRef<Record<string, string>>({})

  const { data, isLoading, dataUpdatedAt } = useQuery<WatchCard[]>({
    queryKey: ['watchlist'],
    queryFn: () => api.get('/watchlist').then(r => r.data),
    refetchInterval: 60000,
    staleTime: 30000,
  })

  const updatedAt = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString('en-US', { hour12: false }) : null

  const statusOrder: Record<string, number> = {
    Momentum: 0, Reclaim: 1, Volatile: 2, Range: 3, Breakdown: 4, Illiquid: 5, NoData: 6,
  }
  const sorted = [...(data || [])].sort((a, b) =>
    (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9)
  )

  // Status change detection — compare new data to previous fetch
  const changedSymbols = new Set<string>()
  if (data && Object.keys(prevStatusRef.current).length > 0) {
    data.forEach(card => {
      const prev = prevStatusRef.current[card.symbol]
      if (prev && prev !== card.status) {
        changedSymbols.add(card.symbol)
        prevStatusSnap.current[card.symbol] = prev
      }
    })
  }
  // Update stored statuses after diff
  if (data) {
    data.forEach(card => { prevStatusRef.current[card.symbol] = card.status })
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>👁 WATCHLIST</h2>
        {updatedAt && <span style={{ color: 'var(--muted)', fontSize: 11 }}>updated {updatedAt}</span>}
        {changedSymbols.size > 0 && (
          <span style={{
            fontSize: 9, padding: '2px 7px', borderRadius: 3, fontWeight: 700,
            background: 'rgba(240,165,0,0.12)', color: 'var(--amber)',
            border: '1px solid rgba(240,165,0,0.25)', letterSpacing: '0.08em',
          }}>
            ⚡ {changedSymbols.size} STATUS CHANGE{changedSymbols.size > 1 ? 'S' : ''}
          </span>
        )}
      </div>

      {isLoading && <LoadingSpinner />}
      {!isLoading && sorted.length === 0 && <EmptyState message="No watchlist tokens configured." />}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
        {sorted.map(card => (
          <WC
            key={card.symbol}
            card={card}
            isChanged={changedSymbols.has(card.symbol)}
            prevStatus={prevStatusSnap.current[card.symbol]}
          />
        ))}
      </div>
    </div>
  )
}
