export type DashboardPage = 'home' | 'system' | 'memecoins' | 'audit'

export interface BudgetIntervalOptions {
  enabled?: boolean
  hiddenMultiplier?: number
  minHiddenMs?: number
  maxHiddenMs?: number
}

export function dashboardHidden(): boolean {
  return typeof document !== 'undefined' ? document.hidden : false
}

export function budgetedInterval(baseMs: number, options: BudgetIntervalOptions = {}) {
  return () => {
    if (options.enabled === false) return false
    if (!dashboardHidden()) return baseMs
    const hidden = Math.max(
      options.minHiddenMs ?? 120_000,
      Math.round(baseMs * (options.hiddenMultiplier ?? 4)),
    )
    return Math.min(hidden, options.maxHiddenMs ?? 900_000)
  }
}

export function slowBudgetedInterval(baseMs: number, enabled = true) {
  return budgetedInterval(baseMs, {
    enabled,
    hiddenMultiplier: 6,
    minHiddenMs: 300_000,
    maxHiddenMs: 1_800_000,
  })
}

export function liveBudgetedInterval(baseMs: number, enabled = true) {
  return budgetedInterval(baseMs, {
    enabled,
    hiddenMultiplier: 3,
    minHiddenMs: 120_000,
    maxHiddenMs: 600_000,
  })
}
