<template>
  <v-app>
    <!-- Wait for startup token validation before choosing a view, so a stale
         token never mounts the main UI (which would fail + blank the screen). -->
    <v-container v-if="!auth.ready" class="fill-height" fluid>
      <v-row align="center" justify="center"><v-progress-circular indeterminate /></v-row>
    </v-container>
    <LoginView v-else-if="!auth.isAuthed" />
    <MainLayout v-else />
  </v-app>
</template>

<script setup>
  import { onMounted } from 'vue'
  import LoginView from '@/components/LoginView.vue'
  import MainLayout from '@/components/MainLayout.vue'
  import { useAuthStore } from '@/stores/auth'

  const auth = useAuthStore()
  onMounted(() => auth.fetchMe())
</script>
