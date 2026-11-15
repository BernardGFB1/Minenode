<template>
  <!-- Wait for the first route: until then $route is the empty start location (not "public"),
       and mounting the panel shell would send a guest on a public page to the login screen. -->
  <template v-if="auth.ready && routeReady">
    <router-view v-if="$route.meta.public" />
    <AppShell v-else />
  </template>
  <Toasts />
  <ConfirmDialog />
</template>

<script setup>
import AppShell from './components/AppShell.vue'
import Toasts from './components/Toasts.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import { ref } from 'vue'
import { auth } from './store'
import { router } from './router'

const routeReady = ref(false)
router.isReady().then(() => { routeReady.value = true })
</script>
