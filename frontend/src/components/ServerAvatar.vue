<template>
  <div class="avatar" :style="{ width: size + 'px', height: size + 'px', '--c': meta.color, '--ink': meta.ink || '#10140f', fontSize: size * 0.36 + 'px' }">
    <img v-if="server.has_icon && !failed" :src="`/api/servers/${server.id}/icon?v=${server.id}`" alt="" @error="failed = true" />
    <img v-else-if="server.modpack?.icon_url && !failed2" :src="server.modpack.icon_url" alt="" referrerpolicy="no-referrer" @error="failed2 = true" />
    <span v-else>{{ meta.letter }}</span>
  </div>
</template>
<script setup>
import { computed, ref } from 'vue'
import { ENGINE_META } from '../utils'
const props = defineProps({ server: Object, size: { type: Number, default: 44 } })
const failed = ref(false)
const failed2 = ref(false)
const meta = computed(() => ENGINE_META[props.server.type] || ENGINE_META.vanilla)
</script>
<style scoped>
.avatar { flex: none; border-radius: 10px; overflow: hidden; display: grid; place-items: center; background: var(--c); color: var(--ink); font-weight: 700; letter-spacing: -0.02em; }
.avatar img { width: 100%; height: 100%; object-fit: cover; image-rendering: pixelated; }
</style>
