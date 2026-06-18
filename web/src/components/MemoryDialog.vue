<template>
  <v-dialog v-model="open" max-width="980" scrollable>
    <v-card class="d-flex flex-column" height="82vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-brain" /> Mémoire
        <v-chip v-if="entries.length" size="small" variant="tonal">{{ entries.length }}</v-chip>
        <v-spacer />
        <v-btn prepend-icon="mdi-plus" variant="tonal" @click="startNew">Nouvelle</v-btn>
        <v-btn icon="mdi-refresh" variant="text" @click="load" />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-divider />

      <!-- Scope + target + search toolbar -->
      <div class="pa-3 d-flex ga-2 align-center flex-wrap">
        <v-btn-toggle v-model="browseScope" density="compact" divided mandatory variant="outlined" @update:model-value="onScopeChange">
          <v-btn v-for="s in SCOPES" :key="s" size="small" :value="s">{{ s }}</v-btn>
        </v-btn-toggle>
        <v-select
          v-if="browseScope === 'project'"
          v-model="browseProjectId"
          density="compact"
          hide-details
          item-title="name"
          item-value="id"
          :items="projects.list"
          label="Projet"
          style="max-width: 220px"
          variant="outlined"
          @update:model-value="load"
        />
        <v-text-field
          v-if="browseScope === 'tool'"
          v-model="browseToolCode"
          density="compact"
          hide-details
          label="Outil (tool_code)"
          style="max-width: 220px"
          variant="outlined"
          @keyup.enter="load"
        />
        <v-spacer />
        <v-text-field
          v-model="query"
          clearable
          density="compact"
          hide-details
          label="Recherche full-text"
          prepend-inner-icon="mdi-magnify"
          style="max-width: 320px"
          variant="outlined"
          @click:clear="load"
          @keyup.enter="search"
        />
      </div>
      <v-divider />

      <div class="d-flex flex-grow-1 overflow-hidden">
        <div class="list overflow-y-auto">
          <v-progress-linear v-if="loading" indeterminate />
          <v-list density="compact" nav>
            <v-list-item
              v-for="e in entries"
              :key="`${e.scope}/${e.code}`"
              :active="selected && selected.scope === e.scope && selected.code === e.code"
              :subtitle="e.title"
              @click="openEntry(e)"
            >
              <template #title>
                <span class="text-caption text-medium-emphasis">[{{ e.scope }}]</span> {{ e.code }}
              </template>
              <template #append>
                <v-chip v-if="e.importance" class="mr-1" size="x-small" :title="`importance ${e.importance}`" variant="tonal">
                  {{ e.importance }}
                </v-chip>
                <v-btn icon="mdi-delete-outline" size="x-small" variant="text" @click.stop="del(e)" />
              </template>
            </v-list-item>
          </v-list>
          <div v-if="!loading && !entries.length" class="text-medium-emphasis text-caption pa-3">
            Rien ici.
          </div>
        </div>
        <v-divider vertical />
        <div class="editor flex-grow-1 overflow-y-auto pa-4">
          <template v-if="mode">
            <div class="d-flex ga-2">
              <v-select
                v-model="form.scope"
                density="compact"
                :items="SCOPES"
                label="Scope"
                :readonly="mode === 'edit'"
                variant="outlined"
              />
              <v-text-field
                v-model="form.code"
                density="compact"
                label="Code"
                :readonly="mode === 'edit'"
                variant="outlined"
              />
            </div>
            <v-select
              v-if="form.scope === 'project'"
              v-model="form.project_id"
              density="compact"
              item-title="name"
              item-value="id"
              :items="projects.list"
              label="Projet"
              :readonly="mode === 'edit'"
              variant="outlined"
            />
            <v-text-field
              v-if="form.scope === 'tool'"
              v-model="form.tool_code"
              density="compact"
              label="Outil (tool_code)"
              :readonly="mode === 'edit'"
              variant="outlined"
            />
            <v-text-field v-model="form.title" counter="60" density="compact" label="Titre" variant="outlined" />
            <v-text-field v-model="form.description" counter="150" density="compact" label="Description" variant="outlined" />
            <v-textarea v-model="form.content" auto-grow counter="1000" label="Contenu" rows="8" variant="outlined" />
            <v-slider
              v-model="form.importance"
              class="mt-1 px-2"
              label="Importance"
              :max="5"
              :min="1"
              show-ticks="always"
              :step="1"
              thumb-label
            />
            <v-alert v-if="error" class="mb-2" density="compact" :text="error" type="error" />
            <div class="d-flex ga-2">
              <v-btn color="primary" :loading="saving" variant="flat" @click="save">
                {{ mode === 'new' ? 'Créer' : 'Enregistrer' }}
              </v-btn>
              <v-btn variant="text" @click="resetForm">Annuler</v-btn>
            </div>
          </template>
          <div v-else class="text-medium-emphasis">Sélectionne une entrée ou crées-en une.</div>
        </div>
      </div>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { reactive, ref, watch } from 'vue'
  import { api } from '@/api'
  import { useProjectStore } from '@/stores/projects'

  const open = defineModel({ type: Boolean })
  const SCOPES = ['user', 'project', 'tool']
  const projects = useProjectStore()

  const browseScope = ref('user')
  const browseProjectId = ref(null)
  const browseToolCode = ref('')
  const query = ref('')

  const entries = ref([])
  const selected = ref(null)
  const mode = ref('') // '' | 'new' | 'edit'
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const form = reactive({ scope: 'user', code: '', title: '', description: '', content: '', importance: 3, project_id: null, tool_code: '' })

  // The query params pinning a list/recall/delete to the current browse target.
  function browseTarget () {
    if (browseScope.value === 'project') return browseProjectId.value ? { project_id: browseProjectId.value } : null
    if (browseScope.value === 'tool') return browseToolCode.value ? { tool_code: browseToolCode.value } : null
    return {} // user / world need no target
  }

  function onScopeChange () {
    selected.value = null
    load()
  }

  async function load () {
    const target = browseTarget()
    if (target === null) { entries.value = []; return } // target not chosen yet
    loading.value = true
    error.value = ''
    try {
      entries.value = (await api.listMemory(browseScope.value, target)).entries
    } catch (e) {
      error.value = e.detail || e.message
    } finally {
      loading.value = false
    }
  }

  async function search () {
    if (!query.value) { load(); return }
    const target = browseTarget()
    if (target === null) return
    loading.value = true
    error.value = ''
    try {
      entries.value = (await api.searchMemory(query.value, { scope: browseScope.value, ...target })).results
    } catch (e) {
      error.value = e.detail || e.message
    } finally {
      loading.value = false
    }
  }

  function resetForm () {
    mode.value = ''
    selected.value = null
    error.value = ''
    Object.assign(form, {
      scope: browseScope.value, code: '', title: '', description: '', content: '', importance: 3,
      project_id: browseScope.value === 'project' ? browseProjectId.value : null,
      tool_code: browseScope.value === 'tool' ? browseToolCode.value : '',
    })
  }

  function startNew () {
    resetForm()
    mode.value = 'new'
  }

  // The target object for an entry's scope (for recall/update/delete/save).
  function entryTarget (e) {
    if (e.scope === 'project') return { project_id: e.project_id }
    if (e.scope === 'tool') return { tool_code: e.tool_code }
    return {}
  }

  async function openEntry (e) {
    error.value = ''
    try {
      const full = (await api.recallMemory(e.scope, e.code, entryTarget(e))).entry
      selected.value = full
      mode.value = 'edit'
      Object.assign(form, {
        scope: full.scope, code: full.code, title: full.title, description: full.description,
        content: full.content, importance: full.importance ?? 3,
        project_id: full.project_id, tool_code: full.tool_code || '',
      })
    } catch (err) {
      error.value = err.detail || err.message
    }
  }

  async function save () {
    saving.value = true
    error.value = ''
    try {
      if (mode.value === 'new') {
        await api.saveMemory({
          scope: form.scope, code: form.code, title: form.title,
          description: form.description, content: form.content, importance: form.importance,
          project_id: form.scope === 'project' ? form.project_id : null,
          tool_code: form.scope === 'tool' ? form.tool_code : null,
        })
      } else {
        await api.updateMemory(form.scope, form.code, {
          title: form.title, description: form.description, content: form.content,
          importance: form.importance,
          ...entryTarget(form),
        })
      }
      await load()
      resetForm()
    } catch (e) {
      error.value = e.detail || e.message
    } finally {
      saving.value = false
    }
  }

  async function del (e) {
    error.value = ''
    try {
      await api.deleteMemory(e.scope, e.code, entryTarget(e))
      if (selected.value?.scope === e.scope && selected.value?.code === e.code) resetForm()
      await load()
    } catch (err) {
      error.value = err.detail || err.message
    }
  }

  watch(open, v => { if (v) { projects.refresh(); load() } })
</script>

<style scoped>
.list { width: 340px; }
</style>
