<template>
  <div>
    <SubTabs :tabs="tabs" />
    <router-view />
  </div>
</template>
<script setup>
import { computed, inject } from 'vue'
import { Settings2, Coffee } from 'lucide-vue-next'
import SubTabs from '../../components/SubTabs.vue'

const live = inject('live')
const base = computed(() => `/servers/${live.summary.id}/settings`)
const tabs = computed(() => [
  { to: base.value, label: 'Ogólne', icon: Settings2, exact: true },
  { to: `${base.value}/java`, label: 'Java i start', icon: Coffee },
])
</script>
