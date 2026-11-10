<template>
  <span class="badge" :class="meta.color">
    <Loader2 v-if="busy" class="spin" style="width: 12px; height: 12px" />
    <span v-else class="dot" />
    {{ $t(meta.label) }}
  </span>
</template>
<script setup>
import { computed } from 'vue'
import { Loader2 } from 'lucide-vue-next'
import { STATE } from '../utils'
const props = defineProps({ state: String })
const meta = computed(() => STATE[props.state] || { label: props.state, color: '' })
const busy = computed(() => ['starting', 'stopping', 'installing'].includes(props.state))
</script>
<style scoped>
.badge .dot { width: 7px; height: 7px; background: currentColor; }
.badge:not(.green):not(.amber):not(.red):not(.blue) .dot { background: var(--muted); }
</style>
