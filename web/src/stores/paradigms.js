import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'

// Paradigm curation (admin panel). Two surfaces : the paradigm catalog (list + edit +
// bind + toggle), and the promotion queue — kind='rule' candidates awaiting human
// review, GLOBAL (cross-conversation), each carrying its conversation_id.
export const useParadigmsStore = defineStore('paradigms', () => {
  const list = ref([])        // full paradigms : {code, title, content, rationale, is_global, active, order_priority, modes, agents, section_code, category_code, …}
  const promotions = ref([])  // [{conversation_id, candidate}]
  const loading = ref(false)
  const error = ref('')

  async function refresh () {
    loading.value = true
    error.value = ''
    try {
      const [paradigms, promos] = await Promise.all([
        api.listParadigms().then(r => r.paradigms || []),
        api.listPromotions().then(r => r.promotions || []).catch(() => []),
      ])
      list.value = paradigms
      promotions.value = promos
    } catch (e) {
      error.value = e?.detail || 'Échec du chargement des paradigmes.'
    } finally {
      loading.value = false
    }
  }

  function _replace (updated) {
    const i = list.value.findIndex(p => p.code === updated.code)
    if (i !== -1) list.value[i] = updated
    return updated
  }

  // patch : {title?, content?, rationale?, is_global?, order_priority?, active?, modes?}.
  async function updateParadigm (code, patch) {
    return _replace((await api.updateParadigm(code, patch)).paradigm)
  }

  async function bind (code, agent) {
    return _replace((await api.bindParadigm(code, agent)).paradigm)
  }

  async function unbind (code, agent) {
    return _replace((await api.unbindParadigm(code, agent)).paradigm)
  }

  // Apply a reviewed rule candidate : action 'create' (dark paradigm) | 'bind' (existing).
  // A 'create' adds a paradigm → refresh the catalog too. Returns the applied result.
  async function applyPromotion (payload) {
    const r = await api.applyPromotion(payload)
    await refresh()
    return r.applied
  }

  async function dismissPromotion (conversationId, candidate) {
    const r = await api.dismissPromotion(conversationId, candidate)
    promotions.value = r.promotions || []
  }

  return {
    list, promotions, loading, error,
    refresh, updateParadigm, bind, unbind, applyPromotion, dismissPromotion,
  }
})
