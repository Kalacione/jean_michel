<template>
  <v-app-bar border="b" flat>
    <v-app-bar-nav-icon @click="drawer = !drawer" />
    <v-app-bar-title>Jean-Michel</v-app-bar-title>
    <v-spacer />
    <v-btn
      icon="mdi-folder-open-outline"
      :disabled="!conv.currentId"
      title="Workspace"
      @click="conv.openWorkspace()"
    />
    <v-btn icon="mdi-brain" title="Mémoire" @click="memory = true" />
    <v-badge
      :content="conv.pendingMemory.length"
      :model-value="conv.pendingMemory.length > 0"
      color="primary"
    >
      <v-btn
        icon="mdi-lightbulb-on-outline"
        :title="`Suggestions mémoire (${conv.pendingMemory.length})`"
        @click="review = true"
      />
    </v-badge>
    <v-btn icon="mdi-robot-outline" title="Réglages des agents" @click="agents = true" />
    <v-btn icon="mdi-script-text-outline" title="Paradigmes" @click="paradigms = true" />
    <v-btn icon="mdi-theme-light-dark" title="Thème" @click="$vuetify.theme.cycle()" />
    <v-chip
      class="ml-2"
      link
      prepend-icon="mdi-account"
      title="Mon profil"
      variant="tonal"
      @click="profile = true"
    >
      {{ auth.user?.username }}
    </v-chip>
    <v-btn icon="mdi-logout" title="Déconnexion" @click="logout" />
  </v-app-bar>

  <v-navigation-drawer v-model="drawer" width="320">
    <ConversationsDrawer />
  </v-navigation-drawer>

  <v-main>
    <ChatPane v-if="conv.currentId" />
    <v-container v-else class="fill-height text-medium-emphasis" fluid>
      <v-row align="center" justify="center">
        <v-col class="text-center" cols="auto">
          <v-icon class="mb-2" icon="mdi-chat-outline" size="64" />
          <div>Sélectionne ou crée une conversation.</div>
        </v-col>
      </v-row>
    </v-container>
  </v-main>

  <AskHumanDialog />
  <WorkspaceDialog v-model="conv.wsOpen" :initial-path="conv.wsInitialPath" />
  <MemoryDialog v-model="memory" />
  <MemoryReviewDialog v-model="review" />
  <ProfileDialog v-model="profile" />
  <AgentsDialog v-model="agents" />
  <ParadigmsDialog v-model="paradigms" />

  <v-snackbar
    v-model="snackbar.visible"
    :color="snackbar.color"
    location="bottom right"
    :timeout="snackbar.timeout"
  >
    {{ snackbar.text }}
  </v-snackbar>
</template>

<script setup>
  import { onMounted, onUnmounted, ref } from 'vue'
  import AgentsDialog from '@/components/AgentsDialog.vue'
  import AskHumanDialog from '@/components/AskHumanDialog.vue'
  import ChatPane from '@/components/ChatPane.vue'
  import ConversationsDrawer from '@/components/ConversationsDrawer.vue'
  import MemoryDialog from '@/components/MemoryDialog.vue'
  import MemoryReviewDialog from '@/components/MemoryReviewDialog.vue'
  import ParadigmsDialog from '@/components/ParadigmsDialog.vue'
  import ProfileDialog from '@/components/ProfileDialog.vue'
  import WorkspaceDialog from '@/components/WorkspaceDialog.vue'
  import { useAuthStore } from '@/stores/auth'
  import { useConvStore } from '@/stores/conversations'
  import { useSnackbarStore } from '@/stores/snackbar'
  import { connectNotifications } from '@/ws'

  const auth = useAuthStore()
  const conv = useConvStore()
  const snackbar = useSnackbarStore()
  const drawer = ref(true)
  const memory = ref(false)
  const review = ref(false)
  const profile = ref(false)
  const agents = ref(false)
  const paradigms = ref(false)

  let notif = null
  let notifClosing = false
  let notifTries = 0

  function openNotifications () {
    notif = connectNotifications({
      notification: m => {
        notifTries = 0 // healthy connection
        if (m.kind === 'memory_proposed') { conv.onMemoryProposed(m); return }
        if (m.kind === 'turn_complete') { conv.syncCompletedTurn(m.conv_id); return }
        if (m.kind !== 'project_image_build') return
        const name = m.project_name || 'projet'
        if (m.state === 'ok') snackbar.show(`Image du sandbox « ${name} » prête.`, 'success')
        else if (m.state === 'failed') snackbar.show(`Build de « ${name} » échoué : ${m.error || 'voir les logs'}`, 'error', 8000)
        else if (m.state === 'deferred') snackbar.show(`Image de « ${name} » : sera buildée à la 1ʳᵉ utilisation.`, 'info')
      },
      // Reconnect on transient drops only — stop when logging out / logged out /
      // after repeated failures (invalid token, daemon down) to avoid a hot loop.
      close: () => {
        if (notifClosing || !auth.isAuthed || notifTries++ > 5) return
        setTimeout(openNotifications, 3000)
      },
    })
  }

  onMounted(() => {
    conv.refresh()
    conv.loadCapabilities()
    openNotifications()
  })

  onUnmounted(() => {
    notifClosing = true
    notif?.close()
  })

  function logout () {
    notifClosing = true
    notif?.close()
    conv.reset()
    auth.logout()
  }
</script>
