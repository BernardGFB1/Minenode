<template>
  <div ref="root" class="entry" :style="{ fontFamily: MC_FONT }">
    <div class="icon">
      <img v-if="iconUrl" :src="iconUrl" alt="" @error="iconUrl = null" />
      <svg v-else viewBox="0 0 16 16"><rect width="16" height="16" fill="#6f6f6f" /><rect x="2" y="2" width="12" height="12" fill="#8b8b8b" /><rect x="5" y="5" width="6" height="6" fill="#5d5d5d" /></svg>
    </div>
    <div class="body">
      <div class="top">
        <span class="name">{{ name }}</span>
        <span class="spacer" />
        <span v-if="versionLabel" class="right" v-html="mcToHtml(versionLabel)" />
        <span v-else class="right"><span class="mc-7">{{ players }}</span><span class="bars"><i v-for="n in 5" :key="n" :style="{ height: n * 2 + 2 + 'px' }" /></span></span>
      </div>
      <div class="motd" v-html="motdHtml" />
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from 'vue'
import { mcToHtml } from '../utils'
import { MC_FONT, animateObfuscated } from '../mc'
const props = defineProps({ name: String, motd: String, players: { type: String, default: '0/20' }, versionLabel: String, icon: String })
const root = ref(null)
const iconUrl = ref(props.icon)
watch(() => props.icon, (v) => (iconUrl.value = v))
// MOTD text is gray by default; only the first two lines are shown by the client.
const motdHtml = computed(() => `<span class="mc-7">${mcToHtml((props.motd || '').split(/\n|\\n/).slice(0, 2).join('\n'))}</span>`)
let stop
onMounted(() => (stop = animateObfuscated(root.value)))
onUnmounted(() => stop?.())
watch(motdHtml, () => nextTick(() => root.value?.querySelectorAll('.mc-k').forEach((e) => delete e.dataset.len)))
</script>
<style scoped>
.entry { display: flex; gap: 10px; padding: 8px; background: #0e0e0e; border: 1px solid #2b2b2b; border-radius: 4px; color: #fff; font-size: 16px; line-height: 1.15; text-shadow: 2px 2px 0 rgba(0, 0, 0, 0.55); }
.icon { width: 64px; height: 64px; flex: none; }
.icon img, .icon svg { width: 100%; height: 100%; image-rendering: pixelated; display: block; }
.body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.top { display: flex; align-items: center; gap: 8px; }
.name { color: #fff; }
.spacer { flex: 1; }
.right { display: inline-flex; align-items: center; gap: 6px; color: #aaa; white-space: nowrap; }
.bars { display: inline-flex; align-items: flex-end; gap: 1px; height: 12px; }
.bars i { width: 2px; background: #55ff55; box-shadow: 1px 1px 0 #153f15; }
.motd { white-space: pre-wrap; overflow: hidden; word-break: break-word; }
.motd :deep(br) { line-height: 1.3; }
</style>
