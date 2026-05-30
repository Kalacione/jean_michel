<template>
  <v-card class="trace pa-2 mb-3" max-width="80%" variant="tonal">
    <div class="d-flex align-center ga-2 mb-1">
      <v-progress-circular v-if="busy" indeterminate size="16" width="2" />
      <v-icon v-else color="success" icon="mdi-check-circle-outline" size="16" />
      <span class="text-caption text-medium-emphasis">
        {{ queued ? 'En file d’attente…' : (busy ? 'Jean-Michel travaille…' : 'Terminé') }}
      </span>
      <v-chip
        v-if="dispatch"
        :color="dispatch.intent === 'alexa' ? 'teal' : 'deep-purple'"
        size="x-small"
        variant="flat"
      >
        {{ dispatch.intent === 'alexa' ? `tier 0 · ${dispatch.tool || ''}` : 'tier 1 · deep' }}
      </v-chip>
    </div>

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
  </v-card>
</template>

<script setup>
  import { computed } from 'vue'

  const props = defineProps({
    events: { type: Array, default: () => [] },
    dispatch: { type: Object, default: null },
    busy: Boolean,
    queued: Boolean,
  })

  // Unwrap the {type:'event', event:{…}} envelope ; LLM call markers stay quiet.
  const HIDDEN = new Set(['LLMCallStarted', 'LLMCallCompleted'])
  const rows = computed(() =>
    props.events.map(m => m.event).filter(e => e && !HIDDEN.has(e.type)),
  )

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
