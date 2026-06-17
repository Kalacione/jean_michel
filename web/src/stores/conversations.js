import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'
import { connectTurn } from '@/ws'

// Transient prompt-assembly injections (TODO/repo recaps, orchestrator nudges)
// are role:user but are NOT conversation history — hide them from the chat view.
// (Backend now also strips them from messages.json; this also cleans already-
// persisted conversations on load.) Mirrors persistence._TRANSIENT_USER_PREFIXES.
const INJECTION_PREFIXES = ['[TODO-RECAP]', '[CODE-REPO]', '[ORCHESTRATOR]', '[PLAN]']
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
  const liveThinking = ref('') // current agent's thinking, streamed into the dedicated block
  const busy = ref(false) // a turn is running
  const stopping = ref(false) // Stop requested ; awaiting the turn's terminal frame
  const queued = ref(false) // waiting for the global turn slot
  const dispatch = ref(null) // last Tier-0 decision {intent, tool, confidence}
  const askHuman = ref(null) // {question, why, choices[], multi} | null
  const error = ref('')
  const vocal = ref(false) // current conversation is in vocal mode
  const wsFiles = ref([]) // workspace file paths of the current conversation
  const wsOpen = ref(false) // WorkspaceDialog open state (shared across components)
  const wsInitialPath = ref('') // file to auto-open when the WorkspaceDialog opens
  const pendingMemory = ref([]) // shadow-consolidation candidates awaiting review
  const currentMode = ref('') // task mode of the selected conversation
  const planMode = ref(false) // Plan/Edit selector value (sticky ; Plan = no mutation)
  const planPending = ref(false) // a plan turn just finished → show the Approve/Refine bar
  const plan = ref(null) // rich plan document (plan.md markdown) of the current conversation
  const planEditorOpen = ref(false) // inline plan (todo) editor dialog state
  const convState = ref(null) // the organizational REFERENT (state.json) : phase, plans, todos, files, subagents
  // Plan mode only makes sense where execution happens (code) or multi-step delegation (analyse).
  const planAvailable = computed(() => currentMode.value === 'code' || currentMode.value === 'analyse')

  let turnWs = null
  let streamingMsg = null // the assistant bubble currently being built live (token stream)

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
    currentMode.value = meta?.mode || ''
    // Plan-first : default to Plan for code & analyse (sticky thereafter).
    planMode.value = currentMode.value === 'code' || currentMode.value === 'analyse'
    planPending.value = false
    plan.value = null
    convState.value = null
    streamingMsg = null
    liveThinking.value = ''
    trace.value = []
    dispatch.value = null
    busy.value = false
    askHuman.value = null
    wsFiles.value = []
    // Load messages + the persisted (coarse) trace + plan state in PARALLEL, then
    // rehydrate : a reload / switch shows the agent's thinking & steps (events.jsonl,
    // no token deltas → cheap), not a blank panel. events/state/todo are best-effort
    // (a failure leaves the panel empty, never breaks select) ; messages stays strict.
    const [loaded, ev, st, td, pend, pl] = await Promise.all([
      api.messages(id).then(r => r.messages),
      api.events(id).then(r => r.events || []).catch(() => []),
      api.state(id).then(r => r.state || null).catch(() => null),
      api.getTodo(id).then(r => r.todo || null).catch(() => null),
      api.pendingMemory(id).then(r => r.pending_memory || []).catch(() => []),
      api.getPlan(id).then(r => r || {}).catch(() => ({})),
    ])
    if (currentId.value !== id) return // switched again mid-load → don't clobber the newer conv
    messages.value = chatBubbles(loaded)
    trace.value = ev.map(e => ({ type: 'event', event: e })) // match the live WS frame shape
    plan.value = pl.plan || null // rich plan markdown survives reload (Approve bar / editor)
    convState.value = st // the referent (phase/plans/todos/files/subagents) for the UI summary strip
    // Approve/Refine bar = a plan still awaiting approval. Source of truth: the plan-level
    // status (proposed → pending), DECOUPLED from the todo and from the (sticky) planMode
    // selector + state.plan_mode. Legacy convs (no status sidecar) fall back to the old heuristic.
    planPending.value = pl.status
      ? pl.status === 'proposed'
      : !!(st?.plan_mode && td?.items?.length)
    // An already-accepted plan → default the selector to Edit so a continuation message
    // executes/continues instead of silently re-planning (the reload re-arm bug).
    if (pl.status === 'accepted') planMode.value = false
    pendingMemory.value = pend // memory suggestions survive reload/switch (loaded from disk)
    openWs(id)
    fetchWsFiles()
  }

  function openWs (id) {
    turnWs = connectTurn(id, {
      dispatch: m => { dispatch.value = m },
      event: m => {
        const ev = m.event
        // Live token stream : two channels rendered in DIFFERENT places.
        if (ev?.type === 'AgentTokenStreamed') {
          if (ev.channel === 'thinking') {
            liveThinking.value += ev.delta || ''   // → dedicated thinking block
          } else {                                  // content → building answer bubble
            if (!streamingMsg) {
              messages.value.push({ role: 'assistant', content: '', streaming: true })
              streamingMsg = messages.value[messages.value.length - 1]
            }
            streamingMsg.content += ev.delta || ''
          }
          return
        }
        // A tool/delegation means any streamed content so far was intermediate
        // narration (not the final answer) → discard the half-built bubble.
        if (ev?.type === 'ToolCallStarted' || ev?.type === 'DelegationStarted') {
          discardStreamingBubble()
        }
        // A finished thinking step is now canonical (persisted row) → drop the live one.
        if (ev?.type === 'AgentThinking') liveThinking.value = ''
        trace.value.push(m)
        if (vocal.value && m.speak) speak(m.speak)
        // Shadow consolidation result : surface candidates for human review.
        if (ev?.type === 'MemoryConsolidationProposed') {
          pendingMemory.value = ev.candidates || []
        }
      },
      ask_human: m => { askHuman.value = { question: m.question, why: m.why, choices: m.choices || [], multi: !!m.multi } },
      queued: () => { queued.value = true },
      final: m => {
        queued.value = false
        busy.value = false
        stopping.value = false
        askHuman.value = null
        // Finalize the streamed bubble with the authoritative answer (or push fresh).
        if (streamingMsg) {
          streamingMsg.content = m.answer
          delete streamingMsg.streaming
          streamingMsg = null
        } else {
          messages.value.push({ role: 'assistant', content: m.answer })
        }
        liveThinking.value = ''
        // Surface the Approve/Refine bar ONLY if a plan was actually authored and awaits
        // approval (status 'proposed') — never on an aborted/no-plan turn (a plain
        // "was a plan turn" flag showed an EMPTY bar after a Stop). Truth = the persisted plan.
        api.getPlan(id).then(r => {
          plan.value = r.plan || null
          planPending.value = r.status === 'proposed'
        }).catch(() => { planPending.value = false })
        // Refresh the organizational referent so the summary strip reflects the turn just done
        // (phase moved, plan accepted, todo progressed, files/subagents produced).
        api.state(id).then(r => { convState.value = r.state || null }).catch(() => {})
        refresh() // re-order the list (last interaction first) + pick up auto-title
        fetchWsFiles() // surface files the agent just created as message links
        if (vocal.value) speak(m.answer)
      },
      error: m => { busy.value = false; stopping.value = false; queued.value = false; stopStreaming(); error.value = m.detail || 'Erreur orchestrateur.' },
      close: () => { busy.value = false; stopping.value = false; stopStreaming() },
    })
  }

  // Stop the live "building" indicator, keeping whatever partial text was streamed.
  function stopStreaming () {
    if (streamingMsg) { delete streamingMsg.streaming; streamingMsg = null }
    liveThinking.value = ''
  }

  // Drop the half-built answer bubble (the streamed content was intermediate narration,
  // not the final user-facing answer) so only the final output survives.
  function discardStreamingBubble () {
    if (!streamingMsg) return
    const idx = messages.value.indexOf(streamingMsg)
    if (idx !== -1 && streamingMsg.streaming) messages.value.splice(idx, 1)
    streamingMsg = null
  }

  function closeWs () {
    if (turnWs) {
      turnWs.close()
      turnWs = null
    }
  }

  function sendTurn (text, files = [], plan = undefined) {
    const clean = (text || '').trim()
    if ((!clean && !files.length) || !turnWs || busy.value) return
    const isPlan = plan === undefined ? (planAvailable.value && planMode.value) : plan
    streamingMsg = null // fresh turn → new building bubble
    liveThinking.value = ''
    messages.value.push({ role: 'user', content: clean, files: [...files] })
    trace.value = []
    dispatch.value = null
    error.value = ''
    planPending.value = false // a new turn supersedes any pending choice bar
    // Optimistic phase so the referent strip reflects THIS turn immediately (the authoritative
    // phase is refetched at `final`) : a new plan turn resets stale "Répondu", an execute turn
    // shows "En cours" right away instead of the previous turn's phase.
    if (convState.value) convState.value.phase = isPlan ? 'planning' : 'executing'
    busy.value = true
    turnWs.sendTurn(clean, files, isPlan)
  }

  // Abort the running turn. The backend stops the orchestrator (mid-stream too) and
  // concludes via {final}, which clears busy/stopping. `stopping` drives the button's
  // transient state so a second click can't spam stop frames.
  function stopTurn () {
    if (!turnWs || !busy.value || stopping.value) return
    stopping.value = true
    turnWs.sendStop()
  }

  // Approve the presented plan → execute it in a fresh Edit turn (gate OFF). The
  // selector flips to Edit (sticky) so subsequent turns keep executing.
  function approveAndExecute () {
    planMode.value = false
    // Optimistic : the human just approved → flip the active plan chip to "approuvé" immediately
    // (the authoritative state is refetched at `final`). sendTurn then sets phase → "En cours".
    const s = convState.value
    if (s?.active_plan_id && s.plans?.[s.active_plan_id]) s.plans[s.active_plan_id].approved = true
    sendTurn('Approved — execute the plan above.', [], false)
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
    convState.value = null
  }

  function dismissMemory (candidate) {
    pendingMemory.value = pendingMemory.value.filter(c => c !== candidate)
    // Persist the prune (reviewed = saved OR ignored ; accept() calls this after saving)
    // so the candidate doesn't resurrect from pending_memory.json on reload. Best-effort.
    if (currentId.value) api.dismissPendingMemory(currentId.value, candidate).catch(() => {})
  }

  // Shadow consolidation is now decoupled (runs ~after the turn) and pushes its
  // candidates over the per-user notifications WS. Surface them only for the
  // conversation currently open (the payload is per-user, not per-tab).
  function onMemoryProposed (m) {
    if (m && m.conv_id === currentId.value) pendingMemory.value = m.candidates || []
  }

  return {
    list, currentId, messages, trace, liveThinking, busy, stopping, queued, dispatch, askHuman, error, vocal,
    wsFiles, wsOpen, wsInitialPath, pendingMemory,
    currentMode, planMode, planAvailable, planPending, plan, planEditorOpen, convState,
    refresh, create, select, sendTurn, stopTurn, answer, approveAndExecute, rename, remove, reset,
    fetchWsFiles, openWorkspace, dismissMemory, onMemoryProposed,
    loadSnapshots, revert, fork, reloadCurrent,
  }
})
