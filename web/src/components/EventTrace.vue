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
            class="trace-row d-flex ga-2"
            :class="e.type === 'AgentThinking' ? 'align-start' : 'align-center text-caption'"
          >
            <!-- Thinking step : collapsible markdown block with a peek + triangle.
                 Only the latest step is expanded ; the rest stay peeked. -->
            <template v-if="e.type === 'AgentThinking'">
              <v-icon
                class="think-toggle"
                color="purple"
                :icon="expandedIdx === i ? 'mdi-menu-down' : 'mdi-menu-right'"
                size="16"
                @click="toggle(i)"
              />
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div
                class="think-body font-italic text-medium-emphasis flex-grow-1"
                :class="{ collapsed: expandedIdx !== i }"
                @click="toggle(i)"
                v-html="renderMarkdown(e.text)"
              />
            </template>
            <!-- Other events : compact one-liner. -->
            <template v-else>
              <v-icon :color="color(e)" :icon="icon(e)" size="14" />
              <span>{{ label(e) }}</span>
            </template>
          </div>
        </v-slide-y-transition>
      </div>
    </v-expand-transition>
  </v-card>
</template>

<script setup>
  import { computed, ref, watch } from 'vue'
  import { renderMarkdown } from '@/markdown'

  const props = defineProps({
    events: { type: Array, default: () => [] },
    dispatch: { type: Object, default: null },
    busy: Boolean,
    queued: Boolean,
  })

  // Whole-trace card : expanded while thinking, auto-collapsed once the turn
  // ends. The chevron lets the user override either way.
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

  // Which thinking step is expanded (index in rows). The latest auto-expands as
  // new steps arrive — collapsing the previous one to a peek. -1 = none expanded.
  const expandedIdx = ref(-1)
  let prevThinkCount = 0
  watch(rows, list => {
    if (!list.length) { prevThinkCount = 0; expandedIdx.value = -1; return }
    const idxs = []
    list.forEach((e, i) => { if (e.type === 'AgentThinking') idxs.push(i) })
    if (idxs.length > prevThinkCount) expandedIdx.value = idxs[idxs.length - 1]
    prevThinkCount = idxs.length
  }, { immediate: true })

  function toggle (i) {
    expandedIdx.value = expandedIdx.value === i ? -1 : i
  }

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

/* Thinking step : smaller text, markdown prose, expandable height (peek). */
.think-toggle { cursor: pointer; margin-top: 1px; }
.think-body {
  font-size: 0.74rem;
  line-height: 1.4;
  cursor: pointer;
  overflow: hidden;
  max-height: 2000px;          /* expanded : effectively unbounded */
  transition: max-height 0.25s ease;
}
.think-body.collapsed {
  max-height: 3.2em;           /* peek : ~2 lines */
}
.think-body :deep(p) { margin: 0 0 0.3em; }
.think-body :deep(p:last-child) { margin-bottom: 0; }
.think-body :deep(ul), .think-body :deep(ol) { padding-left: 1.1em; margin: 0.2em 0; }
.think-body :deep(pre) {
  overflow-x: auto;
  padding: 0.3em 0.4em;
  border-radius: 3px;
  background: rgba(127, 127, 127, 0.15);
}
.think-body :deep(code) { font-family: monospace; }
</style>
