<template>
  <v-dialog v-model="open" max-width="960" scrollable>
    <v-card class="d-flex flex-column" height="80vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-folder-open-outline" /> Workspace
        <v-spacer />
        <v-btn icon="mdi-upload" title="Téléverser" variant="text" @click="uploadOpen = true" />
        <v-btn icon="mdi-refresh" title="Rafraîchir" variant="text" @click="load" />
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
          <div v-if="filePath" class="d-flex align-center ga-2 mb-2">
            <v-btn
              icon="mdi-download"
              size="x-small"
              title="Télécharger"
              variant="tonal"
              @click="download"
            />
            <span class="text-caption text-medium-emphasis text-truncate">{{ filePath }}</span>
          </div>
          <div v-if="error" class="text-error text-caption">{{ error }}</div>
          <pre v-else-if="filePath" class="content">{{ fileContent }}</pre>
          <div v-else class="text-medium-emphasis">Sélectionne un fichier.</div>
        </div>
      </div>
    </v-card>
  </v-dialog>

  <v-dialog v-model="uploadOpen" max-width="520">
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-upload" /> Téléverser des fichiers
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" @click="uploadOpen = false" />
      </v-card-title>
      <v-divider />
      <v-card-text>
        <v-file-upload
          v-model="pending"
          clearable
          :disabled="uploading"
          multiple
          title="Glissez vos fichiers ici"
        />
        <div class="text-caption text-medium-emphasis mt-2">Max {{ MAX_MB }} Mo par fichier.</div>
        <div
          v-for="r in uploadResults"
          :key="r.name"
          class="text-caption mt-1"
          :class="r.status === 'ok' ? 'text-success' : 'text-error'"
        >
          {{ r.status === 'ok' ? '✓' : '✗' }} {{ r.name }}
          <span v-if="r.detail">— {{ r.detail }}</span>
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          color="primary"
          :disabled="!hasPending || uploading"
          :loading="uploading"
          variant="flat"
          @click="doUpload"
        >Envoyer</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { computed, ref, watch } from 'vue'
  import { api } from '@/api'
  import { useConvStore } from '@/stores/conversations'

  const MAX_MB = 22 // affichage seul ; la limite réelle est WORKSPACE_UPLOAD_MAX_BYTES (serveur)

  const open = defineModel({ type: Boolean })
  const conv = useConvStore()

  const files = ref([])
  const filePath = ref('')
  const fileContent = ref('')
  const loading = ref(false)
  const error = ref('')

  const uploadOpen = ref(false)
  const pending = ref([])
  const uploadResults = ref([])
  const uploading = ref(false)
  const hasPending = computed(
    () => (Array.isArray(pending.value) ? pending.value.length > 0 : !!pending.value),
  )

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
    // Select first so the download button shows even when the text preview
    // fails (binary / non-UTF-8 / too large) — those are still downloadable.
    filePath.value = path
    fileContent.value = ''
    error.value = ''
    try {
      const res = await api.workspaceFile(conv.currentId, path)
      fileContent.value = res.content + (res.truncated ? '\n… (tronqué)' : '')
    } catch (e) {
      error.value = e.detail || e.message
    }
  }

  async function download () {
    if (!filePath.value) return
    const blob = await api.downloadWorkspace(conv.currentId, filePath.value)
    if (!blob) {
      error.value = 'Téléchargement impossible.'
      return
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filePath.value.split('/').pop()
    document.body.append(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  async function doUpload () {
    const list = Array.isArray(pending.value)
      ? pending.value
      : (pending.value ? [pending.value] : [])
    if (!list.length) return
    uploading.value = true
    uploadResults.value = []
    try {
      const res = await api.uploadWorkspace(conv.currentId, list)
      uploadResults.value = res.results
      await load() // refresh the tree (and any newly written files)
      if (res.results.every(r => r.status === 'ok')) {
        pending.value = []
        uploadOpen.value = false
      }
    } catch (e) {
      uploadResults.value = [{ name: '(échec)', status: 'error', detail: e.detail || e.message }]
    } finally {
      uploading.value = false
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
