<template>
  <v-dialog max-width="640" :model-value="conv.planEditorOpen" @update:model-value="close">
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon color="primary" icon="mdi-clipboard-edit-outline" /> Modifier le plan
      </v-card-title>
      <v-card-text>
        <v-text-field
          v-model="goal"
          class="mb-2"
          density="compact"
          hide-details
          label="Objectif"
          variant="outlined"
        />
        <p class="text-caption text-medium-emphasis mb-1">
          Étapes (au plus une « en cours ») :
        </p>
        <div v-for="(it, i) in items" :key="i" class="d-flex align-center ga-1 mb-1">
          <v-text-field
            v-model="it.text"
            density="compact"
            hide-details
            :placeholder="`Étape ${i + 1}`"
            variant="outlined"
          />
          <v-select
            v-model="it.status"
            density="compact"
            hide-details
            :items="STATUSES"
            style="max-width: 130px"
            variant="outlined"
          />
          <v-btn
            density="comfortable"
            :disabled="i === 0"
            icon="mdi-arrow-up"
            size="x-small"
            variant="text"
            @click="move(i, -1)"
          />
          <v-btn
            density="comfortable"
            :disabled="i === items.length - 1"
            icon="mdi-arrow-down"
            size="x-small"
            variant="text"
            @click="move(i, 1)"
          />
          <v-btn
            color="error"
            density="comfortable"
            icon="mdi-delete-outline"
            size="x-small"
            variant="text"
            @click="items.splice(i, 1)"
          />
        </div>
        <v-btn
          class="mt-1"
          prepend-icon="mdi-plus"
          size="small"
          variant="text"
          @click="items.push({ id: '', text: '', status: 'pending' })"
        >
          Ajouter une étape
        </v-btn>
        <v-alert v-if="err" class="mt-2" density="compact" :text="err" type="error" />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="close">Annuler</v-btn>
        <v-btn color="primary" :loading="saving" variant="flat" @click="save">Enregistrer</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { ref, watch } from 'vue'
  import { api } from '@/api'
  import { useConvStore } from '@/stores/conversations'

  const STATUSES = [
    { title: 'À faire', value: 'pending' },
    { title: 'En cours', value: 'in_progress' },
    { title: 'Fait', value: 'done' },
  ]

  const conv = useConvStore()
  const goal = ref('')
  const items = ref([])
  const err = ref('')
  const saving = ref(false)

  // Load the current plan when the editor opens.
  watch(() => conv.planEditorOpen, async open => {
    if (!open) return
    err.value = ''
    try {
      const { todo } = await api.getTodo(conv.currentId)
      goal.value = todo?.goal || ''
      items.value = (todo?.items || []).map(it => ({ ...it }))
    } catch {
      goal.value = ''
      items.value = []
    }
    if (!items.value.length) items.value = [{ id: '', text: '', status: 'pending' }]
  })

  function move (i, delta) {
    const j = i + delta
    if (j < 0 || j >= items.value.length) return
    const [it] = items.value.splice(i, 1)
    items.value.splice(j, 0, it)
  }

  function close () {
    conv.planEditorOpen = false
  }

  async function save () {
    const clean = items.value
      .map(it => ({ ...it, text: (it.text || '').trim() }))
      .filter(it => it.text)
    if (!clean.length) { err.value = 'Le plan doit contenir au moins une étape.'; return }
    saving.value = true
    err.value = ''
    try {
      await api.putTodo(conv.currentId, goal.value.trim(), clean)
      close()
    } catch (e) {
      err.value = e.detail || e.message || 'Échec de l’enregistrement.'
    } finally {
      saving.value = false
    }
  }
</script>
