<template>
  <v-dialog v-model="open" max-width="1040" scrollable>
    <v-card class="d-flex flex-column" height="84vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-script-text-outline" /> Paradigmes
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-tabs v-model="tab" density="compact">
        <v-tab value="catalog">Catalogue</v-tab>
        <v-tab value="promotions">
          Promotions<span v-if="store.promotions.length" class="ms-1">({{ store.promotions.length }})</span>
        </v-tab>
      </v-tabs>
      <v-divider />
      <v-progress-linear v-if="store.loading" indeterminate />
      <v-card-text class="flex-grow-1 overflow-y-auto">
        <v-alert v-if="store.error" class="mb-3" density="compact" :text="store.error" type="error" />
        <v-window v-model="tab">
          <!-- ===== Catalogue : liste groupée (gauche) + éditeur (droite) ===== -->
          <v-window-item value="catalog">
            <v-row>
              <v-col cols="12" md="5">
                <v-text-field
                  v-model="search"
                  clearable
                  density="compact"
                  hide-details
                  placeholder="Filtrer (titre / code / contenu)…"
                  prepend-inner-icon="mdi-magnify"
                  variant="outlined"
                />
                <v-list class="mt-2" density="compact" nav>
                  <template v-for="(group, section) in grouped" :key="section">
                    <v-list-subheader>{{ section }}</v-list-subheader>
                    <v-list-item
                      v-for="p in group"
                      :key="p.code"
                      :active="p.code === selectedCode"
                      @click="select(p)"
                    >
                      <v-list-item-title :class="p.active ? '' : 'text-medium-emphasis font-italic'">
                        {{ p.title }}
                      </v-list-item-title>
                      <v-list-item-subtitle>{{ p.code }}</v-list-item-subtitle>
                      <template #append>
                        <v-icon v-if="p.is_global" color="info" icon="mdi-earth" size="14" title="global" />
                        <v-icon v-if="!p.active" color="warning" icon="mdi-eye-off-outline" size="14" title="inactif (dark)" />
                      </template>
                    </v-list-item>
                  </template>
                </v-list>
              </v-col>
              <v-col cols="12" md="7">
                <template v-if="current">
                  <div class="d-flex align-center ga-2 mb-2">
                    <span class="text-subtitle-1">{{ current.title }}</span>
                    <span class="text-caption text-medium-emphasis">
                      {{ current.section_code }}/{{ current.category_code }} · {{ current.code }}
                    </span>
                  </div>
                  <v-text-field v-model="form.title" density="compact" label="Titre" variant="outlined" />
                  <v-textarea
                    v-model="form.content"
                    auto-grow
                    counter
                    density="compact"
                    label="Contenu (injecté — anglais, agnostique modèle)"
                    rows="6"
                    variant="outlined"
                  />
                  <v-textarea
                    v-model="form.rationale"
                    auto-grow
                    density="compact"
                    label="Rationale (note dev — jamais injectée)"
                    rows="2"
                    variant="outlined"
                  />
                  <div class="d-flex align-center ga-4 flex-wrap">
                    <v-switch v-model="form.active" color="primary" density="compact" hide-details label="Actif (injecté)" />
                    <v-switch v-model="form.is_global" color="info" density="compact" hide-details label="Global (tous les agents)" />
                    <v-text-field
                      v-model.number="form.order_priority"
                      density="compact"
                      hide-details
                      label="Priorité"
                      style="max-width: 120px"
                      type="number"
                      variant="outlined"
                    />
                  </div>
                  <div class="mt-3 text-caption text-medium-emphasis">Modes (aucun coché = injecté dans tous)</div>
                  <v-chip-group v-model="form.modes" column multiple>
                    <v-chip v-for="m in ALL_MODES" :key="m" filter :text="m" :value="m" variant="outlined" />
                  </v-chip-group>
                  <div class="mt-2">
                    <div class="text-caption text-medium-emphasis mb-1">Agents bindés (hors globaux)</div>
                    <v-chip
                      v-for="a in current.agents"
                      :key="a"
                      class="ma-1"
                      closable
                      size="small"
                      @click:close="unbind(a)"
                    >
                      {{ a }}
                    </v-chip>
                    <v-autocomplete
                      v-model="bindPick"
                      class="mt-1"
                      density="compact"
                      hide-details
                      :items="bindableAgents"
                      label="Binder un agent…"
                      variant="outlined"
                      @update:model-value="onBind"
                    />
                  </div>
                  <v-alert v-if="saved" class="mt-2" density="compact" text="Enregistré." type="success" />
                  <div class="d-flex ga-2 mt-2">
                    <v-spacer />
                    <v-btn :loading="saving" color="primary" variant="flat" @click="save">Enregistrer</v-btn>
                  </div>
                </template>
                <div v-else class="text-medium-emphasis text-center pa-6">Sélectionne un paradigme.</div>
              </v-col>
            </v-row>
          </v-window-item>

          <!-- ===== Promotions : revue des candidats-règle (kind='rule') ===== -->
          <v-window-item value="promotions">
            <p class="text-caption text-medium-emphasis mb-3">
              Règles proposées (réflexion fin-de-tour / meta-analyst), en attente de validation humaine.
              « Créer » fait naître un paradigme <strong>inactif</strong> (à activer + binder ensuite).
            </p>
            <div v-if="!store.promotions.length" class="text-medium-emphasis pa-4 text-center">
              Aucune promotion en attente.
            </div>
            <v-card v-for="(promo, i) in store.promotions" :key="i" class="mb-3" variant="tonal">
              <v-card-text>
                <div class="d-flex align-center ga-2 mb-1">
                  <v-chip size="x-small" variant="flat">
                    {{ promo.candidate.section_code }}/{{ promo.candidate.category_code }}
                  </v-chip>
                  <v-spacer />
                  <v-chip v-if="promo.candidate.suggested_action === 'bind'" color="info" size="x-small">
                    un similaire existe
                  </v-chip>
                </div>
                <div class="text-subtitle-2">{{ promo.candidate.title }}</div>
                <div class="text-body-2 mt-1" style="white-space: pre-wrap">{{ promo.candidate.content }}</div>
                <v-alert
                  v-if="promo.candidate.grounding_quote"
                  class="mt-2 mb-2"
                  density="compact"
                  type="info"
                  variant="tonal"
                >
                  <span class="text-caption">source : « {{ promo.candidate.grounding_quote }} »</span>
                </v-alert>
                <div v-if="promo.candidate.existing_matches?.length" class="text-caption text-medium-emphasis mb-2">
                  Similaires : {{ promo.candidate.existing_matches.map(m => m.code).join(', ') }}
                </div>
                <v-alert v-if="promoErr[i]" class="mb-2" density="compact" :text="promoErr[i]" type="error" />
                <div class="d-flex ga-2">
                  <v-btn color="primary" :loading="promoBusy[i]" size="small" variant="flat" @click="createFrom(promo, i)">
                    Créer (inactif)
                  </v-btn>
                  <v-btn :loading="promoBusy[i]" size="small" variant="tonal" @click="openBind(promo, i)">
                    Binder l'existant…
                  </v-btn>
                  <v-spacer />
                  <v-btn :loading="promoBusy[i]" size="small" variant="text" @click="dismiss(promo, i)">Ignorer</v-btn>
                </div>
                <div v-if="bindForm.i === i" class="d-flex ga-2 mt-2 align-center">
                  <v-text-field v-model="bindForm.code" density="compact" hide-details label="Code du paradigme existant" variant="outlined" />
                  <v-text-field v-model="bindForm.agent" density="compact" hide-details label="Agent" variant="outlined" />
                  <v-btn color="primary" :loading="promoBusy[i]" size="small" variant="flat" @click="bindExisting(promo, i)">OK</v-btn>
                </div>
              </v-card-text>
            </v-card>
          </v-window-item>
        </v-window>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { computed, reactive, ref, watch } from 'vue'
  import { useAgentsStore } from '@/stores/agents'
  import { useParadigmsStore } from '@/stores/paradigms'

  const ALL_MODES = ['analyse', 'chat', 'vocal', 'code']

  const open = defineModel({ type: Boolean })
  const store = useParadigmsStore()
  const agentsStore = useAgentsStore()

  const tab = ref('catalog')
  const search = ref('')
  const selectedCode = ref(null)
  const form = reactive({ title: '', content: '', rationale: '', active: true, is_global: false, order_priority: 100, modes: [] })
  const saving = ref(false)
  const saved = ref(false)
  const bindPick = ref(null)

  const promoBusy = reactive({})
  const promoErr = reactive({})
  const bindForm = reactive({ i: -1, code: '', agent: '' })

  const current = computed(() => store.list.find(p => p.code === selectedCode.value) || null)

  const filtered = computed(() => {
    const q = (search.value || '').toLowerCase().trim()
    if (!q) return store.list
    return store.list.filter(
      p => p.title.toLowerCase().includes(q)
        || p.code.toLowerCase().includes(q)
        || (p.content || '').toLowerCase().includes(q),
    )
  })

  // Group by "section · category" for the left-hand list (subheaders).
  const grouped = computed(() => {
    const out = {}
    for (const p of filtered.value) {
      const key = `${p.section_code} · ${p.category_title || p.category_code}`
      if (!out[key]) out[key] = []
      out[key].push(p)
    }
    return out
  })

  const bindableAgents = computed(() => {
    const bound = new Set(current.value?.agents || [])
    return agentsStore.list.map(a => a.code).filter(c => !bound.has(c))
  })

  function select (p) {
    selectedCode.value = p.code
    saved.value = false
    form.title = p.title
    form.content = p.content
    form.rationale = p.rationale || ''
    form.active = p.active
    form.is_global = p.is_global
    form.order_priority = p.order_priority
    form.modes = [...(p.modes || [])]
  }

  async function save () {
    if (!current.value) return
    saving.value = true
    saved.value = false
    try {
      await store.updateParadigm(selectedCode.value, {
        title: form.title,
        content: form.content,
        rationale: form.rationale,
        active: form.active,
        is_global: form.is_global,
        order_priority: Number(form.order_priority),
        modes: [...form.modes],
      })
      saved.value = true
    } catch (e) {
      store.error = e.detail || e.message
    } finally {
      saving.value = false
    }
  }

  async function onBind (agent) {
    if (!agent || !current.value) return
    try {
      await store.bind(selectedCode.value, agent)
    } catch (e) {
      store.error = e.detail || e.message
    } finally {
      bindPick.value = null
    }
  }

  async function unbind (agent) {
    try {
      await store.unbind(selectedCode.value, agent)
    } catch (e) {
      store.error = e.detail || e.message
    }
  }

  // ---- promotions (rule candidates) ----
  async function createFrom (promo, i) {
    promoBusy[i] = true
    promoErr[i] = ''
    try {
      await store.applyPromotion({ conversation_id: promo.conversation_id, candidate: promo.candidate, action: 'create' })
    } catch (e) {
      promoErr[i] = e.detail || e.message
    } finally {
      promoBusy[i] = false
    }
  }

  function openBind (promo, i) {
    bindForm.i = i
    bindForm.code = promo.candidate.existing_matches?.[0]?.code || ''
    bindForm.agent = ''
  }

  async function bindExisting (promo, i) {
    promoBusy[i] = true
    promoErr[i] = ''
    try {
      await store.applyPromotion({
        conversation_id: promo.conversation_id,
        candidate: promo.candidate,
        action: 'bind',
        bind_agent: bindForm.agent,
        bind_to_code: bindForm.code,
      })
      bindForm.i = -1
    } catch (e) {
      promoErr[i] = e.detail || e.message
    } finally {
      promoBusy[i] = false
    }
  }

  async function dismiss (promo, i) {
    promoBusy[i] = true
    promoErr[i] = ''
    try {
      await store.dismissPromotion(promo.conversation_id, promo.candidate)
    } catch (e) {
      promoErr[i] = e.detail || e.message
    } finally {
      promoBusy[i] = false
    }
  }

  watch(open, v => {
    if (v) {
      store.refresh()
      agentsStore.refresh()
      selectedCode.value = null
      tab.value = 'catalog'
    }
  })
</script>
