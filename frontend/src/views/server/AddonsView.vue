<template>
  <div>
    <SubTabs :tabs="tabs" />
    <router-view />
  </div>
</template>
<script setup>
import { computed, inject, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Puzzle, Image, PackageOpen, Blocks } from 'lucide-vue-next'
import SubTabs from '../../components/SubTabs.vue'

const live = inject('live')
const route = useRoute()
const router = useRouter()
const base = computed(() => `/servers/${live.summary.id}/addons`)
const type = computed(() => live.summary?.type)
const contentLabel = computed(() => ({ paper: 'Pluginy', purpur: 'Pluginy', custom: 'Mody i pluginy' }[type.value] || 'Mody'))
const tabs = computed(() => [
  ...(type.value !== 'vanilla' ? [{ to: `${base.value}/content`, label: contentLabel.value, icon: Puzzle }] : []),
  { to: `${base.value}/resourcepacks`, label: 'Paczki zasobów', icon: Image },
  { to: `${base.value}/datapacks`, label: 'Datapacki', icon: PackageOpen },
  { to: `${base.value}/services`, label: 'Usługi', icon: Blocks },
])
// Vanilla has no plugins or mods – open the first tab that exists instead.
watchEffect(() => {
  if (route.name === 'server-addons-content' && type.value === 'vanilla') router.replace(tabs.value[0].to)
})
</script>
