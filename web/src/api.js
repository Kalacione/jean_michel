// REST client for the Jean-Michel daemon.
//
// The bearer token lives in localStorage. All calls hit `/api/*`, served by
// the vite dev proxy (→ daemon :8000) in dev and by nginx in production.

const TOKEN_KEY = 'jm_token'

export function getToken () {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken (token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor (status, detail) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }
}

async function request (method, path, body) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  login: (username, password) => request('POST', '/auth/login', { username, password }),
  me: () => request('GET', '/auth/me'),

  listConversations: () => request('GET', '/conversations'),
  createConversation: mode => request('POST', '/conversations', { mode }),
  getConversation: id => request('GET', `/conversations/${id}`),
  messages: id => request('GET', `/conversations/${id}/messages`),
  events: id => request('GET', `/conversations/${id}/events`),
  state: id => request('GET', `/conversations/${id}/state`),
  workspace: (id, sub = '') =>
    request('GET', `/conversations/${id}/workspace${sub ? `?sub_path=${encodeURIComponent(sub)}` : ''}`),
  workspaceFile: (id, path) =>
    request('GET', `/conversations/${id}/workspace/file?path=${encodeURIComponent(path)}`),

  listMemory: type => request('GET', `/memory${type ? `?type=${encodeURIComponent(type)}` : ''}`),
  recallMemory: (type, code) => request('GET', `/memory/${type}/${code}`),
  saveMemory: entry => request('POST', '/memory', entry),
  updateMemory: (type, code, patch) => request('PATCH', `/memory/${type}/${code}`, patch),
  deleteMemory: (type, code) => request('DELETE', `/memory/${type}/${code}`),

  getProfile: () => request('GET', '/profile'),
  updateProfile: patch => request('PATCH', '/profile', patch),

  // TTS is auth-gated, so an <audio src> can't carry the token : fetch with the
  // header and return a Blob the caller plays via the Web Audio / Audio API.
  async tts (text) {
    const token = getToken()
    const res = await fetch(`/api/tts?text=${encodeURIComponent(text)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    return res.ok ? res.blob() : null
  },
}
