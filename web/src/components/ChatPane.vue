<template>
  <div class="chat-pane d-flex flex-column">
    <div ref="scroller" class="messages flex-grow-1 overflow-y-auto pa-4">
      <template v-for="(m, i) in conv.messages" :key="i">
        <div
          v-if="renderable(m)"
          class="mb-3 d-flex"
          :class="m.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <div class="msg-col d-flex flex-column" :class="m.role === 'user' ? 'align-end' : 'align-start'">
            <v-card
              class="pa-3"
              :color="m.role === 'user' ? 'primary' : undefined"
              :variant="m.role === 'user' ? 'flat' : 'tonal'"
            >
              <template v-if="m.role === 'assistant'">
                <!-- eslint-disable-next-line vue/no-v-html -->
                <div
                  v-if="stripImages(m.content)"
                  class="md"
                  @click="onMdClick"
                  @error.capture="onImgError"
                  v-html="renderMarkdown(stripImages(m.content))"
                />
                <!-- Remote images → Vuetify image grid (tiles + wrap). -->
                <v-row v-if="imagesOf(m.content).length" class="ma-0 mt-1" dense>
                  <v-col
                    v-for="(img, k) in imagesOf(m.content)"
                    :key="k"
                    class="pa-1"
                    cols="6"
                    sm="4"
                  >
                    <v-img
                      :alt="img.alt"
                      aspect-ratio="1"
                      class="rounded clickable bg-grey-lighten-2"
                      cover
                      :src="img.url"
                      width="100%"
                      @click="openLightbox(img.url)"
                    >
                      <template #placeholder>
                        <div class="d-flex align-center justify-center fill-height">
                          <v-progress-circular color="grey-lighten-4" indeterminate size="24" />
                        </div>
                      </template>
                      <template #error>
                        <div class="d-flex align-center justify-center fill-height text-disabled">
                          <v-icon icon="mdi-image-off-outline" />
                        </div>
                      </template>
                    </v-img>
                  </v-col>
                </v-row>
              </template>
              <div v-else class="user-text">{{ m.content }}</div>
            </v-card>

            <!-- Per-turn snapshot menu : rewind / fork from this point. Shown
                 only when a git snapshot exists for this assistant turn. -->
            <v-menu v-if="m.role === 'assistant' && snapshotForRow(i)" location="bottom start">
              <template #activator="{ props: menu }">
                <v-btn
                  v-bind="menu"
                  class="snap-btn mt-1"
                  density="comfortable"
                  icon="mdi-dots-horizontal"
                  size="x-small"
                  title="Snapshot de ce tour"
                  variant="text"
                />
              </template>
              <v-list density="compact">
                <v-list-item
                  prepend-icon="mdi-history"
                  title="Revenir à ce point"
                  @click="askRevert(snapshotForRow(i))"
                />
                <v-list-item
                  prepend-icon="mdi-source-branch"
                  title="Nouvelle conversation à partir d'ici"
                  @click="askFork(snapshotForRow(i))"
                />
              </v-list>
            </v-menu>

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
            :live-thinking="conv.liveThinking"
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

    <!-- Plan presented → approve (execute in a fresh Edit turn) or type feedback to refine. -->
    <div v-if="conv.planPending" class="px-3 pt-2 d-flex align-center ga-2">
      <v-icon color="primary" icon="mdi-clipboard-check-outline" size="18" />
      <span class="text-body-2 text-medium-emphasis">
        Plan prêt — relis-le ci-dessus, ou écris un retour pour l'affiner.
      </span>
      <v-spacer />
      <v-btn
        :disabled="conv.busy"
        size="small"
        variant="text"
        @click="conv.planEditorOpen = true"
      >
        Modifier le plan
      </v-btn>
      <v-btn
        color="primary"
        :disabled="conv.busy"
        size="small"
        variant="flat"
        @click="conv.approveAndExecute()"
      >
        Approuver &amp; exécuter
      </v-btn>
    </div>

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
      <!-- Plan/Edit selector (code & analyse only) : Plan = read-only, produce a plan
           for approval ; Edit = normal execution. Sticky between turns. -->
      <v-btn-toggle
        v-if="conv.planAvailable"
        v-model="planSel"
        class="align-self-center"
        density="comfortable"
        :disabled="conv.busy"
        divided
        mandatory
        variant="outlined"
      >
        <v-btn size="small" value="plan" title="Planifier (lecture seule, validation avant exécution)">
          <v-icon icon="mdi-clipboard-text-outline" size="16" start /> Plan
        </v-btn>
        <v-btn size="small" value="edit" title="Exécuter directement">
          <v-icon icon="mdi-pencil-outline" size="16" start /> Edit
        </v-btn>
      </v-btn-toggle>
      <v-btn
        v-if="conv.busy"
        color="error"
        :loading="conv.stopping"
        icon="mdi-stop"
        title="Arrêter le tour en cours"
        @click="conv.stopTurn()"
      />
      <v-btn
        v-else
        color="primary"
        :disabled="!draft.trim() && !attachments.length"
        icon="mdi-send"
        @click="send"
      />
    </div>

    <PlanEditor />

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" :timeout="4000">
      {{ snackbar.text }}
    </v-snackbar>

    <v-dialog v-model="lightbox.open" max-width="80vw">
      <v-img contain max-height="80vh" :src="lightbox.src" @click="lightbox.open = false" />
    </v-dialog>

    <v-dialog v-model="confirm.open" max-width="460">
      <v-card>
        <v-card-title class="text-body-1">{{ confirm.title }}</v-card-title>
        <v-card-text class="text-body-2 text-medium-emphasis">{{ confirm.text }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="confirm.open = false">Annuler</v-btn>
          <v-btn :color="confirm.color" variant="flat" @click="runConfirm">{{ confirm.cta }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
  import { computed, nextTick, ref, watch } from 'vue'
  import { api } from '@/api'
  import EventTrace from '@/components/EventTrace.vue'
  import PlanEditor from '@/components/PlanEditor.vue'
  import WorkspaceImage from '@/components/WorkspaceImage.vue'
  import { saveBlob } from '@/download'
  import { renderMarkdown } from '@/markdown'
  import { useConvStore } from '@/stores/conversations'

  const conv = useConvStore()
  const draft = ref('')

  // Plan/Edit selector ↔ store (sticky). 'plan' = read-only planning turn.
  const planSel = computed({
    get: () => (conv.planMode ? 'plan' : 'edit'),
    set: v => { conv.planMode = v === 'plan' },
  })
  const attachments = ref([]) // workspace paths attached to the next message
  const scroller = ref(null)
  const fileInput = ref(null)
  const snackbar = ref({ show: false, text: '', color: 'success' })

  // ---- per-turn git snapshots (revert / fork) ----------------------------
  // Snapshots come back oldest→newest ; snapshots[0] is the empty "init"
  // commit, so the k-th assistant bubble maps to snapshots[k]. ALEXA turns
  // write nothing → no commit, so the persisted bubbles line up 1:1 with the
  // turn commits.
  const snapshots = ref([])
  async function refreshSnapshots () {
    try { snapshots.value = await conv.loadSnapshots() } catch { snapshots.value = [] }
  }
  const snapshotByRow = computed(() => {
    const out = {}
    let k = 0
    conv.messages.forEach((m, i) => {
      if (m.role === 'assistant') { k += 1; out[i] = snapshots.value[k] || null }
    })
    return out
  })
  function snapshotForRow (i) {
    return snapshotByRow.value[i] || null
  }
  watch(() => conv.currentId, refreshSnapshots, { immediate: true })
  watch(() => conv.busy, now => { if (!now) refreshSnapshots() })

  const confirm = ref({ open: false, kind: '', commit: '', title: '', text: '', cta: '', color: 'primary' })
  function askRevert (snap) {
    if (!snap) return
    confirm.value = {
      open: true, kind: 'revert', commit: snap.commit,
      title: 'Revenir à ce point ?',
      text: 'La conversation sera rembobinée à ce tour. Les tours suivants seront supprimés (récupérables via git reflog).',
      cta: 'Revenir', color: 'warning',
    }
  }
  function askFork (snap) {
    if (!snap) return
    confirm.value = {
      open: true, kind: 'fork', commit: snap.commit,
      title: 'Nouvelle conversation à partir d’ici ?',
      text: 'Crée une nouvelle conversation avec le contenu de ce point. L’originale reste intacte.',
      cta: 'Créer', color: 'primary',
    }
  }
  async function runConfirm () {
    const { kind, commit } = confirm.value
    confirm.value.open = false
    try {
      if (kind === 'revert') {
        await conv.revert(commit)
        snackbar.value = { show: true, color: 'success', text: 'Conversation rembobinée.' }
      } else {
        await conv.fork(commit)
        snackbar.value = { show: true, color: 'success', text: 'Nouvelle conversation créée.' }
      }
      await refreshSnapshots()
    } catch (err) {
      snackbar.value = { show: true, color: 'error', text: err.detail || err.message || 'Échec.' }
    }
  }

  // Anchor for the live thinking block : the most recent user message.
  const lastUserIndex = computed(() => {
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      if (conv.messages[i].role === 'user') return i
    }
    return -1
  })

  // Remote images the agent embeds (Markdown `![](http…)`) are pulled out and
  // shown as a Vuetify image grid ; the rest of the message renders as Markdown.
  const IMG_MD = /!\[([^\]]*)\]\((https?:\/\/[^)\s]+?)(?:\s+"[^"]*")?\)/g
  function imagesOf (content) {
    const out = []
    IMG_MD.lastIndex = 0
    let m
    while ((m = IMG_MD.exec(content || '')) !== null) out.push({ alt: m[1], url: m[2] })
    return out
  }
  function stripImages (content) {
    return (content || '')
      .replace(IMG_MD, '')
      .replace(/^[ \t]*[-*][ \t]*$/gm, '') // drop bullets left empty by image removal
      .replace(/\n{3,}/g, '\n\n')
      .trim()
  }

  const lightbox = ref({ open: false, src: '' })
  function openLightbox (src) {
    lightbox.value = { open: true, src }
  }
  // Stray non-grid images (e.g. a relative path) still render via v-html.
  function onMdClick (e) {
    if (e.target && e.target.tagName === 'IMG') openLightbox(e.target.currentSrc || e.target.src)
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

  // Skip empty assistant bubbles (a content-less turn, or a reloaded
  // conversation with an empty assistant message) — they leave blank cards.
  function renderable (m) {
    if (m.role === 'user') return true
    return Boolean(stripImages(m.content)) || imagesOf(m.content).length > 0 || messageFiles(m).length > 0
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
.clickable { cursor: zoom-in; }
/* Per-turn snapshot menu : discreet until hovered. */
.snap-btn { opacity: 0.35; transition: opacity 0.15s ease; }
.snap-btn:hover { opacity: 1; }
</style>
