<template>
  <div class="meter"><div :class="level" :style="{ width: Math.min(100, Math.max(0, value * 100)) + '%' }" /></div>
</template>
<script setup>
import { computed } from 'vue'
const props = defineProps({ value: { type: Number, default: 0 } })
const level = computed(() => (props.value > 0.9 ? 'red' : props.value > 0.75 ? 'amber' : ''))
</script>
<style scoped>
.meter { height: 5px; border-radius: 5px; background: var(--surface-3); overflow: hidden; }
.meter > div { height: 100%; background: var(--accent); border-radius: 5px; transition: width 0.5s ease; }
.meter > div.amber { background: var(--warn); }
.meter > div.red { background: var(--danger); }
</style>
