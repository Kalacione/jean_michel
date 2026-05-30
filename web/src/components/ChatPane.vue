<template>
  <div class="chat-pane d-flex flex-column">
    <div ref="scroller" class="messages flex-grow-1 overflow-y-auto pa-4">
      <div
        v-for="(m, i) in conv.messages"
        :key="i"
        class="mb-3 d-flex"
        :class="m.role === 'user' ? 'justify-end' : 'justify-start'"
      >
        <v-card
          class="pa-3"
          :color="m.role === 'user' ? 'primary' : undefined"
          max-width="80%"
          :variant="m.role === 'user' ? 'flat' : 'tonal'"
        >
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div v-if="m.role === 'assistant'" class="md" v-html="render(m.content)" />
          <div v-else class="user-text">{{ m.content }}</div>
        </v-card>
      </div>

      <EventTrace
        v-if="conv.busy || conv.trace.length"
        :busy="conv.busy"
        :dispatch="conv.dispatch"
        :events="conv.trace"
        :queued="conv.queued"
      />

      <v-alert
        v-if="conv.error"
        class="mt-2"
        density="compact"
        :text="conv.error"
        type="error"
      />
    </div>

    <v-divider />
    <div class="pa-3 d-flex ga-2 align-end">
      <v-textarea
        v-model="draft"
        auto-grow
        density="comfortable"
        :disabled="conv.busy"
        hide-details
        max-rows="6"
        :placeholder="conv.busy ? 'Jean-Michel travaille…' : 'Écris un message… (Entrée pour envoyer)'"
        rows="1"
        variant="outlined"
        @keydown.enter.exact.prevent="send"
      />
      <v-btn
        color="primary"
        :disabled="conv.busy || !draft.trim()"
        icon="mdi-send"
        @click="send"
      />
    </div>
  </div>
</template>

<script setup>
  import MarkdownIt from 'markdown-it'
  import { nextTick, ref, watch } from 'vue'
  import EventTrace from '@/components/EventTrace.vue'
  import { useConvStore } from '@/stores/conversations'

  const conv = useConvStore()
  const draft = ref('')
  const scroller = ref(null)
  const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

  function render (text) {
    return md.render(text || '')
  }

  function send () {
    if (!draft.value.trim() || conv.busy) return
    conv.sendTurn(draft.value)
    draft.value = ''
  }

  async function scrollDown () {
    await nextTick()
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  }

  watch(() => [conv.messages.length, conv.trace.length, conv.busy], scrollDown, { deep: true })
</script>

<style scoped>
.chat-pane {
  /* v-main already offsets the 64px app-bar ; fill the rest of the viewport. */
  height: calc(100dvh - 64px);
}
.user-text {
  white-space: pre-wrap;
}
.md :deep(p) { margin: 0 0 0.5em; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(pre) {
  overflow-x: auto;
  padding: 0.6em;
  border-radius: 4px;
  background: rgba(127, 127, 127, 0.15);
}
.md :deep(code) { font-family: monospace; }
.md :deep(ul), .md :deep(ol) { padding-left: 1.2em; }
</style>
