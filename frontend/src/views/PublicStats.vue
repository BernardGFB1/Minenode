<template>
  <div class="pub">
    <header class="topbar">
      <a href="/" class="brand">
        <BrandMark />
        <span class="brand-name">MineNode</span>
      </a>
      <span class="spacer" />
      <router-link v-if="auth.user && data" :to="`/servers/${data.server.id}`" class="btn sm ghost"><Settings2 /> {{ $t('Zarządzaj w panelu') }}</router-link>
      <LangSwitch />
      <button class="btn ghost sm icon" :title="ui.theme === 'dark' ? $t('Jasny motyw') : $t('Ciemny motyw')" @click="toggleTheme">
        <Sun v-if="ui.theme === 'dark'" /><Moon v-else />
      </button>
    </header>

    <main class="page">
      <div v-if="loading && !data" class="card empty"><Loader2 class="spin" /><div>{{ $t('Ładowanie serwera…') }}</div></div>

      <div v-else-if="!data" class="card empty">
        <ServerOff />
        <h2 class="missing-title">{{ $t('Nie znaleziono serwera') }}</h2>
        <p>{{ error || $t('Ten adres nie prowadzi do żadnego serwera.') }}</p>
      </div>

      <template v-else>
        <header class="head">
          <ServerAvatar :server="s" :src="s.icon" :size="64" />
          <div class="grow" style="min-width: 0">
            <div class="row" style="gap: 10px">
              <h1 class="ellipsis">{{ s.name }}</h1>
              <StatusBadge :state="s.state" />
            </div>
            <div class="row wrap meta small">
              <span>{{ engineName }} <span class="mono">{{ s.mc_version }}</span></span>
              <span v-if="!s.online_mode">{{ $t('Konta non-premium mile widziane') }}</span>
              <span v-if="s.whitelist">{{ $t('Whitelista') }}</span>
            </div>
          </div>
          <button class="btn primary" @click="copyAddress"><Copy /> {{ $addr(s.address) }}</button>
        </header>

        <div class="grid grid-4">
          <div class="card tile">
            <div class="tl"><Users /> {{ $t('Gracze online') }}</div>
            <div class="tv num">{{ data.players.online }}<small> / {{ data.players.max }}</small></div>
            <Meter :value="data.players.online / (data.players.max || 1)" />
          </div>
          <div class="card tile">
            <div class="tl"><Timer /> {{ $t('Czas działania') }}</div>
            <div class="tv num">{{ isUp && s.started_at ? formatDuration(now - s.started_at) : '—' }}</div>
            <div class="tiny muted">{{ $t(STATE[s.state]?.label || s.state) }}</div>
          </div>
          <div class="card tile">
            <div class="tl"><Gamepad2 /> {{ $t('Wersja gry') }}</div>
            <div class="tv num">{{ s.mc_version }}</div>
            <div class="tiny muted ellipsis">{{ engineName }}<template v-if="s.loader_version"> · <span class="mono">{{ s.loader_version }}</span></template></div>
          </div>
          <div class="card tile">
            <div class="tl"><History /> {{ $t('Gracze w historii') }}</div>
            <div class="tv num">{{ data.players.known }}</div>
            <div class="tiny muted">{{ lastSeenText }}</div>
          </div>
        </div>

        <div class="grid grid-2">
          <section class="card">
            <div class="card-head"><h3>{{ $t('Online teraz') }}</h3><span class="badge" :class="{ green: data.online.length }">{{ data.online.length }}</span></div>
            <div v-if="!data.online.length" class="empty small"><Moon /><div>{{ isUp ? $t('Nikt teraz nie gra – wskocz pierwszy!') : $t('Serwer jest wyłączony.') }}</div></div>
            <ul v-else class="plist">
              <li v-for="p in data.online" :key="p.name">
                <img class="head-img" :src="headUrl(p.name)" alt="" referrerpolicy="no-referrer" loading="lazy" />
                <div class="grow">
                  <div class="pname ellipsis">{{ p.name }}</div>
                  <div class="tiny muted">{{ $t('Na serwerze od {t}', { t: formatDuration(Math.max(0, now - (p.joined_at || now))) }) }}</div>
                </div>
                <span class="dot green" />
              </li>
            </ul>
          </section>

          <section class="card">
            <div class="card-head"><h3>{{ $t('Jak dołączyć') }}</h3></div>
            <div class="card-body col" style="gap: 14px">
              <ServerListEntry :name="s.name" :motd="s.motd" :icon="s.icon" :players="`${data.players.online}/${data.players.max}`"
                               :version-label="isUp ? '' : '§cOffline'" />
              <ol class="steps small">
                <li>{{ $t('Otwórz Minecraft: Java Edition w wersji {v}.', { v: s.mc_version }) }}</li>
                <li>{{ $t('Wejdź w Tryb wieloosobowy → Dodaj serwer.') }}</li>
                <li>
                  {{ $t('Wpisz adres serwera:') }}
                  <button class="addr mono" :title="$t('Kopiuj adres')" @click="copyAddress"><Globe /> {{ $addr(s.address) }} <Copy /></button>
                </li>
              </ol>
            </div>
          </section>
        </div>

        <section v-if="data.offline.length" class="card">
          <div class="card-head">
            <h3>{{ $t('Ostatnio na serwerze') }}</h3>
            <input v-if="data.offline.length > 12" v-model="query" class="input search" :placeholder="$t('Szukaj gracza…')" />
          </div>
          <div class="recent">
            <div v-for="p in recentShown" :key="p.name" class="recent-item">
              <img class="head-img" :src="headUrl(p.name)" alt="" referrerpolicy="no-referrer" loading="lazy" />
              <div class="grow">
                <div class="pname ellipsis">{{ p.name }}</div>
                <div class="tiny muted" :title="formatDate(p.last_seen)">{{ timeAgo(p.last_seen) }}</div>
              </div>
            </div>
            <div v-if="!recentShown.length" class="tiny muted" style="padding: 4px 2px">{{ $t('Brak graczy pasujących do wyszukiwania.') }}</div>
          </div>
          <div v-if="recentFiltered.length > recentShown.length" class="more">
            <button class="btn sm ghost" @click="limit += 24">{{ $t('Pokaż więcej ({n})', { n: recentFiltered.length - recentShown.length }) }}</button>
          </div>
        </section>

        <section v-if="data.map.enabled" class="card">
          <div class="card-head">
            <h3>{{ $t('Mapa świata') }}</h3>
            <span v-if="data.map.available" class="badge green"><span class="dot green" />{{ $t('na żywo') }}</span>
            <a v-if="data.map.available" :href="data.map.url" target="_blank" rel="noopener" class="btn sm ghost"><Maximize2 /> {{ $t('Pełny ekran') }}</a>
          </div>
          <div v-if="data.map.available" class="map-frame">
            <iframe :src="data.map.url" :title="$t('Mapa {name}', { name: s.name })" loading="lazy" referrerpolicy="no-referrer" />
          </div>
          <div v-else class="card-body">
            <div class="callout info small"><Info /><div>{{ $t('Mapa pojawi się po pierwszym uruchomieniu serwera z squaremap. Wygenerowanie kafelków może zająć chwilę.') }}</div></div>
          </div>
        </section>

        <footer class="foot tiny muted">
          <span>{{ $t('Dane odświeżają się automatycznie.') }}</span>
          <span class="spacer" />
          <span>{{ $t('Strona serwera hostowanego w MineNode') }}</span>
        </footer>
      </template>
    </main>
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { Copy, Gamepad2, Globe, History, Info, Loader2, Maximize2, Moon, ServerOff, Settings2, Sun, Timer, Users } from 'lucide-vue-next'
import BrandMark from '../components/BrandMark.vue'
import LangSwitch from '../components/LangSwitch.vue'
import Meter from '../components/Meter.vue'
import ServerAvatar from '../components/ServerAvatar.vue'
import ServerListEntry from '../components/ServerListEntry.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api } from '../api'
import { t } from '../i18n'
import { auth, toast, toggleTheme, ui } from '../store'
import { copyText, ENGINE_META, formatDate, formatDuration, STATE, timeAgo } from '../utils'

