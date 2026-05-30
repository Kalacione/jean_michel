<template>
  <span class="ws-img">
    <img
      v-if="url"
      :alt="name"
      class="thumb"
      :src="url"
      :title="name"
      @click="open = true"
    >
    <v-chip
      v-else-if="failed"
      size="small"
      :title="name"
      variant="tonal"
      @click="conv.openWorkspace(path)"
    >
      <v-icon icon="mdi-image-off-outline" size="14" start /> {{ name }}
    </v-chip>
    <span v-else class="thumb loading d-flex align-center justify-center">
      <v-progress-circular indeterminate size="20" width="2" />
    </span>

    <v-dialog v-model="open" max-width="92vw">
      <v-card>
        <v-toolbar density="compact" :title="name">
          <v-btn icon="mdi-download" title="Télécharger l'original" @click="download" />
          <v-btn icon="mdi-close" @click="open = false" />
        </v-toolbar>
        <v-img contain max-height="82vh" :src="url" />
      </v-card>
    </v-dialog>
  </span>
</template>

<script setup>
  import { onUnmounted, ref, watch } from 'vue'
  import { api } from '@/api'
  import { saveBlob } from '@/download'
  import { useConvStore } from '@/stores/conversations'

  const props = defineProps({ path: { type: String, required: true } })
  const conv = useConvStore()
  const url = ref('')
  const failed = ref(false)
  const open = ref(false)
  const name = props.path.split('/').pop()

  function revoke () {
    if (url.value) {
      URL.revokeObjectURL(url.value)
      url.value = ''
    }
  }

  async function load () {
    revoke()
    failed.value = false
    const blob = await api.workspaceImage(conv.currentId, props.path, { thumb: true })
    if (blob) url.value = URL.createObjectURL(blob)
    else failed.value = true
  }

  async function download () {
    const blob = await api.downloadWorkspace(conv.currentId, props.path)
    if (blob) saveBlob(blob, name)
  }

  watch(() => props.path, load, { immediate: true })
  onUnmounted(revoke)
</script>

<style scoped>
.thumb {
  max-width: 240px;
  max-height: 200px;
  border-radius: 6px;
  cursor: pointer;
  display: block;
}
.thumb.loading {
  width: 120px;
  height: 90px;
  background: rgba(127, 127, 127, 0.12);
}
</style>
