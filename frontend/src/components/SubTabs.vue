<template>
  <nav class="subtabs">
    <router-link v-for="t in tabs" :key="t.to" :to="t.to" class="st" :class="{ on: isOn(t) }">
      <component :is="t.icon" /> {{ $t(t.label) }}
      <span v-if="t.count" class="cnt">{{ t.count }}</span>
    </router-link>
  </nav>
</template>
<script setup>
import { useRoute } from 'vue-router'

// tabs: [{ to, label, icon, exact? }] – an exact tab is active only on its own path (used for the "general" page).
defineProps({ tabs: { type: Array, required: true } })
const route = useRoute()
const trim = (p) => p.replace(/\/+$/, '')
const isOn = (t) => (t.exact ? trim(route.path) === trim(t.to) : trim(route.path).startsWith(trim(t.to)))
</script>
<style scoped>
.subtabs { display: flex; gap: 4px; flex-wrap: wrap; justify-content: center; padding: 3px; margin: 0 auto 16px; width: fit-content; max-width: 100%;
  border-radius: 10px; background: var(--surface-2); border: 1px solid var(--border); }
.st { display: inline-flex; align-items: center; gap: 7px; height: 30px; padding: 0 12px; border-radius: 7px; color: var(--text-2);
  font-weight: 500; font-size: 13px; white-space: nowrap; }
.st svg { width: 14px; height: 14px; }
.st:hover { color: var(--text); text-decoration: none; }
.st.on { background: var(--surface-3); color: var(--text); box-shadow: 0 1px 0 var(--border); }
.cnt { font-size: 11px; color: var(--muted); }
</style>
