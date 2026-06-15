<template>
  <v-dialog max-width="900" :model-value="conv.planEditorOpen" @update:model-value="close">
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon color="primary" icon="mdi-clipboard-edit-outline" /> Modifier le plan
      </v-card-title>
      <v-tabs v-model="tab" density="compact">
        <v-tab value="plan">Plan (markdown)</v-tab>
        <v-tab value="preview">Aperçu</v-tab>
        <v-tab value="steps">Étapes (suivi)</v-tab>
      </v-tabs>
      <v-card-text style="max-height: 60vh; overflow-y: auto;">
        <v-window v-model="tab">
          <!-- Rich plan document : the reasoning the human reviews/edits -->
          <v-window-item value="plan">
            <v-textarea
              v-model="planMd"
              auto-grow
              hide-details
              placeholder="# Plan&#10;&#10;## Context&#10;…&#10;&#10;## Steps&#10;…&#10;&#10;## Verification&#10;…"
              rows="16"
              variant="outlined"
            />
          </v-window-item>
          <!-- Rendered preview -->
          <v-window-item value="preview">
            <div v-if="planMd.trim()" class="markdown-body" v-html="rendered" />
            <p v-else class="text-medium-emphasis">Aucun plan rédigé.</p>
          </v-window-item>
          <!-- Terse tracker (todo.json) : progress only -->
          <v-window-item value="steps">
            <v-text-field
              v-model="goal"
              class="mb-2"
              density="compact"
              hide-details
              label="Objectif"
              variant="outlined"
            />
            <p class="text-caption text-medium-emphasis mb-1">
              Étapes — suivi d’avancement (au plus une « en cours ») :
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
          </v-window-item>
        </v-window>
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
  import { computed, ref, watch } from 'vue'
  import { api } from '@/api'
  import { renderMarkdown } from '@/markdown'
  import { useConvStore } from '@/stores/conversations'

  const STATUSES = [
    { title: 'À faire', value: 'pending' },
    { title: 'En cours', value: 'in_progress' },
    { title: 'Fait', value: 'done' },
  ]

  const conv = useConvStore()
  const tab = ref('plan')
  const planMd = ref('')
  const goal = ref('')
  const items = ref([])
  const err = ref('')
  const saving = ref(false)
  const rendered = computed(() => renderMarkdown(planMd.value))

  // Load the rich plan + the terse tracker when the editor opens.
  watch(() => conv.planEditorOpen, async open => {
    if (!open) return
    err.value = ''
    tab.value = 'plan'
    try {
      const [{ plan }, { todo }] = await Promise.all([
        api.getPlan(conv.currentId),
        api.getTodo(conv.currentId),
      ])
      planMd.value = plan || ''
      goal.value = todo?.goal || ''
      items.value = (todo?.items || []).map(it => ({ ...it }))
    } catch {
      planMd.value = ''
      goal.value = ''
      items.value = []
    }
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
    const cleanItems = items.value
      .map(it => ({ ...it, text: (it.text || '').trim() }))
      .filter(it => it.text)
    if (!planMd.value.trim() && !cleanItems.length) {
      err.value = 'Le plan doit contenir du texte ou au moins une étape.'
      return
    }
    saving.value = true
    err.value = ''
    try {
      await api.putPlan(conv.currentId, planMd.value)
      // The tracker is optional ; only persist it when there are steps.
      if (cleanItems.length) await api.putTodo(conv.currentId, goal.value.trim(), cleanItems)
      conv.plan = planMd.value.trim() || null
      close()
    } catch (e) {
      err.value = e.detail || e.message || 'Échec de l’enregistrement.'
    } finally {
      saving.value = false
    }
  }
</script>
