<template>
  <div class="d-flex flex-column fill-height">
    <div class="pa-3 d-flex flex-column ga-2">
      <div class="d-flex ga-2 align-center">
        <v-select
          v-model="mode"
          density="compact"
          hide-details
          :items="['analyse', 'chat', 'vocal', 'code']"
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
      <div class="d-flex ga-2 align-center">
        <v-select
          v-model="projectId"
          clearable
          density="compact"
          hide-details
          item-title="name"
          item-value="id"
          :items="projects.list"
          label="Projet (optionnel)"
          variant="outlined"
        />
        <v-btn
          icon="mdi-folder-cog-outline"
          title="Gérer les projets"
          variant="text"
          @click="projectsOpen = true"
        />
      </div>
    </div>
    <v-divider />
    <v-list class="flex-grow-1 overflow-y-auto" density="compact" nav>
      <v-list-item
        v-for="c in conv.list"
        :key="c.id"
        :active="c.id === conv.currentId"
        class="conv-item"
        prepend-icon="mdi-message-text-outline"
        :subtitle="`${c.mode} · ${c.status}`"
        :title="c.title || c.id.slice(0, 8)"
        @click="conv.select(c.id)"
      >
        <template #append>
          <v-btn
            class="edit-btn"
            icon="mdi-pencil-outline"
            size="x-small"
            title="Détails / supprimer"
            variant="text"
            @click.stop="openDetails(c)"
          />
        </template>
      </v-list-item>
      <v-list-item
        v-if="!conv.list.length"
        class="text-medium-emphasis"
        title="Aucune conversation"
      />
    </v-list>
  </div>

  <ConversationDetailsDialog v-model="detailsOpen" :item="selected" />
  <ProjectsDialog v-model="projectsOpen" />
</template>

<script setup>
  import { onMounted, ref } from 'vue'
  import ConversationDetailsDialog from '@/components/ConversationDetailsDialog.vue'
  import ProjectsDialog from '@/components/ProjectsDialog.vue'
  import { useConvStore } from '@/stores/conversations'
  import { useProjectStore } from '@/stores/projects'

  const conv = useConvStore()
  const projects = useProjectStore()
  const mode = ref('chat')
  const projectId = ref(null)
  const creating = ref(false)
  const detailsOpen = ref(false)
  const projectsOpen = ref(false)
  const selected = ref(null)

  onMounted(() => projects.refresh())

  function openDetails (c) {
    selected.value = c
    detailsOpen.value = true
  }

  async function create () {
    creating.value = true
    try {
      await conv.create(mode.value, projectId.value ?? null)
    } finally {
      creating.value = false
    }
  }
</script>

<style scoped>
.conv-item .edit-btn { opacity: 0; transition: opacity 0.15s; }
.conv-item:hover .edit-btn,
.conv-item.v-list-item--active .edit-btn { opacity: 1; }
</style>
