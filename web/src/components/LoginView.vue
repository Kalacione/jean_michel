<template>
  <v-main>
    <v-container class="fill-height" fluid>
      <v-row align="center" justify="center">
        <v-col cols="12" lg="4" md="5" sm="8">
          <v-card class="pa-2" elevation="8">
            <v-card-title class="d-flex align-center ga-2 text-h5">
              <v-icon icon="mdi-robot-happy-outline" /> Jean-Michel
            </v-card-title>
            <v-card-subtitle>Connexion</v-card-subtitle>
            <v-card-text>
              <v-form @submit.prevent="submit">
                <v-text-field
                  v-model="username"
                  autofocus
                  :disabled="auth.loading"
                  label="Utilisateur"
                  prepend-inner-icon="mdi-account"
                />
                <v-text-field
                  v-model="password"
                  :disabled="auth.loading"
                  label="Mot de passe"
                  prepend-inner-icon="mdi-lock"
                  type="password"
                />
                <v-alert
                  v-if="auth.error"
                  class="mb-3"
                  density="compact"
                  :text="auth.error"
                  type="error"
                />
                <v-btn block color="primary" :loading="auth.loading" type="submit">
                  Se connecter
                </v-btn>
              </v-form>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </v-container>
  </v-main>
</template>

<script setup>
  import { ref } from 'vue'
  import { useAuthStore } from '@/stores/auth'

  const auth = useAuthStore()
  const username = ref('')
  const password = ref('')

  async function submit () {
    try {
      await auth.login(username.value, password.value)
    } catch { /* error surfaced via the store */ }
  }
</script>
