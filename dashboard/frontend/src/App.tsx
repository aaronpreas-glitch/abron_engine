import { Navigate, Route, Routes } from 'react-router-dom'
import { isAuthenticated } from './api'
import { Shell } from './components/layout/Shell'
import { LoginPage } from './components/layout/LoginPage'
import { CommandCenter } from './components/home/CommandCenter'
import { SignalFeed } from './components/signals/SignalFeed'
import { OutcomeGatedFeed } from './components/signals/OutcomeGatedFeed'
import { WatchlistPanel } from './components/watchlist/WatchlistPanel'
import { PerformancePanel } from './components/performance/PerformancePanel'
import { RegimeTimeline } from './components/regime/RegimeTimeline'
import { RegimeHeatmap } from './components/regime/RegimeHeatmap'
import { PositionsPanel } from './components/positions/PositionsPanel'
import { Leaderboard } from './components/leaderboard/Leaderboard'
import { ConfigEditor } from './components/config/ConfigEditor'
import { NewsPanel } from './components/news/NewsPanel'
import { SolPanel } from './components/sol/SolPanel'
import { RiskPanel } from './components/risk/RiskPanel'
import { SymbolPage } from './components/symbols/SymbolPage'
import { Brain } from './components/brain/Brain'
import { LaunchFeed } from './components/launches/LaunchFeed'
import { ArbFeed } from './components/arb/ArbFeed'
// Trading sub-pages
import { SpotPaperTrading } from './components/trading/SpotPaperTrading'
import { SpotLiveTrading } from './components/trading/SpotLiveTrading'
import { PerpsPaperTrading } from './components/trading/PerpsPaperTrading'
import { PerpsLiveTrading } from './components/trading/PerpsLiveTrading'

function RequireAuth({ children }: { children: React.ReactNode }) {
  return isAuthenticated() ? <>{children}</> : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<RequireAuth><Shell /></RequireAuth>}>
        <Route index                 element={<CommandCenter />} />
        <Route path="signals"        element={<SignalFeed />} />
        <Route path="outcome-feed"   element={<OutcomeGatedFeed />} />
        <Route path="watchlist"      element={<WatchlistPanel />} />
        <Route path="performance"    element={<PerformancePanel />} />
        <Route path="regime"         element={<RegimeTimeline />} />
        <Route path="regime-heatmap" element={<RegimeHeatmap />} />
        <Route path="positions"      element={<PositionsPanel />} />
        <Route path="risk"           element={<RiskPanel />} />
        <Route path="leaderboard"    element={<Leaderboard />} />
        <Route path="sol"            element={<SolPanel />} />
        <Route path="news"           element={<NewsPanel />} />
        <Route path="config"         element={<ConfigEditor />} />
        <Route path="brain"          element={<Brain />} />
        <Route path="launches"       element={<LaunchFeed />} />
        <Route path="arb"            element={<ArbFeed />} />
        <Route path="symbol/:symbol" element={<SymbolPage />} />

        {/* Trading hub — 4 sub-pages */}
        <Route path="trading/spot-paper"  element={<SpotPaperTrading />} />
        <Route path="trading/spot-live"   element={<SpotLiveTrading />} />
        <Route path="trading/perps-paper" element={<PerpsPaperTrading />} />
        <Route path="trading/perps-live"  element={<PerpsLiveTrading />} />

        {/* Legacy redirects — keep old URLs working */}
        <Route path="executor" element={<Navigate to="/trading/spot-paper" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
