export interface JupiterPosition {
  market: string; symbol: string; side: 'LONG' | 'SHORT'
  entry_price: number; mark_price: number; size_usd: number
  collateral_usd: number; value_usd: number; pnl_usd: number
  pnl_pct: number; leverage: number; liq_price: number
  liq_near: boolean; total_fees_usd: number; position_pubkey: string
}

interface Props {
  wallet: string | null; positions: JupiterPosition[]
  solBalance: number | null; solPrice: number | null
  loading: boolean; error: string | null
}

const MONO = { fontFamily: 'JetBrains Mono, monospace' }

function pc(pnl: number) { return pnl > 0 ? '#00d48a' : pnl < 0 ? '#ef4444' : '#a0aec0' }
function fp(p: number) {
  if (!p) return '—'
  return p >= 1000
    ? `$${p.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
    : `$${p.toFixed(2)}`
}
function short(w: string) { return w?.length > 12 ? `${w.slice(0, 6)}…${w.slice(-4)}` : w }

export function WalletSection({ wallet, positions, solBalance, solPrice, loading, error }: Props) {
  const totalPnl = positions.reduce((s, p) => s + p.pnl_usd, 0)
  const totalVal = positions.reduce((s, p) => s + p.value_usd, 0)
  const solVal   = solBalance !== null && solPrice ? solBalance * solPrice : null

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="section-label">MY WALLET</span>
          {wallet && (
            <span style={{
              background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)',
              borderRadius: 20, padding: '2px 8px', fontSize: 9, color: 'var(--dim)', ...MONO,
            }}>
              {short(wallet)}
            </span>
          )}
        </div>
        <span style={{ color: 'var(--dim)', fontSize: 9, ...MONO }}>live · 30s</span>
      </div>

      {/* Stats bar */}
      <div className="kpi-strip">
        {/* SOL */}
        {solVal !== null && (
          <div className="stat-tile">
            <div className="stat-label">SOL SPOT</div>
            <div className="stat-value" style={{ fontSize: 16, color: 'var(--text2)' }}>
              {solBalance!.toFixed(3)}
              <span style={{ fontSize: 11, color: 'var(--dim)', marginLeft: 6, fontWeight: 400 }}>SOL</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO }}>${solVal.toLocaleString('en-US', { maximumFractionDigits: 2 })}</div>
          </div>
        )}
        {/* Perp PnL */}
        {positions.length > 0 && (
          <div className="stat-tile">
            <div className="stat-label">PERP PnL</div>
            <div className="stat-value" style={{ fontSize: 16, color: pc(totalPnl) }}>
              {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
            </div>
            <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO }}>of ${totalVal.toFixed(0)} value</div>
          </div>
        )}
        {/* Position count */}
        {positions.length > 0 && (
          <div className="stat-tile">
            <div className="stat-label">POSITIONS</div>
            <div className="stat-value" style={{ fontSize: 16 }}>{positions.length}</div>
            <div style={{ fontSize: 10, color: 'var(--dim)', ...MONO }}>Jupiter Perps</div>
          </div>
        )}
      </div>

      {/* Positions table */}
      {loading ? (
        <div style={{ color: '#4d5a6e', fontSize: 10, ...MONO }}>fetching positions…</div>
      ) : error ? (
        <div style={{ color: '#ef4444', fontSize: 10, ...MONO }}>⚠ {error}</div>
      ) : positions.length === 0 ? (
        <div style={{ color: '#2d4060', fontSize: 10, ...MONO }}>no open Jupiter perp positions</div>
      ) : (
        <>
          <div className="pos-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Market</th>
                <th>Side</th>
                <th style={{ textAlign: 'right' }}>Entry</th>
                <th style={{ textAlign: 'right' }}>Mark</th>
                <th style={{ textAlign: 'right' }}>Size</th>
                <th style={{ textAlign: 'right' }}>PnL</th>
                <th style={{ textAlign: 'right' }}>Lev</th>
                <th style={{ textAlign: 'right' }}>Liq</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 700, color: '#c0cfe0' }}>{p.market}</td>
                  <td>
                    <span style={{ color: p.side === 'LONG' ? '#00d48a' : '#f59e0b', fontWeight: 700, fontSize: 10 }}>
                      {p.side}
                    </span>
                  </td>
                  <td style={{ textAlign: 'right', color: '#5a7a9a' }}>{fp(p.entry_price)}</td>
                  <td style={{ textAlign: 'right', color: '#a0aec0' }}>{fp(p.mark_price)}</td>
                  <td style={{ textAlign: 'right', color: '#5a7a9a', fontSize: 10 }}>${p.size_usd.toFixed(0)}</td>
                  <td style={{ textAlign: 'right', color: pc(p.pnl_usd), fontWeight: 700 }}>
                    {p.pnl_usd >= 0 ? '+' : ''}${p.pnl_usd.toFixed(2)}
                    <span style={{ color: pc(p.pnl_usd), opacity: 0.7, fontSize: 9, marginLeft: 4 }}>
                      ({p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct.toFixed(1)}%)
                    </span>
                  </td>
                  <td style={{ textAlign: 'right', color: '#5a7a9a', fontSize: 10 }}>{p.leverage.toFixed(1)}×</td>
                  <td style={{ textAlign: 'right', color: p.liq_near ? '#ef4444' : 'var(--dim)', fontSize: 10, fontWeight: p.liq_near ? 700 : 400 }}>
                    {fp(p.liq_price)}{p.liq_near && ' ⚠'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <div style={{ marginTop: 8, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {positions.map((p, i) => (
              <div key={i} style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>
                <span style={{ color: 'var(--muted)' }}>{p.market}</span>
                {' '}col ${p.collateral_usd.toFixed(2)} · fees ${p.total_fees_usd.toFixed(2)}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
