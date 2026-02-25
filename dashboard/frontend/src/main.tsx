import { StrictMode, Component } from 'react'
import type { ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
  },
})

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null }
  static getDerivedStateFromError(e: Error) { return { error: e } }
  render() {
    const { error } = this.state
    if (error) return (
      <div style={{
        padding: 40, fontFamily: 'JetBrains Mono, monospace',
        background: '#070b10', color: '#f04f4f', minHeight: '100vh',
      }}>
        <div style={{ fontSize: 13, marginBottom: 12, color: '#dde3ed' }}>⚠ Dashboard crashed</div>
        <div style={{ fontSize: 11, color: '#f04f4f', whiteSpace: 'pre-wrap' }}>
          {(error as Error).message}
        </div>
        <div style={{ fontSize: 10, color: '#4d5a6e', marginTop: 12, whiteSpace: 'pre-wrap' }}>
          {(error as Error).stack}
        </div>
        <button
          onClick={() => window.location.reload()}
          style={{ marginTop: 20, padding: '8px 16px', background: '#00d48a', color: '#000', border: 'none', borderRadius: 4, cursor: 'pointer', fontFamily: 'JetBrains Mono, monospace' }}
        >
          Reload
        </button>
      </div>
    )
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
)
