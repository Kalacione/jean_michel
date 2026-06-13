import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'
import { connectTurn } from '@/ws'

// Transient prompt-assembly injections (TODO/repo recaps, orchestrator nudges)
// are role:user but are NOT conversation history — hide them from the chat view.
// (Backend now also strips them from messages.json; this also cleans already-
// persisted conversations on load.) Mirrors persistence._TRANSIENT_USER_PREFIXES.
const INJECTION_PREFIXES = ['[TODO-RECAP]', '[CODE-REPO]', '[ORCHESTRATOR]']
const isInjection = m =>
  m.role === 'user' && typeof m.content === 'string' &&
  INJECTION_PREFIXES.some(p => m.content.trimStart().startsWith(p))
const chatBubbles = loaded =>
  loaded.filter(m => (m.role === 'user' || m.role === 'assistant') && !isInjection(m))

export const useConvStore = defineStore('conversations', () => {
  const list = ref([])
  const currentId = ref(null)
  const messages = ref([]) // chat bubbles : {role:'user'|'assistant', content}
  const trace = ref([]) // live event stream of the current/last turn
  const busy = ref(false) // a turn is running
  const queued = ref(false) // waiting for the global turn slot
  const dispatch = ref(null) // last Tier-0 decision {intent, tool, confidence}
  const askHuman = ref(null) // {question, why} | null
  const error = ref('')
  const vocal = ref(false) // current conversation is in vocal mode
  const wsFiles = ref([]) // workspace file paths of the current conversation
  const wsOpen = ref(false) // WorkspaceDialog open state (shared across components)
  const wsInitialPath = ref('') // file to auto-open when the WorkspaceDialog opens
  const pendingMemory = ref([]) // shadow-consolidation candidates awaiting review

  let turnWs = null

  // ---- audio queue (vocal mode) : play TTS clips one at a time -----------
  const audioQueue = []
  let audioPlaying = false

  function playBlob (blob) {
    return new Promise(resolve => {
      const audio = new Audio(URL.createObjectURL(blob))
      audio.onended = audio.onerror = resolve
      audio.play().catch(resolve)
    })
  }
  async function drainAudio () {
    audioPlaying = true
    while (audioQueue.length) {
      const text = audioQueue.shift()
      try {
        const blob = await api.tts(text)
        if (blob) await playBlob(blob)
      } catch { /* audio is best-effort */ }
    }
    audioPlaying = false
  }
  function speak (text) {
    if (!text) return
    audioQueue.push(text)
    if (!audioPlaying) drainAudio()
  }

  // ---- conversation lifecycle --------------------------------------------
  async function refresh () {
    list.value = (await api.listConversations()).conversations
  }

  function flattenPaths (entries, prefix = '') {
    const out = []
    for (const e of entries || []) {
      const p = prefix ? `${prefix}/${e.name}` : e.name
      if (e.type === 'directory') out.push(...flattenPaths(e.children, p))
      else out.push(p)
    }
    return out
  }

  async function fetchWsFiles () {
    if (!currentId.value) { wsFiles.value = []; return }
    try {
      wsFiles.value = flattenPaths((await api.workspace(currentId.value)).entries)
    } catch { wsFiles.value = [] }
  }

  // Open the WorkspaceDialog (optionally on a specific file) from anywhere.
  function openWorkspace (path = '') {
    wsInitialPath.value = path
    wsOpen.value = true
  }

  async function create (mode, projectId = null) {
    const c = await api.createConversation(mode, projectId)
    await refresh()
    await select(c.id)
    return c
  }

  async function select (id) {
    if (currentId.value === id) return
    closeWs()
    currentId.value = id
    error.value = ''
    pendingMemory.value = []
    const meta = list.value.find(c => c.id === id)
    vocal.value = meta?.mode === 'vocal'
    const loaded = (await api.messages(id)).messages
    messages.value = chatBubbles(loaded)
    trace.value = []
    dispatch.value = null
    busy.value = false
    askHuman.value = null
    wsFiles.value = []
    openWs(id)
    fetchWsFiles()
  }

  function openWs (id) {
    turnWs = connectTurn(id, {
      dispatch: m => { dispatch.value = m },
      event: m => {
        trace.value.push(m)
        if (vocal.value && m.speak) speak(m.speak)
        // Shadow consolidation result : surface candidates for human review.
        if (m.event?.type === 'MemoryConsolidationProposed') {
          pendingMemory.value = m.event.candidates || []
        }
      },
      ask_human: m => { askHuman.value = { question: m.question, why: m.why } },
      queued: () => { queued.value = true },
      final: m => {
        queued.value = false
        busy.value = false
        askHuman.value = null
        messages.value.push({ role: 'assistant', content: m.answer })
        refresh() // re-order the list (last interaction first) + pick up auto-title
        fetchWsFiles() // surface files the agent just created as message links
        if (vocal.value) speak(m.answer)
      },
      error: m => { busy.value = false; queued.value = false; error.value = m.detail || 'Erreur orchestrateur.' },
      close: () => { busy.value = false },
    })
  }

  function closeWs () {
    if (turnWs) {
      turnWs.close()
      turnWs = null
    }
  }

  function sendTurn (text, files = []) {
    const clean = (text || '').trim()
    if ((!clean && !files.length) || !turnWs || busy.value) return
    messages.value.push({ role: 'user', content: clean, files: [...files] })
    trace.value = []
    dispatch.value = null
    error.value = ''
    busy.value = true
    turnWs.sendTurn(clean, files)
  }

  function answer (text) {
    if (!turnWs) return
    askHuman.value = null
    turnWs.sendAnswer((text || '').trim())
  }

  async function rename (id, title) {
    await api.renameConversation(id, title)
    await refresh()
  }

  async function remove (id) {
    await api.deleteConversation(id)
    if (id === currentId.value) {
      closeWs()
      currentId.value = null
      messages.value = []
      trace.value = []
      dispatch.value = null
      busy.value = false
      askHuman.value = null
    }
    await refresh()
  }

  // ---- conversation snapshots (git per conversation) ---------------------

  // Reload the current conversation's messages from disk WITHOUT select()'s
  // same-id early-return (needed after a revert rewrites messages.json).
  async function reloadCurrent () {
    if (!currentId.value) return
    const loaded = (await api.messages(currentId.value)).messages
    messages.value = chatBubbles(loaded)
    trace.value = []
    dispatch.value = null
    await fetchWsFiles()
  }

  async function loadSnapshots () {
    if (!currentId.value) return []
    return (await api.snapshots(currentId.value)).snapshots
  }

  async function revert (commit) {
    if (!currentId.value) return
    await api.revertConversation(currentId.value, commit)
    await reloadCurrent()
  }

  async function fork (commit) {
    if (!currentId.value) return null
    const c = await api.forkConversation(currentId.value, commit)
    await refresh()
    await select(c.id)
    return c
  }

  function reset () {
    closeWs()
    list.value = []
    currentId.value = null
    messages.value = []
    trace.value = []
    busy.value = false
    askHuman.value = null
  }

  function dismissMemory (candidate) {
    pendingMemory.value = pendingMemory.value.filter(c => c !== candidate)
  }

  return {
    list, currentId, messages, trace, busy, queued, dispatch, askHuman, error, vocal,
    wsFiles, wsOpen, wsInitialPath, pendingMemory,
    refresh, create, select, sendTurn, answer, rename, remove, reset,
    fetchWsFiles, openWorkspace, dismissMemory,
    loadSnapshots, revert, fork, reloadCurrent,
  }
})
