import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'

// Agent settings (admin panel). Models all come from the backend : each agent's
// effective_model is resolved server-side (env per-agent → DB model_override → role
// default from models.toml). We only edit the per-agent override + temperature/thinking.
export const useAgentsStore = defineStore('agents', () => {
  const list = ref([])      // [{code, name, role, mission, temperature, thinking_mode, model_override, effective_model}]
  const models = ref([])    // installed Ollama model names (dropdown options)
  const loading = ref(false)
  const error = ref('')

  async function refresh () {
    loading.value = true
    error.value = ''
    try {
      const [agents, mods] = await Promise.all([
        api.listAgents().then(r => r.agents || []),
        api.listModels().then(r => r.models || []).catch(() => []),
      ])
      list.value = agents
      models.value = mods
    } catch (e) {
      error.value = e?.detail || 'Échec du chargement des agents.'
    } finally {
      loading.value = false
    }
  }

  // patch : {model_override?, temperature?, thinking_mode?}. Returns the updated agent.
  async function updateAgent (code, patch) {
    const updated = (await api.updateAgent(code, patch)).agent
    const i = list.value.findIndex(a => a.code === code)
    if (i !== -1) list.value[i] = updated
    return updated
  }

  return { list, models, loading, error, refresh, updateAgent }
})
