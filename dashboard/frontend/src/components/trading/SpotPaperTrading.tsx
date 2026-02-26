/**
 * SpotPaperTrading — Paper (simulated) spot token trading.
 * Auto-fires on every signal that scores 72+. No real money.
 * Re-exports LivePositions with a PAPER context badge.
 */
import { LivePositions } from '../home/LivePositions'

export function SpotPaperTrading() {
  return <LivePositions mode="paper" />
}
