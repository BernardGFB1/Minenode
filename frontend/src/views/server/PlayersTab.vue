<template>
  <div v-if="d" class="col" style="gap: 16px">
    <div class="card card-body row wrap" style="gap: 18px">
      <Toggle :model-value="d.whitelist_enabled" label="Whitelista" hint="Tylko gracze z listy mogą dołączyć" @update:model-value="toggleWl($event)" />
      <Toggle v-if="d.whitelist_enabled" :model-value="d.enforce_whitelist" label="Wymuszaj" hint="Wyrzuć graczy spoza listy po przeładowaniu" @update:model-value="toggleWl(true, $event)" />
      <span class="spacer" />
      <span class="badge" :class="d.online_mode ? 'green' : 'amber'">{{ d.online_mode ? 'online-mode: konta premium' : 'online-mode wyłączony' }}</span>
    </div>

    <!-- Pending whitelist requests -->
    <section v-if="d.whitelist_enabled && d.pending.length" class="card pending">
      <div class="card-head"><UserRoundPlus /><h3>Próby dołączenia spoza whitelisty</h3><span class="badge amber">{{ d.pending.length }}</span>
        <button class="btn sm" @click="addAll"><CheckCheck /> Dodaj wszystkich</button></div>
      <div class="plist">
        <div v-for="p in d.pending" :key="p.name" class="prow">
          <img :src="head(p.name)" alt="" referrerpolicy="no-referrer" />
          <div class="grow"><b>{{ p.name }}</b><div class="tiny muted">{{ timeAgo(p.ts) }} · {{ p.count }}× próba · przez {{ p.source === 'proxy' ? 'proxy' : 'log serwera' }}</div></div>
          <button class="btn sm primary" :disabled="busy[p.name]" @click="addWl(p.name)"><Loader2 v-if="busy[p.name]" class="spin" /><Plus v-else /> Dodaj do whitelisty</button>
          <button class="btn sm ghost icon" title="Odrzuć" @click="dismiss(p.name)"><X /></button>
        </div>
      </div>
    </section>
    <div v-else-if="d.whitelist_enabled" class="callout info small"><Info />Gdy ktoś spoza whitelisty spróbuje dołączyć, pojawi się tutaj – dodasz go jednym kliknięciem.</div>

    <div class="grid grid-2">
      <section class="card">
        <div class="card-head"><Radio /><h3>Online</h3><span class="badge">{{ d.online.length }}</span></div>
        <div class="plist">
          <div v-for="p in d.online" :key="p" class="prow">
            <img :src="head(p)" alt="" referrerpolicy="no-referrer" />
            <b class="grow">{{ p }}</b>
            <button v-if="!isOp(p)" class="btn sm" @click="opAdd(p)"><Crown /> OP</button>
            <button class="btn sm ghost" @click="kick(p)">Wyrzuć</button>
            <button class="btn sm ghost danger" @click="ban(p)">Ban</button>
          </div>
          <div v-if="!d.online.length" class="empty small">Nikt nie jest teraz online.</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><Crown /><h3>Operatorzy</h3><span class="badge">{{ d.ops.length }}</span></div>
        <form class="addrow" @submit.prevent="opAdd(opName)">
          <input v-model="opName" class="input" placeholder="Nick gracza" list="known-players" />
          <select v-model.number="opLevel" class="select" style="width: 170px" :title="d.running ? 'Gdy serwer działa, nadawany jest poziom domyślny' : ''">
            <option v-for="l in levels" :key="l.v" :value="l.v">{{ l.v }} – {{ l.l }}</option>
          </select>
          <button class="btn primary" :disabled="!opName.trim()"><Crown /> Nadaj OP</button>
        </form>
        <div class="plist">
          <div v-for="o in d.ops" :key="o.uuid" class="prow">
            <img :src="head(o.name)" alt="" referrerpolicy="no-referrer" />
            <div class="grow"><b>{{ o.name }}</b><div class="tiny muted">poziom {{ o.level }} · {{ levels.find((l) => l.v === o.level)?.l }}</div></div>
            <button class="btn sm ghost" @click="opRemove(o.name)">Odbierz OP</button>
          </div>
          <div v-if="!d.ops.length" class="empty small">Brak operatorów.</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><ListChecks /><h3>Whitelista</h3><span class="badge">{{ d.whitelist.length }}</span></div>
        <form class="addrow" @submit.prevent="addWl(wlName); wlName = ''">
          <input v-model="wlName" class="input" placeholder="Nick gracza" list="known-players" />
          <button class="btn primary" :disabled="!wlName.trim()"><Plus /> Dodaj</button>
        </form>
        <div class="plist">
          <div v-for="w in d.whitelist" :key="w.uuid" class="prow">
            <img :src="head(w.name)" alt="" referrerpolicy="no-referrer" />
            <div class="grow"><b>{{ w.name }}</b><div class="tiny muted mono">{{ w.uuid }}</div></div>
            <button class="btn sm ghost icon" title="Usuń z whitelisty" @click="removeWl(w.name)"><Trash2 /></button>
          </div>
          <div v-if="!d.whitelist.length" class="empty small">Whitelista jest pusta.</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><Ban /><h3>Zbanowani</h3><span class="badge">{{ d.banned.length }}</span></div>
        <div class="plist">
          <div v-for="b in d.banned" :key="b.uuid" class="prow">
            <img :src="head(b.name)" alt="" referrerpolicy="no-referrer" />
            <div class="grow"><b>{{ b.name }}</b><div class="tiny muted ellipsis">{{ b.reason }} · {{ b.source }}</div></div>
            <button class="btn sm ghost" @click="pardon(b.name)">Odbanuj</button>
          </div>
          <div v-if="!d.banned.length" class="empty small">Nikt nie jest zbanowany.</div>
        </div>
      </section>
    </div>
    <datalist id="known-players"><option v-for="k in d.known" :key="k.name" :value="k.name" /></datalist>
  </div>
  <div v-else class="empty"><Loader2 class="spin" /></div>
