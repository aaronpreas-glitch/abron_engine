import { useState } from 'react'
import { isAuthenticated, clearToken } from './api'
import { LoginPage } from './LoginPage'
import { Terminal } from './Terminal'

export default function App() {
  const [authed, setAuthed] = useState(isAuthenticated)

  function handleLogin() { setAuthed(true) }

  function handleLogout() {
    clearToken()
    setAuthed(false)
  }

  if (!authed) return <LoginPage onLogin={handleLogin} />
  return <Terminal onLogout={handleLogout} />
}
