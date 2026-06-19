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
  const planTurn = ref(false) // is the RUNNING turn a plan turn (isPlan at send) → suppress streaming the plan draft as a chat bubble
  const plan = ref(null) // rich plan document (plan.md markdown) of the current conversation
  const planEditorOpen = ref(false) // inline plan (todo) editor dialog state
  const convState = ref(null) // the organizational REFERENT (state.json) : phase, plans, todos, files, subagents
  const todo = ref(null) // full todo tracker (todo.json : {goal, items[{id,text,status}]}) — feeds the progress chip's step tooltip
  // The Approve/Refine bar is a PURE PROJECTION of the authoritative referent (live via ReferentSnapshot,
  // reloaded on select) — not a separate getPlan/status flag that could desync. phase=awaiting_approval ⇒ show it.
  const awaitingApproval = computed(() => convState.value?.phase === 'awaiting_approval')
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
    plan.value = null
    convState.value = null
    todo.value = null
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
    plan.value = pl.plan || null // rich plan markdown survives reload (shown in the Approve bar / editor)
    convState.value = st // the referent (phase/plans/todos/files/subagents) — drives the chips AND the Approve bar
    todo.value = td // full tracker (todo.json) — the progress chip tooltip lists the steps
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
          } else if (!planTurn.value) {             // content → building answer bubble
            // A PLAN turn narrates the plan in `content` before plan_write ; it belongs in the
            // Approve-bar cartouche, not a chat response that streams then gets discarded. Drop
            // those deltas (the plan is captured by plan_write). Thinking still streams above ;
            // normal / edit turns stream their answer here as before.
            if (!streamingMsg) {
              messages.value.push({ role: 'assistant', content: '', streaming: true })
              streamingMsg = messages.value[messages.value.length - 1]
            }
            streamingMsg.content += ev.delta || ''
          }
          return
        }
        // Live referent snapshot (state.json) → replace the summary-strip source WHOLESALE.
        // Authoritative : the backend pushes this after every persist, so the chips reflect the
        // maintained referent live (no optimistic guess, no stale best-effort refetch).
        if (ev?.type === 'ReferentSnapshot') { convState.value = ev.state; return }
        // The full todo (todo.json items) isn't carried by the referent — refetch it on
        // each todo change so the progress-chip tooltip's step list updates LIVE during the
        // turn (the todo is cleared at completion, so live is the only time it's useful).
        if (ev?.type === 'TodoInscribed') {
          const cid = currentId.value
          api.getTodo(cid).then(r => { if (currentId.value === cid) todo.value = r.todo || null }).catch(() => {})
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
        // Load the plan markdown for the Approve bar's CONTENT. The bar's VISIBILITY is driven by the
        // referent (convState.phase, pushed live by the end-of-turn ReferentSnapshot) — not by this fetch.
        api.getPlan(id).then(r => { plan.value = r.plan || null }).catch(() => {})
        api.getTodo(id).then(r => { todo.value = r.todo || null }).catch(() => {})
        // convState is already up to date : the orchestrator pushed an authoritative end-of-turn
        // ReferentSnapshot over the WS just before {final} (no separate best-effort GET needed).
        refresh() // re-order the list (last interaction first) + pick up auto-title
        fetchWsFiles() // surface files the agent just created as message links
        if (vocal.value) speak(m.answer)
      },
      error: m => { busy.value = false; stopping.value = false; queued.value = false; stopStreaming(); error.value = m.detail || 'Erreur orchestrateur.'; api.state(id).then(r => { convState.value = r.state || null }).catch(() => {}) },
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
    planTurn.value = isPlan // a plan turn writes its plan to the Approve-bar cartouche — don't stream the draft as a chat response
    streamingMsg = null // fresh turn → new building bubble
    liveThinking.value = ''
    messages.value.push({ role: 'user', content: clean, files: [...files] })
    trace.value = []
    dispatch.value = null
    error.value = ''
    // A new turn supersedes any pending approval : the optimistic phase below (planning/executing)
    // moves convState.phase off "awaiting_approval", so the Approve bar hides immediately.
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
    // No optimistic `approved` flip : the backend pushes a ReferentSnapshot within ~1 iteration,
    // so the chip flips authoritatively (the old guess sometimes showed "approuvé" wrongly).
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
    // A revert rewrote state.json too (no WS frame for a revert) → re-pull the referent.
    convState.value = await api.state(currentId.value).then(r => r.state || null).catch(() => null)
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
    currentMode, planMode, planTurn, planAvailable, awaitingApproval, plan, planEditorOpen, convState, todo,
    refresh, create, select, sendTurn, stopTurn, answer, approveAndExecute, rename, remove, reset,
    fetchWsFiles, openWorkspace, dismissMemory, onMemoryProposed,
    loadSnapshots, revert, fork, reloadCurrent,
  }
})
