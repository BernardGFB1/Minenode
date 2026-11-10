<template>
  <div class="col" style="gap: 12px">
    <LiveConsole ref="con" :entries="live.console" :connected="live.connected" :can-send="canSend" fill @command="send" />
    <div class="row wrap" style="gap: 6px">
      <span class="tiny muted">Szybkie komendy:</span>
      <button v-for="q in quick" :key="q.c" class="chip" :disabled="!canSend" @click="send(q.c)">{{ q.l }}</button>
    </div>
  </div>
</template>
<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import LiveConsole from '../../components/LiveConsole.vue'
import { api } from '../../api'
import { toastError } from '../../store'

const live = inject('live')
const con = ref(null)
const canSend = computed(() => ['online', 'starting'].includes(live.summary?.state))
const quick = [
  { l: 'list', c: 'list' }, { l: 'save-all', c: 'save-all' }, { l: 'Dzień', c: 'time set day' },
  { l: 'Pogoda: czysto', c: 'weather clear' }, { l: 'tps', c: 'tps' }
]
async function send(command) {
  try { await api.post(`/servers/${live.summary.id}/command`, { command }) } catch (e) { toastError(e) }
}
onMounted(() => con.value?.focus())
</script>
