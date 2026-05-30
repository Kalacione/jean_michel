<template>
  <div class="chat-pane d-flex flex-column">
    <div ref="scroller" class="messages flex-grow-1 overflow-y-auto pa-4">
      <template v-for="(m, i) in conv.messages" :key="i">
        <div class="mb-3 d-flex" :class="m.role === 'user' ? 'justify-end' : 'justify-start'">
          <div class="msg-col d-flex flex-column" :class="m.role === 'user' ? 'align-end' : 'align-start'">
            <v-card
              class="pa-3"
              :color="m.role === 'user' ? 'primary' : undefined"
              :variant="m.role === 'user' ? 'flat' : 'tonal'"
            >
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div
                v-if="m.role === 'assistant'"
                class="md"
                @click="onMdClick"
                @error.capture="onImgError"
                v-html="render(m.content)"
              />
              <div v-else class="user-text">{{ m.content }}</div>
            </v-card>

            <!-- Workspace files attached to / referenced by this message :
                 images render as thumbnails, everything else as a chip. -->
            <div v-if="messageFiles(m).length" class="d-flex flex-wrap ga-2 mt-1">
              <template v-for="p in messageFiles(m)" :key="p">
                <WorkspaceImage v-if="isImage(p)" :path="p" />
                <v-chip
                  v-else
                  size="small"
                  title="Aperçu"
                  variant="tonal"
                  @click="conv.openWorkspace(p)"
                >
                  <v-icon icon="mdi-file-document-outline" size="14" start />
                  {{ basename(p) }}
                  <v-icon end icon="mdi-download" size="14" title="Télécharger" @click.stop="downloadFile(p)" />
                </v-chip>
              </template>
            </div>
          </div>
        </div>

        <!-- Single thinking block, right after the last user message. -->
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

    <div v-if="attachments.length" class="px-3 pt-2 d-flex flex-wrap ga-1">
      <v-chip
        v-for="p in attachments"
        :key="p"
        closable
        size="small"
        @click:close="detach(p)"
      >
        <v-icon icon="mdi-paperclip" size="14" start /> {{ basename(p) }}
      </v-chip>
    </div>

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
      <v-menu :close-on-content-click="false" location="top">
        <template #activator="{ props: menu }">
          <v-btn
            v-bind="menu"
            :disabled="conv.busy"
            icon="mdi-paperclip"
            title="Joindre un fichier"
            variant="text"
          />
        </template>
        <v-card max-width="380" min-width="260">
          <v-list class="overflow-y-auto" density="compact" max-height="300">
            <v-list-subheader>Fichiers du workspace</v-list-subheader>
            <v-list-item v-for="p in conv.wsFiles" :key="p" :title="p" @click="toggleAttach(p)">
              <template #prepend>
                <v-icon
                  :icon="attachments.includes(p) ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline'"
                  size="18"
                />
              </template>
            </v-list-item>
            <v-list-item
              v-if="!conv.wsFiles.length"
              class="text-medium-emphasis text-caption"
              title="Workspace vide"
            />
            <v-divider />
            <v-list-item prepend-icon="mdi-plus" title="Téléverser un fichier" @click="fileInput.click()" />
          </v-list>
        </v-card>
      </v-menu>
      <v-btn
        color="primary"
        :disabled="conv.busy || (!draft.trim() && !attachments.length)"
        icon="mdi-send"
        @click="send"
      />
    </div>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" :timeout="4000">
      {{ snackbar.text }}
    </v-snackbar>

    <v-dialog v-model="lightbox.open" max-width="92vw">
      <v-img contain max-height="86vh" :src="lightbox.src" @click="lightbox.open = false" />
    </v-dialog>
  </div>
</template>

<script setup>
  import MarkdownIt from 'markdown-it'
  import { computed, nextTick, ref, watch } from 'vue'
  import { api } from '@/api'
  import EventTrace from '@/components/EventTrace.vue'
  import WorkspaceImage from '@/components/WorkspaceImage.vue'
  import { saveBlob } from '@/download'
  import { useConvStore } from '@/stores/conversations'

  const conv = useConvStore()
  const draft = ref('')
  const attachments = ref([]) // workspace paths attached to the next message
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

  // Inline images the agent embeds (Markdown `![](url)`) → clickable thumbnails
  // (lightbox) ; broken hotlinked images are hidden rather than shown busted.
  const lightbox = ref({ open: false, src: '' })
  function onMdClick (e) {
    if (e.target && e.target.tagName === 'IMG') {
      lightbox.value = { open: true, src: e.target.currentSrc || e.target.src }
    }
  }
  function onImgError (e) {
    if (e.target && e.target.tagName === 'IMG') e.target.classList.add('img-broken')
  }

  function basename (p) {
    return p.split('/').pop()
  }

  const IMG_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'bmp', 'tif', 'tiff', 'webp', 'svg'])
  function isImage (p) {
    return IMG_EXT.has((p.split('.').pop() || '').toLowerCase())
  }

  // Files shown as preview/download chips under a message : the ones explicitly
  // attached (live), plus any workspace file whose name appears in the text (so
  // attachments survive a reload and the assistant's file mentions get links).
  function messageFiles (m) {
    const explicit = m.files || []
    const mentioned = conv.wsFiles.filter(
      p => m.content && (m.content.includes(p) || m.content.includes(basename(p))),
    )
    return [...new Set([...explicit, ...mentioned])]
  }

  function toggleAttach (p) {
    const i = attachments.value.indexOf(p)
    if (i === -1) attachments.value.push(p)
    else attachments.value.splice(i, 1)
  }

  function detach (p) {
    attachments.value = attachments.value.filter(x => x !== p)
  }

  function send () {
    if ((!draft.value.trim() && !attachments.value.length) || conv.busy) return
    conv.sendTurn(draft.value, attachments.value)
    draft.value = ''
    attachments.value = []
  }

  async function downloadFile (p) {
    const blob = await api.downloadWorkspace(conv.currentId, p)
    if (blob) saveBlob(blob, basename(p))
  }

  async function onFilesPicked (e) {
    const picked = [...e.target.files]
    e.target.value = '' // allow re-picking the same file
    if (!picked.length) return
    try {
      const res = await api.uploadWorkspace(conv.currentId, picked)
      const ok = res.results.filter(r => r.status === 'ok')
      const failed = res.results.filter(r => r.status !== 'ok')
      // Upload from the message zone attaches the file directly.
      for (const r of ok) if (!attachments.value.includes(r.name)) attachments.value.push(r.name)
      await conv.fetchWsFiles()
      snackbar.value = failed.length
        ? {
          show: true,
          color: 'warning',
          text: `${ok.length} joint(s), ${failed.length} refusé(s) : ${failed.map(f => f.name).join(', ')}`,
        }
        : { show: true, color: 'success', text: `${ok.length} fichier(s) ajouté(s) et joint(s).` }
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
.msg-col { max-width: 80%; }
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
.md :deep(img) {
  display: inline-block;
  max-width: 256px;
  max-height: 256px;
  border-radius: 6px;
  cursor: zoom-in;
  margin: 0 8px 8px 0;
  vertical-align: top;
}
.md :deep(img.img-broken) { display: none; }
/* Inline image results tile horizontally and wrap instead of stacking,
   whatever block wrapper Markdown produced (paragraphs, list items, <br>). */
.md :deep(p:has(> img)) { display: inline-block; margin: 0; }
.md :deep(li:has(> img)) { display: inline-block; list-style: none; }
.md :deep(ul:has(img)), .md :deep(ol:has(img)) { padding-left: 0; margin: 0.25em 0; }
.md :deep(p:has(> img) br) { display: none; }
</style>
