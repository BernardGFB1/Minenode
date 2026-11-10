<template>
  <template v-if="auth.ready">
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
import { auth } from './store'
</script>
