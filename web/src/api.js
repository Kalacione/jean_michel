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
  createConversation: (mode, projectId = null) =>
    request('POST', '/conversations', { mode, project_id: projectId }),
  getConversation: id => request('GET', `/conversations/${id}`),
  renameConversation: (id, title) => request('PATCH', `/conversations/${id}`, { title }),
  setConversationProject: (id, projectId) =>
    request('PUT', `/conversations/${id}/project`, { project_id: projectId }),
  deleteConversation: id => request('DELETE', `/conversations/${id}`),
  messages: id => request('GET', `/conversations/${id}/messages`),
  events: id => request('GET', `/conversations/${id}/events`),
  state: id => request('GET', `/conversations/${id}/state`),
  getTodo: id => request('GET', `/conversations/${id}/todo`),
  putTodo: (id, goal, items) => request('PUT', `/conversations/${id}/todo`, { goal, items }),

  snapshots: id => request('GET', `/conversations/${id}/snapshots`),
  revertConversation: (id, commit) => request('POST', `/conversations/${id}/revert`, { commit }),
  forkConversation: (id, commit) => request('POST', `/conversations/${id}/fork`, { commit }),
  workspace: (id, sub = '') =>
    request('GET', `/conversations/${id}/workspace${sub ? `?sub_path=${encodeURIComponent(sub)}` : ''}`),
  workspaceFile: (id, path) =>
    request('GET', `/conversations/${id}/workspace/file?path=${encodeURIComponent(path)}`),

  // Upload is multipart (not JSON) → can't use request() ; build FormData and
  // let the browser set the boundary. Returns {results:[{name,status,...}]}.
  async uploadWorkspace (id, fileList) {
    const token = getToken()
    const form = new FormData()
    for (const f of fileList) form.append('files', f)
    const res = await fetch(`/api/conversations/${id}/workspace/upload`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    })
    if (!res.ok) {
      let detail = res.statusText
      try { detail = (await res.json()).detail ?? detail } catch { /* non-JSON */ }
      throw new ApiError(res.status, detail)
    }
    return res.json()
  },

  // Download is auth-gated (no token in a bare <a href>) → fetch with the
  // header and hand back a Blob the caller saves.
  async downloadWorkspace (id, path) {
    const token = getToken()
    const res = await fetch(
      `/api/conversations/${id}/workspace/download?path=${encodeURIComponent(path)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    )
    return res.ok ? res.blob() : null
  },

  async downloadWorkspaceZip (id) {
    const token = getToken()
    const res = await fetch(`/api/conversations/${id}/workspace/zip`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    return res.ok ? res.blob() : null
  },

  // Auth-gated image fetch → Blob the caller turns into an objectURL for <img>.
  // thumb=true asks for the normalized ≤1024 WebP derivative.
  async workspaceImage (id, path, { thumb = false } = {}) {
    const token = getToken()
    const q = `path=${encodeURIComponent(path)}${thumb ? '&thumb=1' : ''}`
    const res = await fetch(`/api/conversations/${id}/workspace/image?${q}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    return res.ok ? res.blob() : null
  },

  // Memory is scope-based (world/user/project/tool). project/tool targets pass
  // their key as a query param ; user/world need none.
  listMemory: (scope, params = {}) => {
    const q = new URLSearchParams()
    if (scope) q.set('scope', scope)
    for (const [k, v] of Object.entries(params)) if (v != null) q.set(k, v)
    const qs = q.toString()
    return request('GET', `/memory${qs ? `?${qs}` : ''}`)
  },
  searchMemory: (query, params = {}) => {
    const q = new URLSearchParams({ q: query })
    for (const [k, v] of Object.entries(params)) if (v != null) q.set(k, v)
    return request('GET', `/memory/search?${q.toString()}`)
  },
  recallMemory: (scope, code, params = {}) => {
    const q = new URLSearchParams(params)
    const qs = q.toString()
    return request('GET', `/memory/${scope}/${code}${qs ? `?${qs}` : ''}`)
  },
  saveMemory: entry => request('POST', '/memory', entry),
  updateMemory: (scope, code, patch) => request('PATCH', `/memory/${scope}/${code}`, patch),
  deleteMemory: (scope, code, params = {}) => {
    const q = new URLSearchParams(params)
    const qs = q.toString()
    return request('DELETE', `/memory/${scope}/${code}${qs ? `?${qs}` : ''}`)
  },

  listProjects: (includeArchived = true) =>
    request('GET', `/projects?include_archived=${includeArchived ? 'true' : 'false'}`),
  createProject: project => request('POST', '/projects', project),
  updateProject: (id, patch) => request('PATCH', `/projects/${id}`, patch),
  deleteProject: id => request('DELETE', `/projects/${id}`),

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
