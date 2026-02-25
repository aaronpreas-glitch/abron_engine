/**
 * AiChat — Claude-powered query drawer over live engine DB.
 *
 * Floating button bottom-right → opens a slide-up drawer.
 * Each message streams back from /api/chat via fetch + ReadableStream.
 * Stateless per-message (no conversation history — keeps context small).
 */
import { useState, useRef, useEffect, useCallback } from 'react'
import { getToken } from '../../api'

// ── types ─────────────────────────────────────────────────────────────────────

interface Message {
  id: number
  role: 'user' | 'assistant'
  text: string
  streaming?: boolean
  error?: boolean
}

// ── suggested prompts ──────────────────────────────────────────────────────────

const SUGGESTIONS = [
  'Which symbol has the best 4h win rate?',
  'How many alerts fired this week?',
  'What regime gives the highest returns?',
  'Should I adjust my alert threshold?',
  'Summarize my open positions vs engine signals',
  'Which symbols are pending outcome evaluation?',
]

// ── helpers ───────────────────────────────────────────────────────────────────

let _idCounter = 0
function nextId() { return ++_idCounter }

// ── component ─────────────────────────────────────────────────────────────────

export function AiChat() {
  const [open, setOpen]       = useState(false)
  const [input, setInput]     = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [busy, setBusy]       = useState(false)
  const bottomRef             = useRef<HTMLDivElement>(null)
  const inputRef              = useRef<HTMLTextAreaElement>(null)
  const abortRef              = useRef<AbortController | null>(null)

  // Auto-scroll to bottom when messages update
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input when drawer opens
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 120)
  }, [open])

  const send = useCallback(async (text: string) => {
    const q = text.trim()
    if (!q || busy) return
    setInput('')

    const userMsg: Message = { id: nextId(), role: 'user', text: q }
    const asstId = nextId()
    const asstMsg: Message = { id: asstId, role: 'assistant', text: '', streaming: true }

    setMessages(prev => [...prev, userMsg, asstMsg])
    setBusy(true)

    const ctrl = new AbortController()
    abortRef.current = ctrl

    try {
      const token = getToken()
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ message: q }),
        signal: ctrl.signal,
      })

      if (!res.ok) {
        const detail = await res.text().catch(() => `HTTP ${res.status}`)
        setMessages(prev => prev.map(m =>
          m.id === asstId
            ? { ...m, text: detail.includes('ANTHROPIC_API_KEY')
                ? '⚠️ API key not configured on server. Add ANTHROPIC_API_KEY to the VPS .env file.'
                : `❌ Error: ${detail}`, streaming: false, error: true }
            : m
        ))
        return
      }

      // Stream the response
      const reader = res.body?.getReader()
      const decoder = new TextDecoder()
      if (!reader) throw new Error('No response body')

      let accumulated = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = decoder.decode(value, { stream: true })
        accumulated += chunk
        const captured = accumulated
        setMessages(prev => prev.map(m =>
          m.id === asstId ? { ...m, text: captured } : m
        ))
      }

      // Mark as done
      setMessages(prev => prev.map(m =>
        m.id === asstId ? { ...m, streaming: false } : m
      ))
    } catch (err: unknown) {
      if ((err as Error)?.name === 'AbortError') return
      setMessages(prev => prev.map(m =>
        m.id === asstId
          ? { ...m, text: `❌ ${(err as Error)?.message || 'Network error'}`, streaming: false, error: true }
          : m
      ))
    } finally {
      setBusy(false)
      abortRef.current = null
    }
  }, [busy])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    }
  }

  const clear = () => {
    abortRef.current?.abort()
    setMessages([])
    setBusy(false)
    setInput('')
  }

  return (
    <>
      {/* ── Floating trigger button ───────────────────────────────────────── */}
      <button
        onClick={() => setOpen(o => !o)}
        title="Ask the Engine"
        style={{
          position: 'fixed',
          bottom: 24,
          right: 24,
          zIndex: 900,
          width: 48,
          height: 48,
          borderRadius: '50%',
          background: open
            ? 'var(--surface)'
            : 'linear-gradient(135deg, #00d48a 0%, #007a52 100%)',
          border: open ? '1px solid var(--border)' : 'none',
          color: open ? 'var(--muted)' : '#000',
          fontSize: 20,
          cursor: 'pointer',
          boxShadow: open ? 'none' : '0 4px 20px rgba(0,212,138,0.35)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition: 'all 0.2s',
        }}
      >
        {open ? '✕' : '🧠'}
      </button>

      {/* ── Drawer ───────────────────────────────────────────────────────── */}
      {open && (
        <div style={{
          position: 'fixed',
          bottom: 84,
          right: 24,
          zIndex: 900,
          width: 420,
          maxWidth: 'calc(100vw - 48px)',
          height: 540,
          maxHeight: 'calc(100vh - 120px)',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 8px 40px rgba(0,0,0,0.5)',
          overflow: 'hidden',
          animation: 'chatSlideUp 0.18s ease-out',
        }}>

          {/* Header */}
          <div style={{
            padding: '10px 14px',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flexShrink: 0,
          }}>
            <span style={{ fontSize: 14 }}>🧠</span>
            <div>
              <div style={{ fontWeight: 700, fontSize: 12, letterSpacing: '0.05em' }}>ASK THE ENGINE</div>
              <div style={{ fontSize: 9, color: 'var(--muted)', letterSpacing: '0.08em' }}>
                powered by claude · live DB context
              </div>
            </div>
            {messages.length > 0 && (
              <button
                onClick={clear}
                style={{
                  marginLeft: 'auto', background: 'none', border: '1px solid var(--border)',
                  color: 'var(--muted)', padding: '2px 8px', borderRadius: 3,
                  cursor: 'pointer', fontSize: 10,
                }}
              >
                clear
              </button>
            )}
          </div>

          {/* Messages area */}
          <div style={{
            flex: 1,
            overflowY: 'auto',
            padding: '12px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}>

            {/* Empty state — show suggestions */}
            {messages.length === 0 && (
              <div>
                <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 12 }}>
                  Ask anything about your engine's signals, outcomes, or performance.
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {SUGGESTIONS.map(s => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      style={{
                        textAlign: 'left',
                        background: 'var(--surface2)',
                        border: '1px solid var(--border)',
                        color: 'var(--muted)',
                        borderRadius: 4,
                        padding: '7px 10px',
                        fontSize: 11,
                        cursor: 'pointer',
                        transition: 'border-color 0.1s, color 0.1s',
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.borderColor = 'var(--green)'
                        e.currentTarget.style.color = 'var(--text)'
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.borderColor = 'var(--border)'
                        e.currentTarget.style.color = 'var(--muted)'
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Message bubbles */}
            {messages.map(msg => (
              <div
                key={msg.id}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
                }}
              >
                {/* Role label */}
                <div style={{
                  fontSize: 9, color: 'var(--dim)',
                  marginBottom: 3,
                  letterSpacing: '0.08em',
                  fontFamily: 'JetBrains Mono, monospace',
                }}>
                  {msg.role === 'user' ? 'YOU' : 'ENGINE AI'}
                </div>

                {/* Bubble */}
                <div style={{
                  maxWidth: '90%',
                  padding: '8px 11px',
                  borderRadius: msg.role === 'user' ? '8px 8px 2px 8px' : '8px 8px 8px 2px',
                  background: msg.role === 'user'
                    ? 'var(--green-bg)'
                    : msg.error
                    ? 'rgba(248,81,73,0.08)'
                    : 'var(--surface2)',
                  border: `1px solid ${
                    msg.role === 'user'
                      ? 'rgba(57,211,83,0.2)'
                      : msg.error
                      ? 'rgba(248,81,73,0.2)'
                      : 'var(--border)'
                  }`,
                  fontSize: 12,
                  lineHeight: 1.55,
                  color: msg.error ? 'var(--red)' : 'var(--text)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}>
                  {msg.text || (msg.streaming ? '' : '—')}
                  {/* Streaming cursor */}
                  {msg.streaming && (
                    <span style={{
                      display: 'inline-block',
                      width: 8,
                      height: 13,
                      background: 'var(--green)',
                      marginLeft: 2,
                      verticalAlign: 'text-bottom',
                      animation: 'cursorBlink 0.8s step-end infinite',
                      borderRadius: 1,
                    }} />
                  )}
                </div>
              </div>
            ))}

            <div ref={bottomRef} />
          </div>

          {/* Input area */}
          <div style={{
            borderTop: '1px solid var(--border)',
            padding: '10px 12px',
            flexShrink: 0,
          }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask anything… (Enter to send, Shift+Enter for newline)"
                rows={2}
                style={{
                  flex: 1,
                  background: 'var(--bg)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)',
                  borderRadius: 4,
                  padding: '7px 10px',
                  fontSize: 12,
                  resize: 'none',
                  fontFamily: 'inherit',
                  lineHeight: 1.4,
                  outline: 'none',
                  transition: 'border-color 0.15s',
                }}
                onFocus={e => (e.target.style.borderColor = 'var(--green)')}
                onBlur={e => (e.target.style.borderColor = 'var(--border)')}
                disabled={busy}
              />
              <button
                onClick={() => send(input)}
                disabled={busy || !input.trim()}
                style={{
                  padding: '8px 14px',
                  borderRadius: 4,
                  border: 'none',
                  cursor: busy || !input.trim() ? 'default' : 'pointer',
                  background: busy || !input.trim()
                    ? 'var(--surface2)'
                    : 'linear-gradient(135deg, #00d48a 0%, #007a52 100%)',
                  color: busy || !input.trim() ? 'var(--dim)' : '#000',
                  fontWeight: 700,
                  fontSize: 12,
                  flexShrink: 0,
                  transition: 'all 0.15s',
                  height: 52,
                }}
              >
                {busy ? '…' : '↑'}
              </button>
            </div>
            <div style={{ fontSize: 9, color: 'var(--dim)', marginTop: 5, letterSpacing: '0.04em' }}>
              Each query includes a live snapshot of your signals, outcomes, and config
            </div>
          </div>
        </div>
      )}

      {/* Keyframes */}
      <style>{`
        @keyframes chatSlideUp {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes cursorBlink {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0; }
        }
      `}</style>
    </>
  )
}
