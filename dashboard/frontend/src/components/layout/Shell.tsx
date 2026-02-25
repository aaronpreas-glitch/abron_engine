import { Outlet } from 'react-router-dom'
import { RiskBanner } from './RiskBanner'
import { Sidebar } from './Sidebar'
import { AiChat } from '../ai/AiChat'
import { SignalToast } from '../signals/SignalToast'

export function Shell() {
  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <RiskBanner />
        <main style={{
          flex: 1,
          overflow: 'auto',
          padding: '20px 24px',
          background: 'transparent',
        }}>
          <Outlet />
        </main>
      </div>
      {/* AI chat — floats over all pages, bottom-right */}
      <AiChat />
      {/* Signal toasts — top-right ALERT notifications via WebSocket */}
      <SignalToast />
    </div>
  )
}
