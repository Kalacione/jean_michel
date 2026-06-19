<template>
  <v-dialog v-model="open" max-width="920" scrollable>
    <v-card class="d-flex flex-column" height="80vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-robot-outline" /> Réglages des agents
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-divider />
      <v-progress-linear v-if="store.loading" indeterminate />
      <v-alert v-if="store.error" class="ma-3 mb-0" density="compact" :text="store.error" type="error" />
      <p class="text-medium-emphasis text-caption px-4 pt-3 mb-0">
        Le modèle par défaut d'un rôle vit dans <code>models.toml</code>. Ici tu poses un
        <strong>override par agent</strong> (champ vide = défaut du rôle), + la température et le canal
        « thinking ».
      </p>

      <!-- Deux colonnes à défilement INDÉPENDANT (liste agents / formulaire). -->
      <div class="d-flex flex-grow-1" style="min-height: 0">
        <div class="pane-list pane-scroll pa-2" style="min-height: 0">
          <v-list density="compact" nav>
            <v-list-item
              v-for="a in store.list"
              :key="a.code"
              :active="a.code === selected"
              @click="select(a)"
            >
              <v-list-item-title>{{ a.name }}</v-list-item-title>
              <v-list-item-subtitle>
                {{ a.role }} ·
                <span :class="a.model_override ? 'text-primary' : 'text-medium-emphasis'">
                  {{ a.effective_model }}{{ a.model_override ? '' : ' (défaut)' }}
                </span>
              </v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </div>

        <v-divider vertical />

        <div class="flex-grow-1 pane-scroll pa-4" style="min-height: 0">
          <template v-if="current">
            <div class="text-subtitle-1 mb-2">
              {{ current.name }}
              <span class="text-medium-emphasis text-caption">({{ current.code }} · {{ current.role }})</span>
            </div>
            <v-combobox
              v-model="form.model_override"
              clearable
              density="compact"
              :items="store.models"
              label="Modèle (vide → défaut du rôle)"
              variant="outlined"
            />
            <v-slider
              v-model="form.temperature"
              class="mt-2"
              :label="`Température : ${Number(form.temperature).toFixed(2)}`"
              :max="1"
              :min="0"
              :step="0.05"
              thumb-label
            />
            <v-switch
              v-model="form.thinking_mode"
              color="primary"
              density="compact"
              label="Canal thinking"
            />
            <v-alert v-if="saved" class="mb-2" density="compact" text="Enregistré." type="success" />
            <div class="d-flex ga-2">
              <v-spacer />
              <v-btn :loading="saving" color="primary" variant="flat" @click="save">Enregistrer</v-btn>
            </div>
          </template>
          <div v-else class="text-medium-emphasis text-center pa-6">Sélectionne un agent.</div>
        </div>
      </div>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { computed, reactive, ref, watch } from 'vue'
  import { useAgentsStore } from '@/stores/agents'

  const open = defineModel({ type: Boolean })
  const store = useAgentsStore()
  const selected = ref(null)
  const form = reactive({ model_override: '', temperature: 0.2, thinking_mode: false })
  const saving = ref(false)
  const saved = ref(false)

  const current = computed(() => store.list.find(a => a.code === selected.value) || null)

  function select (a) {
    selected.value = a.code
    saved.value = false
    form.model_override = a.model_override || ''
    form.temperature = a.temperature
    form.thinking_mode = !!a.thinking_mode
  }

  async function save () {
    if (!current.value) return
    saving.value = true
    saved.value = false
    try {
      await store.updateAgent(selected.value, {
        model_override: form.model_override || null,
        temperature: Number(form.temperature),
        thinking_mode: form.thinking_mode,
      })
      saved.value = true
    } catch (e) {
      store.error = e.detail || e.message
    } finally {
      saving.value = false
    }
  }

  watch(open, v => { if (v) { store.refresh(); selected.value = null } })
</script>

<style scoped>
/* Liste (gauche) et formulaire (droite) défilent séparément : largeur gauche fixe + min-height:0
   sur les enfants flex pour autoriser le scroll interne plutôt qu'un scroll global de la modale. */
.pane-list { flex: 0 0 40%; }
.pane-scroll { overflow-y: auto; }
</style>
