<template>
  <v-dialog v-model="open" max-width="720" scrollable>
    <v-card class="d-flex flex-column" height="70vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-folder-multiple-outline" /> Projets
        <v-chip v-if="projects.list.length" size="small" variant="tonal">
          {{ projects.list.length }}
        </v-chip>
        <v-spacer />
        <v-btn prepend-icon="mdi-plus" variant="tonal" @click="startNew">Nouveau</v-btn>
        <v-btn icon="mdi-refresh" variant="text" @click="projects.refresh()" />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-divider />
      <div class="d-flex flex-grow-1 overflow-hidden">
        <div class="list overflow-y-auto">
          <v-progress-linear v-if="projects.loading" indeterminate />
          <v-list density="compact" nav>
            <v-list-item
              v-for="p in projects.list"
              :key="p.id"
              :active="form.id === p.id"
              :subtitle="`${p.code} · ${p.status}`"
              :title="p.name"
              @click="openProject(p)"
            >
              <template #append>
                <v-btn icon="mdi-delete-outline" size="x-small" variant="text" @click.stop="del(p)" />
              </template>
            </v-list-item>
          </v-list>
          <div v-if="!projects.loading && !projects.list.length" class="text-medium-emphasis text-caption pa-3">
            Aucun projet.
          </div>
        </div>
        <v-divider vertical />
        <div class="editor flex-grow-1 overflow-y-auto pa-4">
          <template v-if="mode">
            <v-text-field
              v-model="form.code"
              density="compact"
              hint="kebab-case, unique"
              label="Code"
              :readonly="mode === 'edit'"
              variant="outlined"
            />
            <v-text-field v-model="form.name" counter="100" density="compact" label="Nom" variant="outlined" />
            <v-textarea v-model="form.description" auto-grow counter="500" label="Description" rows="3" variant="outlined" />
            <v-text-field
              v-model="form.code_repo"
              density="compact"
              hint="Mode code : chemin local ou URL SSH (vide = aucun dépôt, pas de codebase)"
              label="Dépôt de code"
              persistent-hint
              placeholder="/chemin/vers/repo  ou  git@host:org/repo.git"
              variant="outlined"
            />
            <v-select
              v-model="form.repo_kind"
              density="compact"
              :items="['local', 'ssh']"
              label="Type de dépôt"
              variant="outlined"
            />
            <v-select
              v-if="mode === 'edit'"
              v-model="form.status"
              density="compact"
              :items="['active', 'archived']"
              label="Statut"
              variant="outlined"
            />
            <v-alert v-if="error" class="mb-2" density="compact" :text="error" type="error" />
            <div class="d-flex ga-2">
              <v-btn color="primary" :loading="saving" variant="flat" @click="save">
                {{ mode === 'new' ? 'Créer' : 'Enregistrer' }}
              </v-btn>
              <v-btn variant="text" @click="resetForm">Annuler</v-btn>
            </div>
          </template>
          <div v-else class="text-medium-emphasis">Sélectionne un projet ou crées-en un.</div>
        </div>
      </div>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { reactive, ref, watch } from 'vue'
  import { useProjectStore } from '@/stores/projects'

  const open = defineModel({ type: Boolean })
  const projects = useProjectStore()

  const mode = ref('') // '' | 'new' | 'edit'
  const saving = ref(false)
  const error = ref('')
  const form = reactive({ id: null, code: '', name: '', description: '', status: 'active', code_repo: '', repo_kind: 'local' })

  function resetForm () {
    mode.value = ''
    error.value = ''
    Object.assign(form, { id: null, code: '', name: '', description: '', status: 'active', code_repo: '', repo_kind: 'local' })
  }

  function startNew () {
    resetForm()
    mode.value = 'new'
  }

  function openProject (p) {
    error.value = ''
    mode.value = 'edit'
    Object.assign(form, {
      id: p.id, code: p.code, name: p.name, description: p.description, status: p.status,
      code_repo: p.code_repo || '', repo_kind: p.repo_kind || 'local',
    })
  }

  async function save () {
    saving.value = true
    error.value = ''
    try {
      if (mode.value === 'new') {
        await projects.create({
          code: form.code, name: form.name, description: form.description,
          code_repo: form.code_repo, repo_kind: form.repo_kind,
        })
      } else {
        await projects.update(form.id, {
          name: form.name, description: form.description, status: form.status,
          code_repo: form.code_repo, repo_kind: form.repo_kind,
        })
      }
      resetForm()
    } catch (e) {
      error.value = e.detail || e.message
    } finally {
      saving.value = false
    }
  }

  async function del (p) {
    error.value = ''
    try {
      await projects.remove(p.id)
      if (form.id === p.id) resetForm()
    } catch (e) {
      error.value = e.detail || e.message
    }
  }

  watch(open, v => { if (v) projects.refresh() })
</script>

<style scoped>
.list { width: 280px; }
</style>
