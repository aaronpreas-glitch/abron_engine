/**
 * SpotLiveTrading — Real-money spot token trading.
 * Uses same executor but DRY_RUN=false.
 * Requires WALLET_PRIVATE_KEY in .env.
 */
import { LivePositions } from '../home/LivePositions'

export function SpotLiveTrading() {
  return <LivePositions mode="live" />
}
