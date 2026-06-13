import { ref } from 'vue'
import { defineStore } from 'pinia'

// Global toast/snackbar. Any component or the app-level notifications WS calls
// `show(text, color)`; a single <v-snackbar> in MainLayout renders it.
export const useSnackbarStore = defineStore('snackbar', () => {
  const visible = ref(false)
  const text = ref('')
  const color = ref('success')
  const timeout = ref(4000)

  function show (msg, c = 'success', t = 4000) {
    text.value = msg
    color.value = c
    timeout.value = t
    visible.value = true
  }

  return { visible, text, color, timeout, show }
})