const REFRESH_MS = 10_000

const route = useRoute()
const data = ref(null)
const error = ref('')
const loading = ref(true)
const now = ref(Date.now() / 1000)
const query = ref('')
const limit = ref(24)
let poll = null
let clock = null

const key = computed(() => String(route.params.key || ''))
const s = computed(() => data.value?.server)
const engineName = computed(() => ENGINE_META[s.value?.type]?.name || s.value?.type || '')
const isUp = computed(() => ['online', 'starting', 'stopping'].includes(s.value?.state))
const recentFiltered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return (data.value?.offline || []).filter((p) => !q || p.name.toLowerCase().includes(q))
})
const recentShown = computed(() => recentFiltered.value.slice(0, limit.value))
const lastSeenText = computed(() => {
  if (data.value?.online.length) return t('ktoś gra właśnie teraz')
  const last = data.value?.offline[0]?.last_seen
  return last ? t('ostatnio ktoś grał {t}', { t: timeAgo(last) }) : t('nikt jeszcze nie grał')
})

const headUrl = (name) => `https://mc-heads.net/avatar/${encodeURIComponent(name)}/40`

async function load() {
  try {
    data.value = await api.get(`/public/servers/${encodeURIComponent(key.value)}/stats`)
    error.value = ''
  } catch (err) {
    if (!data.value) error.value = err?.status === 404 ? t('Ten adres nie prowadzi do żadnego serwera.') : (err?.message || '')
  } finally {
    loading.value = false
  }
}

