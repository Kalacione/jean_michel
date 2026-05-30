<template>
  <v-dialog v-model="open" max-width="480">
    <v-card v-if="item">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-information-outline" /> Détails de la conversation
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" @click="open = false" />
      </v-card-title>
      <v-divider />
      <v-card-text>
        <v-text-field
          v-model="title"
          density="compact"
          label="Titre"
          variant="outlined"
          @keydown.enter="save"
        />
        <div class="text-caption text-medium-emphasis">
          <div>Mode : {{ item.mode }} · Statut : {{ item.status }}</div>
          <div>Créée : {{ fmt(item.created_at) }}</div>
          <div>Dernière activité : {{ fmt(item.modified_at) }}</div>
          <div class="text-truncate">ID : {{ item.id }}</div>
        </div>
        <v-alert v-if="error" class="mt-3" density="compact" :text="error" type="error" />
      </v-card-text>
      <v-divider />
      <v-card-actions>
        <v-btn
          :color="confirming ? 'error' : undefined"
          :loading="deleting"
          prepend-icon="mdi-delete-outline"
          :variant="confirming ? 'flat' : 'text'"
          @click="remove"
        >
          {{ confirming ? 'Confirmer ?' : 'Supprimer' }}
        </v-btn>
        <v-spacer />
        <v-btn color="primary" :disabled="!dirty" :loading="saving" variant="flat" @click="save">
          Enregistrer
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
  import { computed, ref, watch } from 'vue'
  import { useConvStore } from '@/stores/conversations'

  const conv = useConvStore()
  const open = defineModel({ type: Boolean })
  const props = defineProps({ item: { type: Object, default: null } })

  const title = ref('')
  const saving = ref(false)
  const deleting = ref(false)
  const confirming = ref(false)
  const error = ref('')

  const dirty = computed(() => {
    const t = title.value.trim()
    return !!t && t !== (props.item?.title || '')
  })

  function fmt (s) {
    return s ? s.replace('T', ' ').replace('Z', '').slice(0, 16) : '—'
  }

  async function save () {
    if (!dirty.value || !props.item) return
    saving.value = true
    error.value = ''
    try {
      await conv.rename(props.item.id, title.value.trim())
      open.value = false
    } catch (e) {
      error.value = e.detail || e.message
    } finally {
      saving.value = false
    }
  }

  async function remove () {
    if (!props.item) return
    if (!confirming.value) { // first click arms the confirmation
      confirming.value = true
      return
    }
    deleting.value = true
    error.value = ''
    try {
      await conv.remove(props.item.id)
      open.value = false
    } catch (e) {
      error.value = e.detail || e.message
    } finally {
      deleting.value = false
    }
  }

  // On open : sync the editable title and reset transient state.
  watch(open, v => {
    if (v) {
      title.value = props.item?.title || ''
      confirming.value = false
      error.value = ''
    }
  })
</script>
