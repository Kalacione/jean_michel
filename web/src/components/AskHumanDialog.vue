<template>
  <v-dialog max-width="560" :model-value="!!conv.askHuman" persistent>
    <v-card v-if="conv.askHuman">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon color="warning" icon="mdi-help-circle-outline" /> Jean-Michel a besoin de toi
      </v-card-title>
      <v-card-text>
        <p v-if="conv.askHuman.why" class="text-medium-emphasis mb-2">{{ conv.askHuman.why }}</p>
        <p class="font-weight-medium mb-3">{{ conv.askHuman.question }}</p>

        <!-- Single choice: radios + "Autre…" -->
        <v-radio-group
          v-if="hasChoices && !conv.askHuman.multi"
          v-model="selected"
          hide-details
        >
          <v-radio v-for="c in conv.askHuman.choices" :key="c" :label="c" :value="c" />
          <v-radio :label="OTHER_LABEL" :value="OTHER" />
        </v-radio-group>

        <!-- Multi choice: checkboxes + "Autre…" -->
        <div v-else-if="hasChoices">
          <v-checkbox
            v-for="c in conv.askHuman.choices"
            :key="c"
            v-model="selectedMulti"
            density="compact"
            hide-details
            :label="c"
            :value="c"
          />
          <v-checkbox v-model="otherChecked" density="compact" hide-details :label="OTHER_LABEL" />
        </div>

        <!-- Free text: no choices, or "Autre…" picked -->
        <v-textarea
          v-if="showText"
          v-model="reply"
          autofocus
          auto-grow
          class="mt-2"
          hide-details
          rows="2"
          variant="outlined"
          @keydown.enter.exact.prevent="submit"
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn :disabled="!canSubmit" color="primary" variant="flat" @click="submit">Répondre</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { computed, ref, watch } from 'vue'
  import { useConvStore } from '@/stores/conversations'

  const OTHER = Symbol('other') // collision-proof sentinel for the "Autre…" option
  const OTHER_LABEL = 'Autre…'
  const conv = useConvStore()

  const reply = ref('') // free-text answer (no choices, or "Autre…")
  const selected = ref(null) // single: a choice label | OTHER | null
  const selectedMulti = ref([]) // multi: array of chosen labels
  const otherChecked = ref(false) // multi: "Autre…" toggled

  const hasChoices = computed(() => !!conv.askHuman?.choices?.length)
  const showText = computed(() => {
    if (!hasChoices.value) return true
    return conv.askHuman.multi ? otherChecked.value : selected.value === OTHER
  })
  const canSubmit = computed(() => {
    if (!conv.askHuman) return false
    if (!hasChoices.value) return !!reply.value.trim()
    if (conv.askHuman.multi) {
      return selectedMulti.value.length > 0 || (otherChecked.value && !!reply.value.trim())
    }
    return selected.value != null && (selected.value !== OTHER || !!reply.value.trim())
  })

  function reset () {
    reply.value = ''
    selected.value = null
    selectedMulti.value = []
    otherChecked.value = false
  }

  // A new question (or the dialog closing) starts from a clean slate.
  watch(() => conv.askHuman, reset)

  function buildAnswer () {
    if (!hasChoices.value) return reply.value.trim()
    if (conv.askHuman.multi) {
      const parts = [...selectedMulti.value]
      if (otherChecked.value && reply.value.trim()) parts.push(reply.value.trim())
      return parts.join(', ')
    }
    return selected.value === OTHER ? reply.value.trim() : selected.value
  }

  function submit () {
    if (!canSubmit.value) return
    conv.answer(buildAnswer())
    reset()
  }
</script>
