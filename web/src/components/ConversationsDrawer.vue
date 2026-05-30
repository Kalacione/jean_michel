<template>
  <div class="d-flex flex-column fill-height">
    <div class="pa-3 d-flex ga-2 align-center">
      <v-select
        v-model="mode"
        density="compact"
        hide-details
        :items="['analyse', 'chat', 'vocal']"
        label="Mode"
        variant="outlined"
      />
      <v-btn
        color="primary"
        icon="mdi-plus"
        :loading="creating"
        title="Nouvelle conversation"
        @click="create"
      />
    </div>
    <v-divider />
    <v-list class="flex-grow-1 overflow-y-auto" density="compact" nav>
      <v-list-item
        v-for="c in conv.list"
        :key="c.id"
        :active="c.id === conv.currentId"
        prepend-icon="mdi-message-text-outline"
        :subtitle="`${c.mode} · ${c.status}`"
        :title="c.title || c.id.slice(0, 8)"
        @click="conv.select(c.id)"
      />
      <v-list-item
        v-if="!conv.list.length"
        class="text-medium-emphasis"
        title="Aucune conversation"
      />
    </v-list>
  </div>
</template>

<script setup>
  import { ref } from 'vue'
  import { useConvStore } from '@/stores/conversations'

  const conv = useConvStore()
  const mode = ref('chat')
  const creating = ref(false)

  async function create () {
    creating.value = true
    try {
      await conv.create(mode.value)
    } finally {
      creating.value = false
    }
  }
</script>