</template>
<script setup>
import { inject, onMounted, reactive, ref, watch } from 'vue'
import { UserRoundPlus, CheckCheck, Plus, X, Info, Radio, Crown, ListChecks, Ban, Trash2, Loader2 } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import { api } from '../../api'
import { confirmDialog, toast, toastError } from '../../store'
import { timeAgo } from '../../utils'

const live = inject('live')
const id = live.summary.id
const d = ref(null)
const wlName = ref('')
const opName = ref('')
const opLevel = ref(4)
const busy = reactive({})
const levels = [
  { v: 1, l: 'omijanie ochrony spawnu' }, { v: 2, l: 'komendy gracza / bloki' },
  { v: 3, l: 'moderacja (kick, ban)' }, { v: 4, l: 'pełny administrator' },
]
const head = (n) => `https://mc-heads.net/avatar/${n}/32`
const isOp = (n) => d.value.ops.some((o) => o.name.toLowerCase() === n.toLowerCase())

async function load() {
  try {
    d.value = await api.get(`/servers/${id}/players`)
    live.pendingCount = d.value.whitelist_enabled ? d.value.pending.length : 0
    opLevel.value = d.value.op_level_default
  } catch (e) { toastError(e) }
}
onMounted(load)
watch(() => live.tick, () => setTimeout(load, 300))

const act = async (fn, okMsg) => {
  try { await fn(); if (okMsg) toast(okMsg, 'success', 2200); await load() } catch (e) { toastError(e) }
}
async function toggleWl(enabled, enforce) {
  await act(() => api.post(`/servers/${id}/players/whitelist/toggle`, { enabled, enforce: enforce ?? null }), enabled ? 'Whitelista włączona' : 'Whitelista wyłączona')
}
async function addWl(name) {
  name = name.trim()
  if (!name) return
  busy[name] = true
  await act(() => api.post(`/servers/${id}/players/whitelist`, { name }), `Dodano ${name} do whitelisty`)
  busy[name] = false
}
async function addAll() { for (const p of [...d.value.pending]) await addWl(p.name) }
const removeWl = (name) => act(() => api.del(`/servers/${id}/players/whitelist/${name}`), `Usunięto ${name}`)
const dismiss = (name) => act(() => api.del(`/servers/${id}/players/pending/${name}`))
async function opAdd(name) {
  name = (name || '').trim()
  if (!name) return
  await act(() => api.post(`/servers/${id}/players/ops`, { name, level: opLevel.value }), `${name} jest teraz operatorem`)
  opName.value = ''
}
const opRemove = (name) => act(() => api.del(`/servers/${id}/players/ops/${name}`), `Odebrano OP graczowi ${name}`)
const kick = (name) => act(() => api.post(`/servers/${id}/players/kick`, { name }), `Wyrzucono ${name}`)
async function ban(name) {
  if (await confirmDialog({ title: `Zbanować ${name}?`, confirmText: 'Zbanuj', danger: true })) act(() => api.post(`/servers/${id}/players/ban`, { name }), `Zbanowano ${name}`)
}
const pardon = (name) => act(() => api.del(`/servers/${id}/players/bans/${name}`), `Odbanowano ${name}`)
</script>
<style scoped>
.card-head > svg { width: 17px; height: 17px; color: var(--muted); }
.pending { border-color: color-mix(in srgb, var(--warn) 45%, var(--border)); }
.pending .card-head > svg { color: var(--warn); }
.plist { display: flex; flex-direction: column; padding: 6px 8px; max-height: 360px; overflow-y: auto; }
.prow { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: var(--radius-sm); }
.prow:hover { background: var(--surface-2); }
.prow img { width: 30px; height: 30px; border-radius: 6px; image-rendering: pixelated; background: var(--surface-3); flex: none; }
.addrow { display: flex; gap: 8px; padding: 12px 18px 4px; }
.empty { padding: 20px; }
</style>
