<template>
  <v-dialog v-model="open" max-width="780" scrollable>
    <v-card class="d-flex flex-column" height="80vh">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-lightbulb-on-outline" /> Suggestions mémoire
        <v-chip v-if="conv.pendingMemory.length" size="small" variant="tonal">
          {{ conv.pendingMemory.length }}
        </v-chip>
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-divider />
      <v-card-text class="flex-grow-1 overflow-y-auto">
        <div v-if="!conv.pendingMemory.length" class="text-medium-emphasis pa-4 text-center">
          Aucune suggestion en attente.
        </div>
        <v-card
          v-for="(c, i) in conv.pendingMemory"
          :key="i"
          class="mb-3"
          :color="c.suggested_action === 'review' ? 'warning' : undefined"
          variant="tonal"
        >
          <v-card-text>
            <div class="d-flex align-center ga-2 mb-1">
              <v-chip size="x-small" variant="flat">{{ c.scope }}</v-chip>
              <span class="text-caption text-medium-emphasis">{{ targetLabel(c) }}</span>
              <v-spacer />
              <v-chip v-if="c.suggested_action === 'extend'" color="info" size="x-small">
                étend une entrée existante
              </v-chip>
              <v-chip v-else-if="c.suggested_action === 'review'" color="warning" size="x-small">
                similaire à une entrée existante
              </v-chip>
            </div>
            <v-text-field v-model="c.code" density="compact" hide-details label="Code" readonly variant="plain" />
            <v-text-field v-model="c.title" counter="60" density="compact" label="Titre" variant="outlined" />
            <v-text-field v-model="c.description" counter="150" density="compact" label="Description" variant="outlined" />
            <v-textarea v-model="c.content" auto-grow counter="1000" density="compact" label="Contenu" rows="3" variant="outlined" />
            <v-alert class="mt-1 mb-2" density="compact" type="info" variant="tonal">
              <span class="text-caption">source : « {{ c.grounding_quote }} »</span>
            </v-alert>
            <div v-if="c.existing_matches?.length" class="text-caption text-medium-emphasis mb-2">
              Similaires : {{ c.existing_matches.map(m => m.code).join(', ') }}
            </div>
            <v-alert v-if="errors[i]" class="mb-2" density="compact" :text="errors[i]" type="error" />
            <div class="d-flex ga-2">
              <v-btn color="primary" :loading="busy[i]" size="small" variant="flat" @click="accept(c, i)">
                {{ c.suggested_action === 'extend' ? 'Étendre' : 'Sauver' }}
              </v-btn>
              <v-btn
                v-if="c.suggested_action !== 'extend'"
                :loading="busy[i]"
                size="small"
                variant="tonal"
                @click="accept(c, i, true)"
              >
                Sauver comme nouveau
              </v-btn>
              <v-spacer />
              <v-btn size="small" variant="text" @click="conv.dismissMemory(c)">Ignorer</v-btn>
            </div>
          </v-card-text>
        </v-card>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { reactive } from 'vue'
  import { api } from '@/api'
  import { useConvStore } from '@/stores/conversations'

  const open = defineModel({ type: Boolean })
  const conv = useConvStore()
  const busy = reactive({})
  const errors = reactive({})

  function targetLabel (c) {
    if (c.scope === 'tool') return c.tool_code || ''
    if (c.scope === 'project') return c.project_id ? `projet #${c.project_id}` : ''
    return ''
  }

  function memTarget (c) {
    if (c.scope === 'project') return { project_id: c.project_id }
    if (c.scope === 'tool') return { tool_code: c.tool_code }
    return {}
  }

  // accept : extend the existing entry, or save a new one (forceNew bypasses extend).
  async function accept (c, i, forceNew = false) {
    busy[i] = true
    errors[i] = ''
    try {
      const fields = { title: c.title, description: c.description, content: c.content }
      if (c.suggested_action === 'extend' && !forceNew) {
        await api.updateMemory(c.scope, c.code, { ...fields, ...memTarget(c) })
      } else {
        await api.saveMemory({
          scope: c.scope, code: c.code, ...fields,
          project_id: c.scope === 'project' ? c.project_id : null,
          tool_code: c.scope === 'tool' ? c.tool_code : null,
        })
      }
      conv.dismissMemory(c)
    } catch (e) {
      errors[i] = e.detail || e.message
    } finally {
      busy[i] = false
    }
  }
</script>
