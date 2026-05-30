<template>
  <div class="chat-pane d-flex flex-column">
    <div ref="scroller" class="messages flex-grow-1 overflow-y-auto pa-4">
      <template v-for="(m, i) in conv.messages" :key="i">
        <div class="mb-3 d-flex" :class="m.role === 'user' ? 'justify-end' : 'justify-start'">
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

        <!-- Single thinking block, right after the last user message → just above
             the response (or at the bottom while the turn runs). Not kept in
             history : reset every turn, absent on reload. -->
        <div
          v-if="i === lastUserIndex && (conv.busy || conv.queued || conv.trace.length)"
          class="d-flex justify-start"
        >
          <EventTrace
            :busy="conv.busy"
            :dispatch="conv.dispatch"
            :events="conv.trace"
            :queued="conv.queued"
          />
        </div>
      </template>

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
      <input ref="fileInput" class="d-none" multiple type="file" @change="onFilesPicked">
      <v-btn
        :disabled="conv.busy"
        icon="mdi-paperclip"
        title="Joindre des fichiers au workspace"
        variant="text"
        @click="fileInput.click()"
      />
      <v-btn
        color="primary"
        :disabled="conv.busy || !draft.trim()"
        icon="mdi-send"
        @click="send"
      />
    </div>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" :timeout="4000">
      {{ snackbar.text }}
    </v-snackbar>
  </div>
</template>

<script setup>
  import MarkdownIt from 'markdown-it'
  import { computed, nextTick, ref, watch } from 'vue'
  import { api } from '@/api'
  import EventTrace from '@/components/EventTrace.vue'
  import { useConvStore } from '@/stores/conversations'

  const conv = useConvStore()
  const draft = ref('')
  const scroller = ref(null)
  const fileInput = ref(null)
  const snackbar = ref({ show: false, text: '', color: 'success' })
  const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

  // Anchor for the live thinking block : the most recent user message.
  const lastUserIndex = computed(() => {
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      if (conv.messages[i].role === 'user') return i
    }
    return -1
  })

  function render (text) {
    return md.render(text || '')
  }

  function send () {
    if (!draft.value.trim() || conv.busy) return
    conv.sendTurn(draft.value)
    draft.value = ''
  }

  async function onFilesPicked (e) {
    const picked = [...e.target.files]
    e.target.value = '' // allow re-picking the same file
    if (!picked.length) return
    try {
      const res = await api.uploadWorkspace(conv.currentId, picked)
      const failed = res.results.filter(r => r.status !== 'ok')
      const ok = res.results.length - failed.length
      snackbar.value = failed.length
        ? {
          show: true,
          color: 'warning',
          text: `${ok} ajouté(s), ${failed.length} refusé(s) : ${failed.map(f => f.name).join(', ')}`,
        }
        : { show: true, color: 'success', text: `${ok} fichier(s) ajouté(s) au workspace.` }
    } catch (err) {
      snackbar.value = { show: true, color: 'error', text: err.detail || err.message }
    }
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
