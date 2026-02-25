// ws.ts — WebSocket singleton with auto-reconnect and auth handshake

import { getToken } from './api'

type MsgHandler = (msg: unknown) => void

class SignalSocket {
  private ws: WebSocket | null = null
  private handlers: Set<MsgHandler> = new Set()
  private reconnectDelay = 2000
  private _connected = false
  private shouldConnect = false

  connect() {
    this.shouldConnect = true
    this._open()
  }

  disconnect() {
    this.shouldConnect = false
    this.ws?.close()
    this.ws = null
  }

  subscribe(fn: MsgHandler) {
    this.handlers.add(fn)
    return () => this.handlers.delete(fn)
  }

  get connected() { return this._connected }

  private _open() {
    if (!this.shouldConnect) return
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/ws/signals`
    const ws = new WebSocket(url)
    this.ws = ws

    ws.onopen = () => {
      const token = getToken()
      ws.send(JSON.stringify({ type: 'auth', token }))
    }

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'connected') { this._connected = true; this.reconnectDelay = 2000 }
        if (msg.type === 'ping') ws.send(JSON.stringify({ type: 'ping' }))
        this.handlers.forEach((h) => h(msg))
      } catch { /* ignore malformed */ }
    }

    ws.onclose = () => {
      this._connected = false
      if (this.shouldConnect) {
        setTimeout(() => this._open(), this.reconnectDelay)
        this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, 30000)
      }
    }

    ws.onerror = () => ws.close()
  }
}

export const signalSocket = new SignalSocket()
