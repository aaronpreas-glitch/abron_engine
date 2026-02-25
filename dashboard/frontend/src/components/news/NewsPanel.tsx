import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { LoadingSpinner } from '../shared/LoadingSpinner'
import { EmptyState } from '../shared/EmptyState'

interface NewsItem {
  id: string
  source: string
  title: string
  link: string
  summary: string
  pub_ts: string | null
  tag: 'SOL' | 'MARKET' | 'CRYPTO'
}

// ── helpers ──────────────────────────────────────────────────────────────────

function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1)  return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const TAG_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  SOL:    { bg: '#39d35322', color: '#39d353', label: '◈ SOL'    },
  MARKET: { bg: '#f0a50022', color: '#f0a500', label: '◉ MARKET' },
  CRYPTO: { bg: '#58a6ff22', color: '#58a6ff', label: '◎ CRYPTO' },
}

const SOURCE_ABBR: Record<string, string> = {
  'CoinDesk':      'CD',
  'CoinTelegraph': 'CT',
  'Decrypt':       'DC',
  'The Block':     'TB',
}

// ── subcomponents ─────────────────────────────────────────────────────────────

function TagPill({ tag }: { tag: string }) {
  const s = TAG_STYLE[tag] || TAG_STYLE.CRYPTO
  return (
    <span style={{
      display: 'inline-block',
      padding: '1px 7px',
      borderRadius: 3,
      fontSize: 9,
      fontWeight: 700,
      letterSpacing: '0.06em',
      background: s.bg,
      color: s.color,
      border: `1px solid ${s.color}44`,
    }}>
      {s.label}
    </span>
  )
}

function SourceBadge({ source }: { source: string }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '1px 6px',
      borderRadius: 3,
      fontSize: 9,
      fontWeight: 700,
      letterSpacing: '0.04em',
      background: 'var(--surface2)',
      color: 'var(--muted)',
      border: '1px solid var(--border)',
    }}>
      {SOURCE_ABBR[source] || source.slice(0, 2).toUpperCase()}
    </span>
  )
}

function NewsCard({ item, isNew }: { item: NewsItem; isNew: boolean }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      style={{
        padding: '12px 14px',
        borderBottom: '1px solid var(--border)',
        background: isNew ? '#39d35308' : 'transparent',
        transition: 'background 0.3s',
        cursor: 'pointer',
      }}
      onClick={() => setExpanded(e => !e)}
    >
      {/* Top row: badges + time */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 7 }}>
        <TagPill tag={item.tag} />
        <SourceBadge source={item.source} />
        <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 10 }}>
          {timeAgo(item.pub_ts)}
        </span>
      </div>

      {/* Title */}
      <a
        href={item.link}
        target="_blank"
        rel="noopener noreferrer"
        onClick={e => e.stopPropagation()}
        style={{
          color: 'var(--text)',
          textDecoration: 'none',
          fontSize: 12,
          fontWeight: 600,
          lineHeight: 1.45,
          display: 'block',
          letterSpacing: '0.01em',
        }}
        onMouseEnter={e => (e.currentTarget.style.color = 'var(--green)')}
        onMouseLeave={e => (e.currentTarget.style.color = 'var(--text)')}
      >
        {item.title}
      </a>

      {/* Summary — shown when expanded */}
      {expanded && item.summary && (
        <p style={{
          margin: '8px 0 0',
          color: 'var(--muted)',
          fontSize: 11,
          lineHeight: 1.55,
          borderLeft: '2px solid var(--border)',
          paddingLeft: 10,
        }}>
          {item.summary}
        </p>
      )}
    </div>
  )
}

// ── main panel ────────────────────────────────────────────────────────────────

const TABS = ['ALL', 'SOL', 'MARKET', 'CRYPTO'] as const
type Tab = typeof TABS[number]

