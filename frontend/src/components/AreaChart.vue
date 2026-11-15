<template>
  <div class="chart" ref="el">
    <svg :viewBox="`0 0 ${w} ${h}`" preserveAspectRatio="none" @mousemove="hover" @mouseleave="hoverIdx = -1">
      <line v-for="g in grid" :key="g" :x1="0" :x2="w" :y1="g" :y2="g" class="grid" />
      <path v-for="(seg, i) in segments" :key="'a' + i" :d="seg.area" class="area" :style="{ fill: color }" />
      <path v-for="(seg, i) in segments" :key="'l' + i" :d="seg.line" class="line" :style="{ stroke: color }" />
      <line v-if="hoverIdx >= 0" :x1="xs[hoverIdx]" :x2="xs[hoverIdx]" :y1="0" :y2="h" class="cursor" />
    </svg>
    <div v-if="hoverIdx >= 0 && pts[hoverIdx]" class="tip" :style="{ left: `${Math.min(88, Math.max(12, (xs[hoverIdx] / w) * 100))}%` }">
      <b class="num">{{ format(pts[hoverIdx].v) }}</b>
      <span class="muted">{{ new Date(pts[hoverIdx].t * 1000).toLocaleTimeString(dateLocale()) }}</span>
    </div>
    <div v-if="!pts.length" class="nodata muted small">{{ $t('Brak danych – serwer nie działa') }}</div>
  </div>
</template>
<script setup>
import { computed, ref } from 'vue'
import { dateLocale } from '../i18n'
const props = defineProps({
  points: { type: Array, default: () => [] }, // [{t, v}]
  max: { type: Number, default: null },
  color: { type: String, default: 'var(--accent)' },
  format: { type: Function, default: (v) => v },
  window: { type: Number, default: 600 },
})
const w = 600, h = 140
const hoverIdx = ref(-1)
const grid = [h * 0.25, h * 0.5, h * 0.75]
const GAP = 20 // seconds without samples (server stopped) – the line is broken there, not bridged
const t1 = computed(() => props.points.at(-1)?.t || Date.now() / 1000)
// Only samples inside the time window are drawn: older ones (e.g. from before a restart) would get a
// negative x and spill over the neighbouring card.
const pts = computed(() => props.points.filter((p) => p.t >= t1.value - props.window))
// Grow the visible window with the data (min 2 min) so a fresh server doesn't render as a sliver.
const span = computed(() => Math.min(props.window, Math.max(120, t1.value - (pts.value[0]?.t ?? t1.value))))
const t0 = computed(() => t1.value - span.value)
const ymax = computed(() => props.max || Math.max(1, ...pts.value.map((p) => p.v)) * 1.15)
const xs = computed(() => pts.value.map((p) => Math.max(0, Math.min(w, ((p.t - t0.value) / span.value) * w))))
const ys = computed(() => pts.value.map((p) => h - Math.min(1, Math.max(0, p.v) / ymax.value) * (h - 6) - 2))
const segments = computed(() => {
  const out = []
  let start = 0
  for (let i = 1; i <= pts.value.length; i++) {
    if (i < pts.value.length && pts.value[i].t - pts.value[i - 1].t <= GAP) continue
    if (i - start >= 2) {
      const idx = Array.from({ length: i - start }, (_, k) => start + k)
      const line = idx.map((j, k) => `${k ? 'L' : 'M'}${xs.value[j].toFixed(1)},${ys.value[j].toFixed(1)}`).join('')
      out.push({ line, area: `${line}L${xs.value[i - 1].toFixed(1)},${h}L${xs.value[start].toFixed(1)},${h}Z` })
    }
    start = i
  }
  return out
})
function hover(e) {
  if (!pts.value.length) return
  const r = e.currentTarget.getBoundingClientRect()
  const x = ((e.clientX - r.left) / r.width) * w
  let best = 0
  xs.value.forEach((px, i) => { if (Math.abs(px - x) < Math.abs(xs.value[best] - x)) best = i })
  hoverIdx.value = best
}
</script>
<style scoped>
.chart { position: relative; height: 140px; }
svg { width: 100%; height: 100%; display: block; overflow: hidden; }
.grid { stroke: var(--border); stroke-dasharray: 3 4; vector-effect: non-scaling-stroke; }
.line { fill: none; stroke-width: 1.8; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
.area { opacity: 0.12; }
.cursor { stroke: var(--border-strong); vector-effect: non-scaling-stroke; }
.tip { position: absolute; top: -6px; transform: translateX(-50%); background: var(--surface-2); border: 1px solid var(--border-strong); border-radius: 6px; padding: 3px 8px; font-size: 12px; display: flex; gap: 8px; pointer-events: none; white-space: nowrap; }
.nodata { position: absolute; inset: 0; display: grid; place-items: center; }
</style>
