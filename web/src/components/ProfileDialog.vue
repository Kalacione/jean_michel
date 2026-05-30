<template>
  <v-dialog v-model="open" max-width="560" scrollable>
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-account-cog" /> Mon profil
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-divider />
      <v-card-text>
        <p class="text-medium-emphasis text-caption mb-3">
          Ces champs alimentent le contexte « ## Human » de tes conversations
          (langue, ville pour la météo/heure…). Privés à ton compte.
        </p>
        <v-text-field v-model="form.name" density="compact" label="Nom" variant="outlined" />
        <div class="d-flex ga-2">
          <v-text-field v-model="form.city" density="compact" label="Ville" variant="outlined" />
          <v-text-field v-model="form.country" density="compact" label="Pays" variant="outlined" />
        </div>
        <div class="d-flex ga-2">
          <v-text-field v-model="form.language" density="compact" label="Langue (ex. fr)" variant="outlined" />
          <v-text-field v-model="form.birthdate" density="compact" label="Naissance" variant="outlined" />
        </div>
        <v-text-field v-model="form.interests" density="compact" label="Centres d'intérêt" variant="outlined" />
        <v-textarea v-model="form.notes" auto-grow label="Notes" rows="3" variant="outlined" />
        <v-alert v-if="error" class="mb-2" density="compact" :text="error" type="error" />
        <v-alert v-if="saved" class="mb-2" density="compact" text="Profil enregistré." type="success" />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn color="primary" :loading="saving" variant="flat" @click="save">Enregistrer</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { reactive, ref, watch } from 'vue'
  import { api } from '@/api'

  const open = defineModel({ type: Boolean })
  const FIELDS = ['name', 'birthdate', 'city', 'country', 'language', 'interests', 'notes']
  const form = reactive(Object.fromEntries(FIELDS.map(f => [f, ''])))
  const saving = ref(false)
  const saved = ref(false)
  const error = ref('')

  async function load () {
    error.value = ''
    saved.value = false
    try {
      const { profile } = await api.getProfile()
      for (const f of FIELDS) form[f] = profile[f] || ''
    } catch (e) {
      error.value = e.message
    }
  }

  async function save () {
    saving.value = true
    error.value = ''
    saved.value = false
    try {
      const { profile } = await api.updateProfile({ ...form })
      for (const f of FIELDS) form[f] = profile[f] || ''
      saved.value = true
    } catch (e) {
      error.value = e.detail || e.message
    } finally {
      saving.value = false
    }
  }

  watch(open, v => { if (v) load() })
</script>
