<template>
  <v-dialog max-width="900" :model-value="conv.planEditorOpen" @update:model-value="close">
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon color="primary" icon="mdi-clipboard-edit-outline" /> Plan &amp; suivi
      </v-card-title>
      <v-tabs v-model="tab" density="compact">
        <v-tab value="plan">Plan (markdown)</v-tab>
        <v-tab value="preview">Aperçu</v-tab>
        <v-tab value="steps">Suivi</v-tab>
      </v-tabs>
      <v-card-text style="max-height: 60vh; overflow-y: auto;">
        <v-window v-model="tab">
          <!-- Rich plan document : the reasoning the human reviews/edits (markdown). -->
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
            <div v-if="planMd.trim()" class="md" v-html="rendered" />
            <p v-else class="text-medium-emphasis">Aucun plan rédigé.</p>
          </v-window-item>
          <!-- Terse tracker (todo.json) : READ-ONLY. Editing here would shift the item ids
               and desync the orchestrator's todo_update(item_id, …) — so it is view-only. -->
          <v-window-item value="steps">
            <p v-if="goal" class="text-body-2 mb-2"><strong>Objectif :</strong> {{ goal }}</p>
            <p class="text-caption text-medium-emphasis mb-2">
              Suivi d’avancement — lecture seule (la todo est maintenue par l’orchestrateur).
            </p>
            <v-list v-if="items.length" class="py-0" density="compact">
              <v-list-item v-for="(it, i) in items" :key="i" class="px-0">
                <template #prepend>
                  <v-icon
                    class="me-2"
                    :color="statusMeta(it.status).color"
                    :icon="statusMeta(it.status).icon"
                    size="20"
                  />
                </template>
                <v-list-item-title
                  :class="it.status === 'done' ? 'text-decoration-line-through text-medium-emphasis' : ''"
                  style="white-space: normal;"
                >
                  {{ it.id }}. {{ it.text }}
                </v-list-item-title>
              </v-list-item>
            </v-list>
            <p v-else class="text-medium-emphasis">
              Aucune todo pour l’instant — elle est créée à l’exécution, à partir du plan.
            </p>
          </v-window-item>
        </v-window>
        <v-alert v-if="err" class="mt-2" density="compact" :text="err" type="error" />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="close">Fermer</v-btn>
        <v-btn color="primary" :loading="saving" variant="flat" @click="save">Enregistrer le plan</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { computed, ref, watch } from 'vue'
  import { api } from '@/api'
  import { renderMarkdown } from '@/markdown'
  import { useConvStore } from '@/stores/conversations'

  const conv = useConvStore()
  const tab = ref('plan')
  const planMd = ref('')
  const goal = ref('')
  const items = ref([])
  const err = ref('')
  const saving = ref(false)
  const rendered = computed(() => renderMarkdown(planMd.value))

  const STATUS_META = {
    done: { icon: 'mdi-check-circle', color: 'success' },
    in_progress: { icon: 'mdi-progress-clock', color: 'primary' },
    pending: { icon: 'mdi-circle-outline', color: 'grey' },
  }
  const statusMeta = s => STATUS_META[s] || STATUS_META.pending

  // Load the rich plan (editable) + the terse tracker (read-only) when the editor opens.
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

  function close () {
    conv.planEditorOpen = false
  }

  // Only the plan markdown is persisted — the todo is read-only here (editing it would
  // shift item ids and break the orchestrator's todo_update).
  async function save () {
    saving.value = true
    err.value = ''
    try {
      await api.putPlan(conv.currentId, planMd.value)
      conv.plan = planMd.value.trim() || null
      close()
    } catch (e) {
      err.value = e.detail || e.message || 'Échec de l’enregistrement.'
    } finally {
      saving.value = false
    }
  }
</script>
