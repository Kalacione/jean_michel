import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'

export const useProjectStore = defineStore('projects', () => {
  const list = ref([])
  const loading = ref(false)

  async function refresh () {
    loading.value = true
    try {
      list.value = (await api.listProjects()).projects
    } finally {
      loading.value = false
    }
  }

  async function create (project) {
    const p = (await api.createProject(project)).project
    await refresh()
    return p
  }

  async function update (id, patch) {
    const p = (await api.updateProject(id, patch)).project
    await refresh()
    return p
  }

  async function remove (id) {
    await api.deleteProject(id)
    await refresh()
  }

  function reset () {
    list.value = []
  }

  return { list, loading, refresh, create, update, remove, reset }
})
