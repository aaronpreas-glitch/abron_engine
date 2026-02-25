import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../../api'

export function LoginPage() {
  const [pw, setPw]     = useState('')
  const [err, setErr]   = useState('')
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  async function login(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setErr('')
    try {
      const { data } = await api.post('/auth/login', { password: pw })
      setToken(data.token)
      nav('/')
    } catch {
      setErr('Invalid password')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:'var(--bg)' }}>
      <form onSubmit={login} style={{ width: 320 }}>
        <div style={{ marginBottom: 24, textAlign: 'center' }}>
          <div style={{ color: 'var(--green)', fontSize: 22, fontWeight: 700, letterSpacing: '0.08em' }}>◈ ABRONS ENGINE</div>
          <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 4 }}>personal trading dashboard</div>
        </div>
        <div className="card">
          <label style={{ color: 'var(--muted)', fontSize: 11, display: 'block', marginBottom: 6 }}>PASSWORD</label>
          <input
            type="password"
            value={pw}
            onChange={e => setPw(e.target.value)}
            style={{
              width: '100%', padding: '8px 10px',
              background: 'var(--bg)', border: '1px solid var(--border)',
              color: 'var(--text)', borderRadius: 3, fontFamily: 'monospace',
              fontSize: 14, marginBottom: 12,
            }}
            autoFocus
          />
          {err && <div style={{ color: 'var(--red)', fontSize: 11, marginBottom: 10 }}>{err}</div>}
          <button
            type="submit"
            disabled={busy}
            style={{
              width: '100%', padding: '9px', background: 'var(--green)',
              color: '#000', border: 'none', borderRadius: 3,
              fontFamily: 'monospace', fontWeight: 700, fontSize: 13, cursor: 'pointer',
            }}
          >
            {busy ? 'Connecting…' : 'Enter Dashboard'}
          </button>
        </div>
      </form>
    </div>
  )
}
