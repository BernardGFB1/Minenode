<template>
  <div class="ram">
    <div class="row" style="align-items: baseline">
      <div class="big num">{{ formatMB(max) }}</div>
      <div class="muted small">maksymalnie (-Xmx)</div>
      <div class="spacer" />
      <div class="small text-2 num">start: {{ formatMB(Math.min(min, max)) }}</div>
    </div>
    <input type="range" :min="512" :max="limit" step="256" :value="max" @input="setMax(+$event.target.value)" />
    <div class="row wrap" style="gap: 6px">
      <button v-for="p in presets" :key="p" type="button" class="chip" :class="{ active: max === p }" @click="setMax(p)">{{ formatMB(p) }}</button>
    </div>
    <div v-if="totalMb" class="host">
      <div class="bar">
        <div class="seg other" :style="{ width: pct(otherMb) }" />
        <div class="seg mine" :class="{ over: overcommit }" :style="{ width: pct(max) }" />
      </div>
      <div class="row tiny muted">
        <span><i class="sw other" /> inne serwery {{ formatMB(otherMb) }}</span>
        <span><i class="sw mine" /> ten serwer {{ formatMB(max) }}</span>
        <span class="spacer" />
        <span>dostępne: {{ formatMB(totalMb) }}</span>
      </div>
      <div v-if="overcommit" class="callout warn small" style="margin-top: 6px">
        <AlertTriangle />Suma przydziałów przekracza pamięć hosta/kontenera – serwery mogą zostać zabite przez OOM.
      </div>
    </div>
    <details class="advanced">
      <summary class="small text-2">Pamięć startowa (-Xms)</summary>
      <div class="row" style="margin-top: 8px">
        <input type="range" :min="256" :max="max" step="256" :value="Math.min(min, max)" @input="$emit('update:min', +$event.target.value)" />
        <button type="button" class="chip" @click="$emit('update:min', max)">= maks.</button>
      </div>
      <div class="hint tiny muted">Przy flagach Aikara zalecane jest Xms = Xmx (stała sterta, mniej pauz GC).</div>
    </details>
  </div>
</template>
<script setup>
import { computed } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'
import { formatMB } from '../utils'
const props = defineProps({ min: Number, max: Number, totalBytes: Number, otherBytes: { type: Number, default: 0 } })
const emit = defineEmits(['update:min', 'update:max'])
const totalMb = computed(() => (props.totalBytes ? Math.floor(props.totalBytes / 1048576) : 0))
const otherMb = computed(() => Math.floor((props.otherBytes || 0) / 1048576))
const limit = computed(() => Math.max(2048, Math.min(65536, totalMb.value ? Math.floor(totalMb.value / 256) * 256 : 16384)))
const presets = computed(() => [1024, 2048, 4096, 6144, 8192, 12288, 16384, 24576, 32768].filter((p) => p <= limit.value))
const overcommit = computed(() => totalMb.value && otherMb.value + props.max > totalMb.value)
const pct = (mb) => `${Math.min(100, (mb / Math.max(totalMb.value, otherMb.value + props.max)) * 100)}%`
function setMax(v) {
  emit('update:max', v)
  if (props.min > v) emit('update:min', v)
}
</script>
<style scoped>
.ram { display: flex; flex-direction: column; gap: 12px; }
.big { font-size: 26px; font-weight: 650; letter-spacing: -0.02em; }
.host { display: flex; flex-direction: column; gap: 6px; }
.bar { display: flex; height: 8px; border-radius: 8px; background: var(--surface-3); overflow: hidden; }
.seg.other { background: var(--border-strong); }
.seg.mine { background: var(--accent); }
.seg.mine.over { background: var(--danger); }
.sw { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; vertical-align: -0.5px; }
.sw.other { background: var(--border-strong); }
.sw.mine { background: var(--accent); }
.advanced summary { cursor: pointer; }
</style>