export function NewsPanel() {
  const [tab, setTab] = useState<Tab>('ALL')
  const [seenIds, setSeenIds] = useState<Set<string>>(new Set())

  const { data: items, isLoading, dataUpdatedAt, refetch, isFetching } = useQuery<NewsItem[]>({
    queryKey: ['news', tab],
    queryFn: () => api.get(`/news?limit=60${tab !== 'ALL' ? `&tag=${tab}` : ''}`).then(r => r.data),
    refetchInterval: 5 * 60 * 1000,   // 5 min — matches server cache TTL
    staleTime:       4 * 60 * 1000,
  })

  // Track which IDs were present on last render so we can highlight new ones
  const currentIds = new Set((items || []).map(i => i.id))
  const newIds = new Set([...currentIds].filter(id => !seenIds.has(id)))
  if (newIds.size > 0 && seenIds.size > 0) {
    // Will update seenIds after render via effect — for now just highlight
  }

  const btnStyle = (active: boolean) => ({
    padding: '3px 11px',
    fontSize: 10,
    cursor: 'pointer',
    background: active ? 'var(--surface2)' : 'transparent',
    border: `1px solid ${active ? 'var(--green)' : 'var(--border)'}`,
    color: active ? 'var(--green)' : 'var(--muted)',
    borderRadius: 3,
    letterSpacing: '0.05em',
    fontWeight: active ? 700 : 400,
  })

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em' }}>📰 NEWS FEED</h2>

        {/* Tab filters */}
        <div style={{ display: 'flex', gap: 5 }}>
          {TABS.map(t => (
            <button
              key={t}
              style={btnStyle(tab === t)}
              onClick={() => {
                setSeenIds(currentIds)
                setTab(t)
              }}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Right side: update time + refresh */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
          {dataUpdatedAt > 0 && (
            <span style={{ color: 'var(--muted)', fontSize: 10 }}>
              updated {timeAgo(new Date(dataUpdatedAt).toISOString())}
            </span>
          )}
          <button
            onClick={() => { setSeenIds(currentIds); refetch() }}
            style={{
              padding: '3px 10px',
              fontSize: 10,
              cursor: isFetching ? 'default' : 'pointer',
              background: 'transparent',
              border: '1px solid var(--border)',
              color: isFetching ? 'var(--muted)' : 'var(--text)',
              borderRadius: 3,
              opacity: isFetching ? 0.5 : 1,
            }}
            disabled={isFetching}
          >
            {isFetching ? '↻ loading' : '↻ refresh'}
          </button>
        </div>
      </div>

      {/* Source legend */}
      <div style={{
        display: 'flex', gap: 12, marginBottom: 12,
        color: 'var(--muted)', fontSize: 10,
      }}>
        {['CoinDesk → CD', 'CoinTelegraph → CT', 'Decrypt → DC', 'The Block → TB'].map(s => (
          <span key={s}>{s}</span>
        ))}
      </div>

      {/* Content */}
      {isLoading ? <LoadingSpinner /> : !items || items.length === 0 ? (
        <EmptyState message="No news available. Check back shortly." />
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {/* Count bar */}
          <div style={{
            padding: '8px 14px',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: 'var(--surface2)',
          }}>
            <span style={{ color: 'var(--muted)', fontSize: 10 }}>
              {items.length} articles
            </span>
            {newIds.size > 0 && (
              <span style={{
                padding: '1px 7px',
                borderRadius: 3,
                background: '#39d35322',
                color: '#39d353',
                fontSize: 10,
                fontWeight: 700,
                border: '1px solid #39d35344',
              }}>
                +{newIds.size} new
              </span>
            )}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 12 }}>
              {(['SOL', 'MARKET', 'CRYPTO'] as const).map(t => {
                const count = items.filter(i => i.tag === t).length
                const s = TAG_STYLE[t]
                return (
                  <span key={t} style={{ fontSize: 10, color: s.color }}>
                    {s.label} <span style={{ color: 'var(--muted)' }}>{count}</span>
                  </span>
                )
              })}
            </div>
          </div>

          {/* Articles list */}
          {items.map((item, idx) => (
            <NewsCard
              key={item.id}
              item={item}
              isNew={idx < 3 && newIds.has(item.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
