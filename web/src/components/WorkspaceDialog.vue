<template>
  <v-dialog v-model="open" max-width="960" scrollable>
    <v-card class="d-flex flex-column" height="80vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-folder-open-outline" /> Workspace
        <v-spacer />
        <v-btn
          :disabled="!files.length"
          icon="mdi-folder-zip-outline"
          title="Tout télécharger (zip)"
          variant="text"
          @click="downloadZip"
        />
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
              :prepend-icon="f.previewable ? 'mdi-file-document-outline' : 'mdi-file-outline'"
              :subtitle="fmtSize(f.size)"
              :title="f.path"
              @click="openFile(f)"
            >
              <template #append>
                <v-btn
                  icon="mdi-download"
                  size="x-small"
                  title="Télécharger"
                  variant="text"
                  @click.stop="downloadPath(f.path)"
                />
              </template>
            </v-list-item>
          </v-list>
          <div v-if="!loading && !files.length" class="text-medium-emphasis text-caption pa-2">
            Workspace vide.
          </div>
        </div>
        <v-divider vertical />
        <div class="viewer flex-grow-1 overflow-y-auto pa-3">
          <div v-if="filePath" class="text-caption text-medium-emphasis mb-2 text-truncate">{{ filePath }}</div>
          <div v-if="error" class="text-error text-caption">{{ error }}</div>
          <pre v-else-if="filePath" class="content">{{ fileContent }}</pre>
          <div v-else class="text-medium-emphasis">
            Clique un fichier texte pour l’aperçu — sinon télécharge-le via l’icône
            <v-icon icon="mdi-download" size="14" />.
          </div>
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
  import { saveBlob } from '@/download'
  import { useConvStore } from '@/stores/conversations'

  const MAX_MB = 22 // affichage seul ; la limite réelle est WORKSPACE_UPLOAD_MAX_BYTES (serveur)

  // Extensions previewable as text. Anything else is download-only — clicking it
  // must NOT trigger a read (it would surface "File is not valid UTF-8").
  const TEXT_EXT = new Set(
    ('txt md markdown json jsonl yaml yml toml ini cfg conf csv tsv log xml html htm '
      + 'css scss sass js mjs cjs ts tsx jsx vue py sh bash zsh sql rs go c h cpp hpp '
      + 'java rb php pl r lua svg env gitignore').split(' '),
  )
  function previewable (name) {
    const dot = name.lastIndexOf('.')
    if (dot <= 0) return true // dotfile or no extension → assume text
    return TEXT_EXT.has(name.slice(dot + 1).toLowerCase())
  }

  const open = defineModel({ type: Boolean })
  const props = defineProps({ initialPath: { type: String, default: '' } })
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
      else out.push({ path: p, size: e.size_bytes, previewable: previewable(e.name) })
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

  async function openFile (f) {
    if (!f.previewable) return // binary / non-text : download-only, no preview
    filePath.value = f.path
    fileContent.value = ''
    error.value = ''
    try {
      const res = await api.workspaceFile(conv.currentId, f.path)
      fileContent.value = res.content + (res.truncated ? '\n… (tronqué)' : '')
    } catch (e) {
      error.value = e.detail || e.message
    }
  }

  function openByPath (path) {
    const f = files.value.find(x => x.path === path)
    if (f) openFile(f)
  }

  async function downloadPath (path) {
    const blob = await api.downloadWorkspace(conv.currentId, path)
    if (blob) saveBlob(blob, path.split('/').pop())
  }

  async function downloadZip () {
    const blob = await api.downloadWorkspaceZip(conv.currentId)
    if (blob) saveBlob(blob, 'workspace.zip')
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
      conv.fetchWsFiles()
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

  watch(open, async v => {
    if (!v) return
    await load()
    if (props.initialPath) openByPath(props.initialPath)
  })
</script>

<style scoped>
.files { width: 300px; }
.content { white-space: pre-wrap; word-break: break-word; font-family: monospace; font-size: 0.8rem; }
</style>
