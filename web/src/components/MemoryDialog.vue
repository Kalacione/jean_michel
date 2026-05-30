<template>
  <v-dialog v-model="open" max-width="960" scrollable>
    <v-card class="d-flex flex-column" height="80vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-brain" /> Mémoire utilisateur
        <v-chip v-if="entries.length" size="small" variant="tonal">{{ entries.length }}</v-chip>
        <v-spacer />
        <v-btn prepend-icon="mdi-plus" variant="tonal" @click="startNew">Nouvelle</v-btn>
        <v-btn icon="mdi-refresh" variant="text" @click="load" />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-divider />
      <div class="d-flex flex-grow-1 overflow-hidden">
        <div class="list overflow-y-auto">
          <v-progress-linear v-if="loading" indeterminate />
          <v-list density="compact" nav>
            <v-list-item
              v-for="e in entries"
              :key="`${e.type}/${e.code}`"
              :active="selected && selected.type === e.type && selected.code === e.code"
              :subtitle="e.title"
              @click="openEntry(e)"
            >
              <template #title>
                <span class="text-caption text-medium-emphasis">[{{ e.type }}]</span> {{ e.code }}
              </template>
              <template #append>
                <v-btn
                  icon="mdi-delete-outline"
                  size="x-small"
                  variant="text"
                  @click.stop="del(e)"
                />
              </template>
            </v-list-item>
          </v-list>
          <div v-if="!loading && !entries.length" class="text-medium-emphasis text-caption pa-3">
            Mémoire vide.
          </div>
        </div>
        <v-divider vertical />
        <div class="editor flex-grow-1 overflow-y-auto pa-4">
          <template v-if="mode">
            <div class="d-flex ga-2">
              <v-select
                v-model="form.type"
                density="compact"
                :items="TYPES"
                label="Type"
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
            <v-text-field v-model="form.title" counter="60" density="compact" label="Titre" variant="outlined" />
            <v-text-field v-model="form.description" counter="150" density="compact" label="Description" variant="outlined" />
            <v-textarea v-model="form.content" auto-grow counter="1000" label="Contenu" rows="8" variant="outlined" />
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

  const open = defineModel({ type: Boolean })
  const TYPES = ['user', 'feedback', 'project', 'reference']

  const entries = ref([])
  const selected = ref(null)
  const mode = ref('') // '' | 'new' | 'edit'
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const form = reactive({ type: 'user', code: '', title: '', description: '', content: '' })

  async function load () {
    loading.value = true
    error.value = ''
    try {
      entries.value = (await api.listMemory()).entries
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  function resetForm () {
    mode.value = ''
    selected.value = null
    error.value = ''
    Object.assign(form, { type: 'user', code: '', title: '', description: '', content: '' })
  }

  function startNew () {
    resetForm()
    mode.value = 'new'
  }

  async function openEntry (e) {
    error.value = ''
    try {
      const full = (await api.recallMemory(e.type, e.code)).entry
      selected.value = full
      mode.value = 'edit'
      Object.assign(form, {
        type: full.type, code: full.code, title: full.title,
        description: full.description, content: full.content,
      })
    } catch (err) {
      error.value = err.message
    }
  }

  async function save () {
    saving.value = true
    error.value = ''
    try {
      if (mode.value === 'new') {
        await api.saveMemory({ ...form })
      } else {
        await api.updateMemory(form.type, form.code, {
          title: form.title, description: form.description, content: form.content,
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
      await api.deleteMemory(e.type, e.code)
      if (selected.value?.type === e.type && selected.value?.code === e.code) resetForm()
      await load()
    } catch (err) {
      error.value = err.message
    }
  }

  watch(open, v => { if (v) load() })
</script>

<style scoped>
.list { width: 320px; }
</style>
