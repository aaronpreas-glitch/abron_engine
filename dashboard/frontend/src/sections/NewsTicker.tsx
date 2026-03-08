import { useQuery } from '@tanstack/react-query'
import { api } from '../api'

interface NewsItem {
  id: string
  source: string
  title: string
  link: string
  pub_ts: string
}

export function NewsTicker() {
  const { data, isLoading } = useQuery<NewsItem[]>({
    queryKey: ['news-ticker'],
    queryFn: async () => {
      const r = await api.get('/news?limit=25')
      return r.data ?? []
    },
    refetchInterval: 5 * 60_000,  // refresh every 5 min
  })

  if (isLoading || !data?.length) {
    return (
      <div style={{
        height: 28, background: 'rgba(4,6,12,0.6)',
        borderBottom: '1px solid rgba(255,255,255,0.05)',
        display: 'flex', alignItems: 'center',
        padding: '0 16px',
        color: '#2d4060', fontSize: 10,
      }}>
        loading news…
      </div>
    )
  }

  // Build the ticker text — duplicate for seamless loop
  const items = data.map(n => `${n.source.toUpperCase()}  ·  ${n.title}`)
  const text = items.join('     ◆     ')
  const full = text + '     ◆     ' + text   // doubled for seamless wrap

  return (
    <div style={{
      height: 28,
      background: 'rgba(4,6,12,0.65)',
      backdropFilter: 'blur(12px)',
      borderBottom: '1px solid rgba(255,255,255,0.05)',
      overflow: 'hidden',
      position: 'relative',
    }}>
      {/* Fade edges */}
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: 60, zIndex: 2,
        background: 'linear-gradient(to right, rgba(4,6,12,0.9), transparent)',
        pointerEvents: 'none',
      }} />
      <div style={{
        position: 'absolute', right: 0, top: 0, bottom: 0, width: 60, zIndex: 2,
        background: 'linear-gradient(to left, rgba(4,6,12,0.9), transparent)',
        pointerEvents: 'none',
      }} />

      <div style={{
        display: 'flex', alignItems: 'center', height: '100%',
        animation: `news-scroll ${items.length * 6}s linear infinite`,
        whiteSpace: 'nowrap',
        willChange: 'transform',
      }}>
        <span style={{
          color: '#4d6a8a',
          fontSize: 10,
          letterSpacing: '0.04em',
          fontFamily: 'JetBrains Mono, monospace',
          paddingLeft: 20,
        }}>
          {full}
        </span>
      </div>

      <style>{`
        @keyframes news-scroll {
          0%   { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
      `}</style>
    </div>
  )
}
