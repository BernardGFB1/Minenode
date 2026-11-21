<template>
  <Modal :open="open" :title="$t('Statystyki gracza')" width="760px" @close="$emit('close')">
    <div v-if="loading" class="empty small"><Loader2 class="spin" /><div>{{ $t('Wczytywanie statystyk…') }}</div></div>

    <div v-else-if="error" class="callout warn small"><TriangleAlert /><div>{{ error }}</div></div>

    <template v-else-if="data">
      <div class="who">
        <img :src="headUrl" alt="" referrerpolicy="no-referrer" />
        <div class="grow" style="min-width: 0">
          <div class="name ellipsis">{{ data.name || playerName }}</div>
          <div class="tiny muted mono ellipsis">{{ data.uuid }}</div>
        </div>
        <div v-if="data.last_seen" class="tiny muted">{{ $t('ostatnio {t}', { t: timeAgo(data.last_seen) }) }}</div>
      </div>

      <div class="summary">
        <div v-for="m in metrics" :key="m.key" class="stat">
          <div class="lbl"><component :is="m.icon" /> {{ $t(m.label) }}</div>
          <div class="val num">{{ m.value }}</div>
        </div>
      </div>

      <div v-if="lists.length" class="lists">
        <section v-for="c in lists" :key="c.key" class="listcard">
          <h4>{{ $t(c.label) }}</h4>
          <div v-for="item in c.items" :key="item.id" class="item">
            <span class="ellipsis" :title="item.id">{{ itemLabel(item.id) }}</span>
            <span class="count num">{{ number(item.count) }}</span>
          </div>
        </section>
      </div>
      <div v-else class="callout info small" style="margin-top: 16px">
        <Info /><div>{{ $t('Ten gracz nie ma jeszcze szczegółowych statystyk.') }}</div>
      </div>
    </template>
  </Modal>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { Footprints, Hammer, Info, Loader2, Pickaxe, Skull, Swords, Timer, TriangleAlert } from 'lucide-vue-next'
import Modal from './Modal.vue'
import { api } from '../api'
import { t, tm } from '../i18n'
import { formatDuration, timeAgo } from '../utils'

const props = defineProps({
  open: Boolean,
  serverKey: { type: String, default: '' },
  playerUuid: { type: String, default: '' },
  playerName: { type: String, default: '' },
})
defineEmits(['close'])

const data = ref(null)
const loading = ref(false)
const error = ref('')
let request = 0

const headUrl = computed(() => `https://mc-heads.net/avatar/${encodeURIComponent(data.value?.name || props.playerName)}/64`)
const number = (value) => Number(value || 0).toLocaleString()
const itemLabel = (id) => {
  if (!id) return ''
  const split = String(id).indexOf(':')
  const namespace = split >= 0 ? id.slice(0, split) : 'minecraft'
  const name = split >= 0 ? id.slice(split + 1) : id
  const pretty = name.replaceAll('_', ' ')
  return namespace === 'minecraft' ? pretty : `${namespace}: ${pretty}`
}

const metrics = computed(() => {
  const d = data.value
  if (!d) return []
  return [
    { key: 'play', icon: Timer, label: 'Czas gry', value: formatDuration(d.play_time_seconds) },
    { key: 'deaths', icon: Skull, label: 'Śmierci', value: number(d.deaths) },
    { key: 'mobs', icon: Swords, label: 'Zabójstwa mobów', value: number(d.mob_kills) },
    { key: 'players', icon: Swords, label: 'Zabójstwa graczy', value: number(d.player_kills) },
    { key: 'distance', icon: Footprints, label: 'Przebyty dystans', value: `${number(d.distance_km)} km` },
    { key: 'mined', icon: Pickaxe, label: 'Wydobyte bloki', value: number(d.blocks_mined) },
    { key: 'crafted', icon: Hammer, label: 'Wytworzone przedmioty', value: number(d.items_crafted) },
    { key: 'jumps', icon: Footprints, label: 'Skoki', value: number(d.jumps) },
  ]
})

const CATEGORIES = [
  ['mined', 'Najczęściej wydobywane'],
  ['crafted', 'Najczęściej wytwarzane'],
  ['killed', 'Pokonane moby'],
  ['killed_by', 'Śmierci od'],
  ['used', 'Używane przedmioty'],
  ['broken', 'Zniszczone narzędzia'],
  ['picked_up', 'Podniesione przedmioty'],
  ['dropped', 'Upuszczone przedmioty'],
]
const lists = computed(() => CATEGORIES
  .map(([key, label]) => ({ key, label, items: data.value?.top?.[key] || [] }))
  .filter((category) => category.items.length))

async function load() {
  if (!props.open || !props.playerUuid) return
  const current = ++request
  loading.value = true
  error.value = ''
  data.value = null
  try {
    const result = await api.get(`/public/servers/${encodeURIComponent(props.serverKey)}/players/${encodeURIComponent(props.playerUuid)}/stats`)
    if (current === request) data.value = result
  } catch (err) {
    if (current === request) error.value = tm(err?.message || '') || t('Nie udało się wczytać statystyk.')
  } finally {
    if (current === request) loading.value = false
  }
}

watch(() => [props.open, props.playerUuid, props.serverKey], load, { immediate: true })
</script>
<style scoped>
.who { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; }
.who img { width: 52px; height: 52px; border-radius: 10px; flex: none; image-rendering: pixelated; background: var(--surface-2); }
.name { font-size: 18px; font-weight: 650; letter-spacing: -0.01em; }
.summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.stat { padding: 11px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); min-width: 0; }
.lbl { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--muted); white-space: nowrap; }
.lbl svg { width: 13px; height: 13px; flex: none; }
.val { font-size: 19px; font-weight: 650; margin-top: 4px; letter-spacing: -0.01em; overflow-wrap: anywhere; }
.lists { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.listcard { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 11px 13px; background: var(--surface); }
.listcard h4 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 6px; }
.item { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 4px 0; border-top: 1px solid var(--border); font-size: 13px; }
.item:first-of-type { border-top: 0; }
.count { color: var(--text-2); flex: none; }
@media (max-width: 720px) {
  .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .lists { grid-template-columns: 1fr; }
  .who { flex-wrap: wrap; }
}
</style>
