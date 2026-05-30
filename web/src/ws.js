// WebSocket turn client.
//
// Streams the orchestrator's events for one conversation and carries ask_human
// answers back. The token rides in the query string — the WS handshake can't
// set an Authorization header.

import { getToken } from './api'

export function connectTurn (convId, handlers = {}) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = encodeURIComponent(getToken() || '')
  const ws = new WebSocket(`${proto}://${location.host}/ws/conversations/${convId}?token=${token}`)

  ws.onmessage = ev => {
    let msg
    try {
      msg = JSON.parse(ev.data)
    } catch {
      return
    }
    // Server message types: dispatch | event | ask_human | final | queued | error.
    handlers[msg.type]?.(msg)
  }
  ws.onclose = e => handlers.close?.(e)
  ws.onerror = e => handlers.wserror?.(e)

  return {
    sendTurn: (text, files = []) => ws.send(JSON.stringify({ type: 'turn', text, files })),
    sendAnswer: text => ws.send(JSON.stringify({ type: 'answer', text })),
    close: () => ws.close(),
    raw: ws,
  }
}
