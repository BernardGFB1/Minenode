<template>
  <div class="picker">
    <button type="button" class="thumb" :title="$t('Wgraj ikonę')" :disabled="busy" @click="input?.click()">
      <img v-if="src" :src="src" alt="" />
      <ImagePlus v-else />
    </button>
    <div class="grow" style="min-width: 0">
      <div class="row wrap" style="gap: 6px">
        <button type="button" class="btn sm" :disabled="busy" @click="input?.click()"><Upload /> {{ $t('Wgraj ikonę') }}</button>
        <button type="button" class="btn sm ghost" :disabled="busy" @click="run(() => api.post(`/servers/${serverId}/icon/default`), 'Przywrócono ikonę MineNode')"><RotateCcw /> {{ $t('Ikona MineNode') }}</button>
        <button v-if="hasIcon" type="button" class="btn sm ghost danger" :disabled="busy" @click="run(() => api.del(`/servers/${serverId}/icon`), 'Usunięto ikonę')"><Trash2 /> {{ $t('Usuń') }}</button>
      </div>
      <div class="hint">{{ $t('PNG, JPG, WebP lub GIF – panel przytnie obraz do kwadratu 64×64 px.') }}</div>
    </div>
    <input ref="input" type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden @change="pick" />
  </div>
</template>
<script setup>
import { ref } from 'vue'
import { ImagePlus, Upload, RotateCcw, Trash2 } from 'lucide-vue-next'
import { api } from '../api'
import { toast, toastError } from '../store'

const props = defineProps({ serverId: String, src: String, hasIcon: Boolean })
const emit = defineEmits(['updated'])
const input = ref(null)
const busy = ref(false)

/** Center-crop any image to a 64×64 PNG, the only size Minecraft shows in the server list. */
async function toServerIcon(file) {
  const bmp = await createImageBitmap(file)
  const side = Math.min(bmp.width, bmp.height)
  let src = bmp
  let sx = (bmp.width - side) / 2
  let sy = (bmp.height - side) / 2
  let size = side
  // Halve big images step by step: a single 2048 → 64 resize looks jagged in some browsers.
  while (size / 2 >= 128) {
    const step = document.createElement('canvas')
    step.width = step.height = Math.floor(size / 2)
    const c = step.getContext('2d')
    c.imageSmoothingQuality = 'high'
    c.drawImage(src, sx, sy, size, size, 0, 0, step.width, step.height)
    src = step; sx = 0; sy = 0; size = step.width
  }
  const out = document.createElement('canvas')
  out.width = out.height = 64
  const ctx = out.getContext('2d')
  ctx.imageSmoothingEnabled = size > 64 // keep small pixel-art crisp when it is scaled up
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(src, sx, sy, size, size, 0, 0, 64, 64)
  bmp.close?.()
  return new Promise((resolve, reject) => out.toBlob((b) => (b ? resolve(b) : reject(new Error('Nie udało się przetworzyć obrazu'))), 'image/png'))
}

async function run(fn, okMsg) {
  busy.value = true
  try {
    const r = await fn()
    emit('updated', r)
    toast(okMsg, 'success', 2500)
    if (r.restart_required) toast('Serwer działa – nowa ikona pojawi się w grze po restarcie.', 'info', 5000)
  } catch (e) { toastError(e) } finally { busy.value = false }
}

async function pick(ev) {
  const file = ev.target.files?.[0]
  ev.target.value = ''
  if (!file) return
  let blob
  try { blob = await toServerIcon(file) } catch { return toast('Nie udało się odczytać obrazu', 'error') }
  const fd = new FormData()
  fd.append('file', blob, 'server-icon.png')
  await run(() => api.upload(`/servers/${props.serverId}/icon`, fd), 'Zapisano ikonę serwera')
}
</script>
<style scoped>
.picker { display: flex; gap: 12px; align-items: center; }
.thumb { width: 64px; height: 64px; flex: none; padding: 0; border-radius: 8px; border: 1px dashed var(--border-strong); background: var(--surface-2); color: var(--muted); display: grid; place-items: center; cursor: pointer; overflow: hidden; }
.thumb:hover { border-color: var(--accent); color: var(--accent); }
.thumb img { width: 100%; height: 100%; image-rendering: pixelated; display: block; }
.thumb svg { width: 22px; height: 22px; }
.hint { margin-top: 6px; font-size: 12px; color: var(--muted); }
</style>
