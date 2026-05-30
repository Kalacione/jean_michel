<template>
  <v-card v-if="busy || queued || rows.length" class="trace pa-2 mb-3" max-width="80%" variant="tonal">
    <div
      class="d-flex align-center ga-2"
      :class="{ 'mb-1': open && rows.length }"
      :style="{ cursor: rows.length ? 'pointer' : 'default' }"
      @click="rows.length && (open = !open)"
    >
      <v-progress-circular v-if="busy" indeterminate size="16" width="2" />
      <v-icon v-else color="success" icon="mdi-check-circle-outline" size="16" />
      <span class="text-caption text-medium-emphasis">{{ headerLabel }}</span>
      <v-chip
        v-if="dispatch"
        :color="dispatch.intent === 'alexa' ? 'teal' : 'deep-purple'"
        size="x-small"
        variant="flat"
      >
        {{ dispatch.intent === 'alexa' ? `tier 0 · ${dispatch.tool || ''}` : 'tier 1 · deep' }}
      </v-chip>
      <v-spacer />
      <v-icon
        v-if="rows.length"
        :icon="open ? 'mdi-chevron-up' : 'mdi-chevron-down'"
        size="18"
      />
    </div>

    <v-expand-transition>
      <div v-show="open && rows.length">
        <v-slide-y-transition group tag="div">
          <div
            v-for="(e, i) in rows"
            :key="i"
            class="trace-row d-flex align-center ga-2 text-caption"
          >
            <v-icon :color="color(e)" :icon="icon(e)" size="14" />
            <span :class="{ 'font-italic text-medium-emphasis': e.type === 'AgentThinking' }">{{ label(e) }}</span>
          </div>
        </v-slide-y-transition>
      </div>
    </v-expand-transition>
  </v-card>
</template>

<script setup>
  import { computed, ref, watch } from 'vue'

  const props = defineProps({
    events: { type: Array, default: () => [] },
    dispatch: { type: Object, default: null },
    busy: Boolean,
    queued: Boolean,
  })

  // Expanded while thinking, auto-collapsed once the turn ends. The chevron
  // lets the user override either way. No history is kept — a single live block.
  const open = ref(props.busy)
  watch(() => props.busy, (now, was) => {
    if (now) open.value = true
    else if (was) open.value = false
  })

  // Unwrap the {type:'event', event:{…}} envelope ; LLM call markers stay quiet.
  const HIDDEN = new Set(['LLMCallStarted', 'LLMCallCompleted'])
  const rows = computed(() =>
    props.events.map(m => m.event).filter(e => e && !HIDDEN.has(e.type)),
  )

  const headerLabel = computed(() => {
    if (props.queued) return 'En file d’attente…'
    if (props.busy) return 'Jean-Michel réfléchit…'
    const n = rows.value.length
    return `Réflexion · ${n} étape${n > 1 ? 's' : ''}`
  })

  const ICONS = {
    RequestStarted: 'mdi-play-circle-outline',
    ToolCallStarted: 'mdi-tools',
    ToolCallCompleted: 'mdi-tools',
    DelegationStarted: 'mdi-account-arrow-right-outline',
    DelegationCompleted: 'mdi-account-check-outline',
    AgentThinking: 'mdi-thought-bubble-outline',
    HookFired: 'mdi-alert-circle-outline',
    WorkingBudgetUpdate: 'mdi-gauge',
    MemoryNearCapacity: 'mdi-alert',
    RequestCompleted: 'mdi-flag-checkered',
  }
  function icon (e) {
    return ICONS[e.type] || 'mdi-circle-small'
  }
  function color (e) {
    switch (e.type) {
      case 'AgentThinking': return 'purple'
      case 'DelegationStarted': return 'indigo'
      case 'ToolCallStarted': return 'amber-darken-2'
      case 'RequestCompleted': return 'success'
      case 'HookFired': return 'warning'
      default: return 'medium-emphasis'
    }
  }
  function label (e) {
    switch (e.type) {
      case 'RequestStarted': return `→ ${e.agent}${e.depth ? ` (depth ${e.depth})` : ''}`
      case 'ToolCallStarted': return `${e.tool_name} ${e.args_summary || ''}`.trim()
      case 'ToolCallCompleted': return `↳ ${e.tool_name}`
      case 'DelegationStarted': return `délègue → ${e.child_agent}`
      case 'DelegationCompleted': return `✓ ${e.child_agent} (${e.confidence})`
      case 'AgentThinking': return e.text
      case 'HookFired': return `${e.hook_name}: ${e.action}`
      case 'WorkingBudgetUpdate': return `compaction (${Math.round((e.ratio || 0) * 100)} %)`
      case 'MemoryNearCapacity': return `mémoire ${e.current_count}/${e.limit}`
      case 'RequestCompleted': return `réponse de ${e.agent}`
      default: return e.type
    }
  }
</script>

<style scoped>
.trace-row { padding: 1px 0; }
</style>
