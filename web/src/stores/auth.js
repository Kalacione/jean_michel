import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, getToken, setToken } from '@/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getToken())
  const user = ref(null)
  const error = ref('')
  const loading = ref(false)
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
  async function fetchMe () {
    if (!token.value) return
    try {
      user.value = (await api.me()).user
    } catch {
      logout()
    }
  }

  return { token, user, error, loading, isAuthed, login, logout, fetchMe }
})