async function copyAddress() {
  if (await copyText(s.value?.address || '')) toast(t('Skopiowano adres'), 'success', 1800)
}

watch(key, () => { data.value = null; loading.value = true; query.value = ''; limit.value = 24; load() })
watchEffect(() => {
  if (data.value) document.title = `${s.value.name} · ${data.value.players.online}/${data.value.players.max} ${t('online')}`
})
onMounted(() => {
  load()
  poll = setInterval(() => document.visibilityState === 'visible' && load(), REFRESH_MS)
  clock = setInterval(() => (now.value = Date.now() / 1000), 1000)
})
onUnmounted(() => {
  clearInterval(poll)
  clearInterval(clock)
  document.title = 'MineNode'
})
</script>
<style scoped>
.pub { min-height: 100vh; background: var(--bg); }
.topbar { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 10px; height: 58px; padding: 0 24px;
  border-bottom: 1px solid var(--border); background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(8px); }
.brand { display: flex; align-items: center; gap: 10px; color: var(--text); text-decoration: none; }
.brand :deep(.mark) { width: 28px; height: 28px; }
.brand-name { font-weight: 650; letter-spacing: -0.01em; }
.page { max-width: 1120px; display: flex; flex-direction: column; gap: 16px; }

.head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 2px; }
.head h1 { font-size: 24px; }
.meta { color: var(--muted); gap: 6px 14px; margin-top: 4px; }
.missing-title { font-size: 17px; color: var(--text); margin: 4px 0 6px; }

.tile { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.tl { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--text-2); font-weight: 550; }
.tl svg { width: 15px; height: 15px; color: var(--muted); }
.tv { font-size: 24px; font-weight: 650; letter-spacing: -0.02em; }
.tv small { font-size: 13px; color: var(--muted); font-weight: 500; }

.head-img { width: 32px; height: 32px; border-radius: 7px; flex: none; image-rendering: pixelated; background: var(--surface-2); }
.pname { font-weight: 550; font-size: 13.5px; }
.plist { list-style: none; margin: 0; padding: 6px 20px; }
.plist li { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); min-width: 0; }
.plist li:last-child { border-bottom: 0; }
.dot.green { background: var(--accent); }
.empty.small { padding: 36px 20px; font-size: 13px; }

.steps { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 8px; color: var(--text-2); }
.addr { display: inline-flex; align-items: center; gap: 6px; margin-left: 4px; border: 1px solid var(--border); background: var(--surface-2);
  color: var(--text); border-radius: 6px; padding: 2px 8px; cursor: pointer; font-size: 12px; vertical-align: middle; }
.addr:hover { border-color: var(--border-strong); }
.addr svg { width: 13px; height: 13px; color: var(--muted); }

.search { width: 200px; height: 30px; font-size: 12.5px; }
.recent { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 6px 14px; padding: 14px 20px; }
.recent-item { display: flex; align-items: center; gap: 10px; padding: 6px 4px; min-width: 0; }
.recent-item .head-img { filter: saturate(0.7); }
.more { display: flex; justify-content: center; padding: 0 0 12px; }

.map-frame { padding: 12px; }
.map-frame iframe { display: block; width: 100%; height: min(70vh, 720px); min-height: 420px; border: 0; border-radius: var(--radius);
  background: var(--surface-2); }
.badge .dot { width: 6px; height: 6px; }

.foot { display: flex; gap: 12px; padding: 4px 2px 0; }

@media (max-width: 900px) {
  .grid-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid-2 { grid-template-columns: 1fr; }
}
@media (max-width: 560px) {
  .topbar { padding: 0 16px; }
  .page { padding: 18px 16px 40px; }
  .head .btn.primary { width: 100%; justify-content: center; }
  .search { width: 140px; }
  .foot { flex-direction: column; gap: 4px; }
}
</style>
