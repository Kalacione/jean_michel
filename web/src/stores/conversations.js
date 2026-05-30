import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'
import { connectTurn } from '@/ws'

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

  async function create (mode) {
    const c = await api.createConversation(mode)
    await refresh()
    await select(c.id)
    return c
  }

  async function select (id) {
    if (currentId.value === id) return
    closeWs()
    currentId.value = id
    error.value = ''
    const meta = list.value.find(c => c.id === id)
    vocal.value = meta?.mode === 'vocal'
    const loaded = (await api.messages(id)).messages
    messages.value = loaded.filter(m => m.role === 'user' || m.role === 'assistant')
    trace.value = []
    dispatch.value = null
    busy.value = false
    askHuman.value = null
    openWs(id)
  }

  function openWs (id) {
    turnWs = connectTurn(id, {
      dispatch: m => { dispatch.value = m },
      event: m => {
        trace.value.push(m)
        if (vocal.value && m.speak) speak(m.speak)
      },
      ask_human: m => { askHuman.value = { question: m.question, why: m.why } },
      queued: () => { queued.value = true },
      final: m => {
        queued.value = false
        busy.value = false
        askHuman.value = null
        messages.value.push({ role: 'assistant', content: m.answer })
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

  function sendTurn (text) {
    const clean = (text || '').trim()
    if (!clean || !turnWs || busy.value) return
    messages.value.push({ role: 'user', content: clean })
    trace.value = []
    dispatch.value = null
    error.value = ''
    busy.value = true
    turnWs.sendTurn(clean)
  }

  function answer (text) {
    if (!turnWs) return
    askHuman.value = null
    turnWs.sendAnswer((text || '').trim())
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

  return {
    list, currentId, messages, trace, busy, queued, dispatch, askHuman, error, vocal,
    refresh, create, select, sendTurn, answer, reset,
  }
})
