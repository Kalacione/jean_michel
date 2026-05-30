<template>
  <v-dialog v-model="open" max-width="960" scrollable>
    <v-card class="d-flex flex-column" height="80vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-folder-open-outline" /> Workspace
        <v-spacer />
        <v-btn icon="mdi-refresh" variant="text" @click="load" />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-divider />
      <div class="d-flex flex-grow-1 overflow-hidden">
        <div class="files overflow-y-auto pa-2">
          <v-progress-linear v-if="loading" indeterminate />
          <v-list density="compact" nav>
            <v-list-item
              v-for="f in files"
              :key="f.path"
              :active="f.path === filePath"
              prepend-icon="mdi-file-document-outline"
              :subtitle="fmtSize(f.size)"
              :title="f.path"
              @click="openFile(f.path)"
            />
          </v-list>
          <div v-if="!loading && !files.length" class="text-medium-emphasis text-caption pa-2">
            Workspace vide.
          </div>
        </div>
        <v-divider vertical />
        <div class="viewer flex-grow-1 overflow-y-auto pa-3">
          <div v-if="error" class="text-error text-caption">{{ error }}</div>
          <template v-else-if="filePath">
            <div class="text-caption text-medium-emphasis mb-2">{{ filePath }}</div>
            <pre class="content">{{ fileContent }}</pre>
          </template>
          <div v-else class="text-medium-emphasis">Sélectionne un fichier.</div>
        </div>
      </div>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { ref, watch } from 'vue'
  import { api } from '@/api'
  import { useConvStore } from '@/stores/conversations'

  const open = defineModel({ type: Boolean })
  const conv = useConvStore()

  const files = ref([])
  const filePath = ref('')
  const fileContent = ref('')
  const loading = ref(false)
  const error = ref('')

  function flatten (entries, prefix = '') {
    const out = []
    for (const e of entries || []) {
      const p = prefix ? `${prefix}/${e.name}` : e.name
      if (e.type === 'directory') out.push(...flatten(e.children, p))
      else out.push({ path: p, size: e.size_bytes })
    }
    return out
  }

  async function load () {
    if (!conv.currentId) return
    loading.value = true
    error.value = ''
    filePath.value = ''
    fileContent.value = ''
    try {
      const res = await api.workspace(conv.currentId)
      files.value = flatten(res.entries)
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  async function openFile (path) {
    error.value = ''
    try {
      const res = await api.workspaceFile(conv.currentId, path)
      filePath.value = res.path
      fileContent.value = res.content + (res.truncated ? '\n… (tronqué)' : '')
    } catch (e) {
      error.value = e.message
    }
  }

  function fmtSize (n) {
    if (n == null) return ''
    return n < 1024 ? `${n} o` : `${(n / 1024).toFixed(1)} Ko`
  }

  watch(open, v => { if (v) load() })
</script>

<style scoped>
.files { width: 300px; }
.content { white-space: pre-wrap; word-break: break-word; font-family: monospace; font-size: 0.8rem; }
</style>
