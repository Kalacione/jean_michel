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
    <v-btn icon="mdi-account-cog" title="Profil" @click="profile = true" />
    <v-btn icon="mdi-theme-light-dark" title="Thème" @click="$vuetify.theme.cycle()" />
    <v-chip class="ml-2" prepend-icon="mdi-account" variant="tonal">
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
</template>

<script setup>
  import { onMounted, ref } from 'vue'
  import AskHumanDialog from '@/components/AskHumanDialog.vue'
  import ChatPane from '@/components/ChatPane.vue'
  import ConversationsDrawer from '@/components/ConversationsDrawer.vue'
  import MemoryDialog from '@/components/MemoryDialog.vue'
  import MemoryReviewDialog from '@/components/MemoryReviewDialog.vue'
  import ProfileDialog from '@/components/ProfileDialog.vue'
  import WorkspaceDialog from '@/components/WorkspaceDialog.vue'
  import { useAuthStore } from '@/stores/auth'
  import { useConvStore } from '@/stores/conversations'

  const auth = useAuthStore()
  const conv = useConvStore()
  const drawer = ref(true)
  const memory = ref(false)
  const review = ref(false)
  const profile = ref(false)

  onMounted(() => conv.refresh())

  function logout () {
    conv.reset()
    auth.logout()
  }
</script>
