<template>
  <v-dialog max-width="560" :model-value="!!conv.askHuman" persistent>
    <v-card v-if="conv.askHuman">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon color="warning" icon="mdi-help-circle-outline" /> Jean-Michel a besoin de toi
      </v-card-title>
      <v-card-text>
        <p v-if="conv.askHuman.why" class="text-medium-emphasis mb-2">{{ conv.askHuman.why }}</p>
        <p class="font-weight-medium mb-3">{{ conv.askHuman.question }}</p>
        <v-textarea
          v-model="reply"
          autofocus
          auto-grow
          hide-details
          rows="2"
          variant="outlined"
          @keydown.enter.exact.prevent="submit"
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn color="primary" variant="flat" @click="submit">Répondre</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { ref } from 'vue'
  import { useConvStore } from '@/stores/conversations'

  const conv = useConvStore()
  const reply = ref('')

  function submit () {
    conv.answer(reply.value)
    reply.value = ''
  }
</script>
