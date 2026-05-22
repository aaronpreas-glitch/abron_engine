// api.ts — axios instance with auth + interceptors

import axios from 'axios'

const TOKEN_KEY = 'dash_token'

export const api = axios.create({ baseURL: '/api' })

interface RequestMetric {
  ts: number
  path: string
  ms: number
  ok: boolean
  status?: number
}

const requestMetrics: RequestMetric[] = []
let inflightRequests = 0

function normalizePath(url: string | undefined): string {
  const raw = String(url || '')
  return raw.split('?')[0] || 'unknown'
}

function recordMetric(metric: RequestMetric) {
  const cutoff = Date.now() - 5 * 60_000
  requestMetrics.push(metric)
  while (requestMetrics.length > 500 || (requestMetrics[0] && requestMetrics[0].ts < cutoff)) {
    requestMetrics.shift()
  }
}

export function getDashboardRequestMetrics() {
  const now = Date.now()
  const recent = requestMetrics.filter(m => now - m.ts <= 60_000)
  const slow = recent.filter(m => m.ms >= 2_000)
  const failed = recent.filter(m => !m.ok)
  const avgMs = recent.length ? recent.reduce((s, m) => s + m.ms, 0) / recent.length : 0
  const byPath = new Map<string, { path: string; count: number; slow: number; failed: number; avg_ms: number }>()
  for (const metric of recent) {
    const row = byPath.get(metric.path) || { path: metric.path, count: 0, slow: 0, failed: 0, avg_ms: 0 }
    row.count += 1
    row.slow += metric.ms >= 2_000 ? 1 : 0
    row.failed += metric.ok ? 0 : 1
    row.avg_ms += metric.ms
    byPath.set(metric.path, row)
  }
  const paths = Array.from(byPath.values())
    .map(row => ({ ...row, avg_ms: Math.round(row.avg_ms / Math.max(row.count, 1)) }))
    .sort((a, b) => b.count - a.count || b.avg_ms - a.avg_ms)
  return {
    generated_at: new Date(now).toISOString(),
    requests_60s: recent.length,
    inflight: inflightRequests,
    slow_60s: slow.length,
    failed_60s: failed.length,
    avg_ms: Math.round(avgMs),
    busiest: paths.slice(0, 5),
  }
}

api.interceptors.request.use((cfg) => {
  const t = localStorage.getItem(TOKEN_KEY)
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  cfg.headers['Cache-Control'] = 'no-cache'
  cfg.headers.Pragma = 'no-cache'
  ;(cfg as any).__dashMeta = { startedAt: Date.now(), path: normalizePath(cfg.url) }
  inflightRequests += 1
  return cfg
})

api.interceptors.response.use(
  (r) => {
    const meta = (r.config as any).__dashMeta || {}
    inflightRequests = Math.max(0, inflightRequests - 1)
    recordMetric({
      ts: Date.now(),
      path: meta.path || normalizePath(r.config.url),
      ms: Date.now() - Number(meta.startedAt || Date.now()),
      ok: true,
      status: r.status,
    })
    return r
  },
  (err) => {
    const meta = (err.config as any)?.__dashMeta || {}
    inflightRequests = Math.max(0, inflightRequests - 1)
    recordMetric({
      ts: Date.now(),
      path: meta.path || normalizePath(err.config?.url),
      ms: Date.now() - Number(meta.startedAt || Date.now()),
      ok: false,
      status: err.response?.status,
    })
    if (err.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)
export const isAuthenticated = () => !!localStorage.getItem(TOKEN_KEY)
