interface PriceItem {
  coin: string
  price: number
  chg24: number | null
}

interface Props {
  prices: PriceItem[]
  loading: boolean
}

function fmt(price: number, coin: string): string {
  if (!price) return '—'
  if (coin === 'BTC') return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (price >= 1000) return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (price >= 10) return `$${price.toFixed(2)}`
  return `$${price.toFixed(3)}`
}

export function PriceStrip({ prices, loading }: Props) {
  if (loading && prices.length === 0) return null

  return (
    <div style={{
      display: 'flex', alignItems: 'center',
      padding: '0 20px',
      background: 'rgba(4,6,12,0.8)',
      backdropFilter: 'blur(16px)',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      overflowX: 'auto',
    }}>
      {prices.map((item, i) => {
        const up = (item.chg24 ?? 0) > 0
        const dn = (item.chg24 ?? 0) < 0
        const chgColor = up ? '#00d48a' : dn ? '#ef4444' : '#4d5a6e'
        return (
          <div key={item.coin} style={{
            display: 'flex', alignItems: 'center', gap: 7,
            padding: '8px 18px',
            borderRight: i < prices.length - 1 ? '1px solid rgba(255,255,255,0.05)' : 'none',
            flexShrink: 0,
          }}>
            <span style={{ color: '#4a6280', fontSize: 9, fontWeight: 700, letterSpacing: '0.1em', fontFamily: 'JetBrains Mono, monospace' }}>
              {item.coin}
            </span>
            <span style={{ color: '#c0cfe0', fontSize: 11, fontWeight: 600, fontFamily: 'JetBrains Mono, monospace' }}>
              {fmt(item.price, item.coin)}
            </span>
            <span style={{ color: chgColor, fontSize: 10, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace' }}>
              {item.chg24 != null ? `${up ? '+' : ''}${item.chg24.toFixed(2)}%` : '—'}
            </span>
          </div>
        )
      })}
    </div>
  )
}
