import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, getToken, setToken } from '@/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getToken())
  const user = ref(null)
  const error = ref('')
  const loading = ref(false)
  const ready = ref(false) // startup token validation done (fetchMe resolved)
  const isAuthed = computed(() => !!token.value)

  async function login (username, password) {
    loading.value = true
    error.value = ''
    try {
      const res = await api.login(username, password)
      setToken(res.token)
      token.value = res.token
      user.value = res.user
    } catch (e) {
      error.value = e.status === 401 ? 'Identifiants invalides.' : (e.message || 'Échec de connexion.')
      throw e
    } finally {
      loading.value = false
    }
  }

  function logout () {
    setToken(null)
    token.value = null
    user.value = null
  }

  // Validate a persisted token on startup ; drop it if the daemon rejects it.
  // `ready` flips true once done so the app gates rendering on a VALIDATED token
  // (else a stale token would mount the main UI, fire failing calls, and blank
  // the screen instead of falling back to the login view).
  async function fetchMe () {
    try {
      if (token.value) user.value = (await api.me()).user
    } catch {
      logout()
    } finally {
      ready.value = true
    }
  }

  return { token, user, error, loading, ready, isAuthed, login, logout, fetchMe }
})
