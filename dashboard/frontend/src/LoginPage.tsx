import { useState } from 'react'
import axios from 'axios'
import { setToken } from './api'

interface Props {
  onLogin: () => void
}

export function LoginPage({ onLogin }: Props) {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setErr('')
    try {
      const res = await axios.post('/api/auth/login', { password: pw })
      setToken(res.data.token)
      onLogin()
    } catch {
      setErr('Invalid password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex',
      alignItems: 'center', justifyContent: 'center',
      fontFamily: 'JetBrains Mono, monospace',
    }}>
      <div className="card" style={{ width: 320, padding: '32px 28px' }}>
        <div style={{ color: 'var(--green)', fontSize: 18, fontWeight: 700, marginBottom: 8, letterSpacing: '0.1em' }}>
          ABRON ENGINE
        </div>
        <div style={{ color: 'var(--dim)', fontSize: 11, marginBottom: 32 }}>TERMINAL ACCESS</div>
        <form onSubmit={handleSubmit}>
          <input
            type="password"
            value={pw}
            onChange={e => setPw(e.target.value)}
            placeholder="Password"
            autoFocus
            style={{ width: '100%', boxSizing: 'border-box' }}
          />
          {err && <div style={{ color: 'var(--red)', fontSize: 11, marginTop: 8 }}>{err}</div>}
          <button
            type="submit"
            disabled={loading || !pw}
            style={{
              width: '100%', marginTop: 12, padding: '10px',
              background: loading ? 'var(--green-bg)' : 'var(--green)',
              color: loading ? 'var(--dim)' : '#000',
              border: loading ? '1px solid var(--green-border)' : 'none',
              borderRadius: 4, fontFamily: 'inherit', fontSize: 13, fontWeight: 700,
              cursor: loading || !pw ? 'default' : 'pointer',
              letterSpacing: '0.05em',
              opacity: !pw && !loading ? 0.55 : 1,
              transition: 'background 0.15s, opacity 0.15s',
            }}
          >
            {loading ? 'CONNECTING...' : 'ENTER'}
          </button>
        </form>
      </div>
    </div>
  )
}
