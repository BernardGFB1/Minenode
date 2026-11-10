<template>
  <div class="chart" ref="el">
    <svg :viewBox="`0 0 ${w} ${h}`" preserveAspectRatio="none" @mousemove="hover" @mouseleave="hoverIdx = -1">
      <line v-for="g in grid" :key="g" :x1="0" :x2="w" :y1="g" :y2="g" class="grid" />
      <path v-if="area" :d="area" class="area" :style="{ fill: color }" />
      <path v-if="line" :d="line" class="line" :style="{ stroke: color }" />
      <line v-if="hoverIdx >= 0" :x1="xs[hoverIdx]" :x2="xs[hoverIdx]" :y1="0" :y2="h" class="cursor" />
    </svg>
    <div v-if="hoverIdx >= 0 && points[hoverIdx]" class="tip" :style="{ left: `${(xs[hoverIdx] / w) * 100}%` }">
      <b class="num">{{ format(points[hoverIdx].v) }}</b>
      <span class="muted">{{ new Date(points[hoverIdx].t * 1000).toLocaleTimeString('pl-PL') }}</span>
    </div>
    <div v-if="!points.length" class="nodata muted small">Brak danych – serwer nie działa</div>
  </div>
</template>
<script setup>
import { computed, ref } from 'vue'
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
const t1 = computed(() => props.points.at(-1)?.t || Date.now() / 1000)
// Grow the visible window with the data (min 2 min) so a fresh server doesn't render as a sliver.
const span = computed(() => Math.min(props.window, Math.max(120, t1.value - (props.points[0]?.t ?? t1.value))))
const t0 = computed(() => t1.value - span.value)
const ymax = computed(() => props.max || Math.max(1, ...props.points.map((p) => p.v)) * 1.15)
const xs = computed(() => props.points.map((p) => ((p.t - t0.value) / span.value) * w))
const ys = computed(() => props.points.map((p) => h - Math.min(1, p.v / ymax.value) * (h - 6) - 2))
const line = computed(() => (props.points.length < 2 ? '' : xs.value.map((x, i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${ys.value[i].toFixed(1)}`).join('')))
const area = computed(() => (line.value ? `${line.value}L${xs.value.at(-1)},${h}L${xs.value[0]},${h}Z` : ''))
function hover(e) {
  if (!props.points.length) return
  const r = e.currentTarget.getBoundingClientRect()
  const x = ((e.clientX - r.left) / r.width) * w
  let best = 0
  xs.value.forEach((px, i) => { if (Math.abs(px - x) < Math.abs(xs.value[best] - x)) best = i })
  hoverIdx.value = best
}
</script>
<style scoped>
.chart { position: relative; height: 140px; }
svg { width: 100%; height: 100%; display: block; overflow: visible; }
.grid { stroke: var(--border); stroke-dasharray: 3 4; vector-effect: non-scaling-stroke; }
.line { fill: none; stroke-width: 1.8; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
.area { opacity: 0.12; }
.cursor { stroke: var(--border-strong); vector-effect: non-scaling-stroke; }
.tip { position: absolute; top: -6px; transform: translateX(-50%); background: var(--surface-2); border: 1px solid var(--border-strong); border-radius: 6px; padding: 3px 8px; font-size: 12px; display: flex; gap: 8px; pointer-events: none; white-space: nowrap; }
.nodata { position: absolute; inset: 0; display: grid; place-items: center; }
</style>
