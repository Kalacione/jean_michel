<template>
  <v-app>
    <LoginView v-if="!auth.isAuthed" />
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
