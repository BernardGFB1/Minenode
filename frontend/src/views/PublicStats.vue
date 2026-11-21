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
      <div v-if="disabled" class="card empty">
        <EyeOff />
        <h2 class="missing-title">{{ $t('Publiczna strona jest wyłączona') }}</h2>
        <p>{{ $t('Administrator tego serwera wyłączył publiczną stronę statystyk.') }}</p>
      </div>

      <div v-else-if="loading && !data" class="card empty"><Loader2 class="spin" /><div>{{ $t('Ładowanie serwera…') }}</div></div>

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

        <div v-if="showStart" class="card start-card">
          <div class="start-icon"><Loader2 v-if="starting" class="spin" /><Play v-else /></div>
          <div class="grow" style="min-width: 0">
            <b>{{ starting ? $t('Uruchamianie serwera…') : $t('Serwer jest wyłączony') }}</b>
            <div class="tiny muted">{{ starting
              ? $t('Pierwszy gracz będzie mógł dołączyć po chwili.')
              : $t('Kliknij, aby go uruchomić. Pierwszy gracz będzie mógł dołączyć po chwili.') }}</div>
          </div>
          <button class="btn primary" :disabled="starting" @click="startServer">
            <Loader2 v-if="starting" class="spin" /><Play v-else /> {{ starting ? $t('Uruchamianie…') : $t('Uruchom serwer') }}
          </button>
        </div>

        <div class="grid" :class="`grid-${tiles}`">
          <div v-if="pub.online_count" class="card tile">
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
          <div v-if="pub.history_players" class="card tile">
            <div class="tl"><History /> {{ $t('Gracze w historii') }}</div>
            <div class="tv num">{{ data.players.known }}</div>
            <div class="tiny muted">{{ lastSeenText }}</div>
          </div>
        </div>

        <div v-if="pub.online_players || pub.join_guide" class="grid" :class="{ 'grid-2': pub.online_players && pub.join_guide }">
          <section v-if="pub.online_players" class="card">
            <div class="card-head"><h3>{{ $t('Online teraz') }}</h3><span class="badge" :class="{ green: data.online.length }">{{ data.online.length }}</span></div>
            <div v-if="!data.online.length" class="empty small"><Moon /><div>{{ isUp ? $t('Nikt teraz nie gra – wskocz pierwszy!') : $t('Serwer jest wyłączony.') }}</div></div>
            <ul v-else class="plist">
              <li v-for="p in data.online" :key="p.name">
                <img class="head-img" :src="headUrl(p.name)" alt="" referrerpolicy="no-referrer" loading="lazy" />
                <div class="grow">
                  <div class="pname ellipsis" @click="openStats(p.uuid || '', p.name)" :class="{ clickable: pub.player_stats }" :title="pub.player_stats ? $t('Kliknij, aby zobaczyć statystyki') : ''">{{ p.name }}</div>
                  <div class="tiny muted">{{ $t('Na serwerze od {t}', { t: formatDuration(Math.max(0, now - (p.joined_at || now))) }) }}</div>
                </div>
                <span class="pms deaths num" :title="$t('Liczba śmierci z logów serwera')"><Skull />{{ p.deaths }}</span>
                <span v-if="p.ping != null" class="pms num" :class="pingClass(p.ping)" :title="$t('Opóźnienie połączenia')">{{ p.ping }} ms</span>
                <span v-else class="dot green" />
              </li>
            </ul>
          </section>

          <section v-if="pub.join_guide" class="card">
            <div class="card-head"><h3>{{ $t('Jak dołączyć') }}</h3></div>
            <div class="card-body col" style="gap: 14px">
              <ServerListEntry :name="s.name" :motd="s.motd" :icon="s.icon" :players="pub.online_count ? `${data.players.online}/${data.players.max}` : ''"
                               :version-label="isUp ? '' : '§cOffline'" />
              <ol class="steps small">
                <li>{{ $t('Otwórz Minecraft: Java Edition w wersji {v}.', { v: s.mc_version }) }}</li>
                <li>{{ $t('Wejdź w Tryb wieloosobowy → Dodaj serwer.') }}</li>
                <li>
                  {{ $t('Wpisz adres serwera:') }}
                  <button class="addr mono" :title="$t('Kopiuj adres')" @click="copyAddress"><Globe /> {{ $addr(s.address) }} <Copy /></button>
                </li>
              </ol>
              <div v-if="lodMods.length" class="lod">
                <div class="lod-head"><Mountain /> <b>{{ $t('Daleki teren (LOD)') }}</b></div>
                <div class="tiny muted">{{ $t('Serwer wysyła uproszczony teren daleko poza zasięg widzenia. Zainstaluj jeden z modów w wersji dla Minecrafta {v}, a od razu zobaczysz odległe góry i wybrzeża.', { v: s.mc_version }) }}</div>
                <div v-for="m in lodMods" :key="m.id" class="lod-row">
                  <span class="lod-name">{{ m.name }}</span>
                  <span class="lod-links">
                    <a v-for="l in m.links" :key="l.url" :href="l.url" target="_blank" rel="noopener" class="lod-link">{{ l.name }}<ExternalLink /></a>
                  </span>
                </div>
                <div v-if="lodMods.some((m) => m.id === 'voxy')" class="tiny muted">{{ $t('Zamiast LOD Server Support może być Voxy Server Side – to ten sam mod.') }}</div>
              </div>
            </div>
          </section>
        </div>

        <section v-if="pub.chat" class="card chat-card">
          <div class="card-head">
            <h3>{{ $t('Czat graczy') }}</h3>
            <span class="spacer" />
            <span class="tiny muted chat-limit">{{ $t('ostatnie 50 wiadomości') }}</span>
            <span class="badge" :class="{ green: chat.length }">{{ chat.length }}</span>
          </div>
          <div v-if="!chat.length" class="chat-empty">
            <MessageSquare />
            <div>
              <div class="chat-empty-title">{{ isUp ? $t('Cisza na czacie') : $t('Brak ostatnich wiadomości') }}</div>
              <div class="tiny muted">{{ isUp ? $t('Wiadomości graczy pojawią się tutaj.') : $t('W logach serwera nie znaleziono wiadomości graczy.') }}</div>
            </div>
          </div>
          <div v-else ref="chatEl" class="chat-list" @scroll="onChatScroll">
            <div v-for="m in chat" :key="m.seq" class="chat-msg">
              <img class="chat-head" :src="headUrl(m.name)" alt="" referrerpolicy="no-referrer" loading="lazy" />
              <div class="chat-body">
                <div class="chat-meta">
                  <span class="chat-name" :style="{ color: nameColor(m.name) }">{{ m.name }}</span>
                  <span class="chat-time" :title="chatFullTime(m.t)">{{ chatTime(m.t) }}</span>
                </div>
                <div class="chat-text">{{ m.text }}</div>
              </div>
            </div>
          </div>
        </section>

        <section v-if="pub.recent_players && data.offline.length" class="card">
          <div class="card-head">
            <h3>{{ $t('Ostatnio na serwerze') }}</h3>
            <input v-if="data.offline.length > 12" v-model="query" class="input search" :placeholder="$t('Szukaj gracza…')" />
          </div>
          <div class="recent">
            <div v-for="p in recentShown" :key="p.name" class="recent-item">
              <img class="head-img" :src="headUrl(p.name)" alt="" referrerpolicy="no-referrer" loading="lazy" />
              <div class="grow">
                <div class="pname ellipsis" @click="openStats(p.uuid || '', p.name)" :class="{ clickable: pub.player_stats }" :title="pub.player_stats ? $t('Kliknij, aby zobaczyć statystyki') : ''">{{ p.name }}</div>
                <div class="tiny muted" :title="formatDate(p.last_seen)">{{ timeAgo(p.last_seen) }}</div>
              </div>
              <span class="pms deaths num" :title="$t('Liczba śmierci z logów serwera')"><Skull />{{ p.deaths }}</span>
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
            <div class="callout info small"><Info /><div>{{ data.map.needs_running && !s.running
              ? $t('Mapa ({m}) jest dostępna, gdy serwer działa.', { m: data.map.title })
              : $t('Mapa pojawi się po uruchomieniu serwera. Pierwsze kafelki mogą potrzebować chwili.') }}</div></div>
          </div>
        </section>

        <footer class="foot tiny muted">
          <span>{{ $t('Dane odświeżają się automatycznie.') }}</span>
          <span class="spacer" />
          <span>{{ $t('Strona serwera hostowanego w MineNode') }}</span>
        </footer>
      </template>
    </main>
    <PlayerStatsModal :open="statsOpen" :server-key="key" :player-uuid="statsPlayer.uuid" :player-name="statsPlayer.name" @close="closeStats" />
  </div>
</template>
<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { Copy, ExternalLink, EyeOff, Gamepad2, Globe, History, Info, Loader2, Maximize2, MessageSquare, Moon, Mountain, Play, ServerOff, Settings2, Skull, Sun, Timer, Users } from 'lucide-vue-next'
import BrandMark from '../components/BrandMark.vue'
import LangSwitch from '../components/LangSwitch.vue'
import Meter from '../components/Meter.vue'
import ServerAvatar from '../components/ServerAvatar.vue'
import ServerListEntry from '../components/ServerListEntry.vue'
import StatusBadge from '../components/StatusBadge.vue'
import PlayerStatsModal from '../components/PlayerStatsModal.vue'
import { api } from '../api'
import { pingClass } from '../pings'
import { t } from '../i18n'
import { auth, toast, toastError, toggleTheme, ui } from '../store'
import { copyText, ENGINE_META, formatDate, formatDuration, STATE, timeAgo } from '../utils'

const REFRESH_MS = 10_000

const route = useRoute()
const data = ref(null)
const error = ref('')
const loading = ref(true)
const disabled = ref(false)
const now = ref(Date.now() / 1000)
const query = ref('')
const limit = ref(24)
let poll = null
let clock = null

const key = computed(() => String(route.params.key || ''))
const s = computed(() => data.value?.server)
const engineName = computed(() => ENGINE_META[s.value?.type]?.name || s.value?.type || '')
const isUp = computed(() => ['online', 'starting', 'stopping'].includes(s.value?.state))
const pub = computed(() => data.value?.public_page || {
  enabled: true, online_players: true, join_guide: true, chat: true,
  recent_players: true, online_count: true, history_players: true, start_button: true,
})
const starting = ref(false)
const canStart = computed(() => ['offline', 'crashed'].includes(s.value?.state))
const showStart = computed(() => !!pub.value.start_button && (canStart.value || starting.value))
const tiles = computed(() => 2 + Number(pub.value.online_count) + Number(pub.value.history_players))
const recentFiltered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return (data.value?.offline || []).filter((p) => !q || p.name.toLowerCase().includes(q))
})
const recentShown = computed(() => recentFiltered.value.slice(0, limit.value))
const lastSeenText = computed(() => {
  if (data.value?.players.online) return t('ktoś gra właśnie teraz')
  if (!pub.value.recent_players) return t('od początku istnienia serwera')
  const last = data.value?.offline[0]?.last_seen
  return last ? t('ostatnio ktoś grał {t}', { t: timeAgo(last) }) : t('nikt jeszcze nie grał')
})
const LOD_MODS = {
  distant_horizons: { name: 'Distant Horizons', links: [{ name: 'Modrinth', url: 'https://modrinth.com/mod/distanthorizons' }] },
  voxy: { name: 'Voxy', links: [{ name: 'Voxy', url: 'https://modrinth.com/mod/voxy' }, { name: 'LOD Server Support', url: 'https://modrinth.com/mod/lod-server-support' }] },
}
const lodMods = computed(() => (data.value?.lod?.mods || []).filter((id) => LOD_MODS[id]).map((id) => ({ id, ...LOD_MODS[id] })))
const chat = computed(() => (data.value?.chat || []).slice(-50))
const chatLast = computed(() => chat.value.length ? chat.value[chat.value.length - 1].seq : 0)

const chatEl = ref(null)
let chatStick = true

const statsOpen = ref(false)
const statsPlayer = ref({ uuid: '', name: '' })

const headUrl = (name) => `https://mc-heads.net/avatar/${encodeURIComponent(name)}/40`
const chatFullTime = (t) => new Date((t || 0) * 1000).toLocaleString()

function chatTime(t) {
  const d = new Date((t || 0) * 1000)
  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return d.toDateString() === new Date().toDateString()
    ? time
    : `${d.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit' })} ${time}`
}

function nameColor(name) {
  let hue = 0
  for (let i = 0; i < name.length; i++) hue = (hue * 31 + name.charCodeAt(i)) % 360
  return ui.theme === 'dark' ? `hsl(${hue} 52% 72%)` : `hsl(${hue} 55% 38%)`
}

function onChatScroll() {
  const el = chatEl.value
  if (el) chatStick = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

watch(chatLast, async (value, previous) => {
  if (!value) { chatStick = true; return }
  await nextTick()
  const el = chatEl.value
  if (el && (chatStick || !previous)) el.scrollTop = el.scrollHeight
})

async function load() {
  try {
    data.value = await api.get(`/public/servers/${encodeURIComponent(key.value)}/stats`)
    disabled.value = false
    error.value = ''
  } catch (err) {
    if (err?.status === 403) {
      data.value = null
      disabled.value = true
      error.value = ''
    } else if (!data.value) {
      error.value = err?.status === 404 ? t('Ten adres nie prowadzi do żadnego serwera.') : (err?.message || '')
    }
  } finally {
    loading.value = false
  }
}

async function copyAddress() {
  if (await copyText(s.value?.address || '')) toast(t('Skopiowano adres'), 'success', 1800)
}

async function startServer() {
  if (starting.value) return
  starting.value = true
  try {
    await api.post(`/public/servers/${encodeURIComponent(key.value)}/start`)
    toast(t('Uruchamianie serwera…'), 'success', 2500)
    await load()
  } catch (err) {
    toastError(err)
  } finally {
    starting.value = false
  }
}

function openStats(uuid, name) {
  if (!pub.value.player_stats) return
  statsPlayer.value = { uuid, name }
  statsOpen.value = true
}

function closeStats() {
  statsOpen.value = false
  statsPlayer.value = { uuid: '', name: '' }
}

watch(key, () => { data.value = null; disabled.value = false; loading.value = true; query.value = ''; limit.value = 24; load() })
watchEffect(() => {
  if (data.value && pub.value.online_count) {
    document.title = `${s.value.name} · ${data.value.players.online}/${data.value.players.max} ${t('online')}`
  } else if (data.value) {
    document.title = `${s.value.name} · ${t('Publiczna strona ze statystykami serwera')}`
  } else if (disabled.value) {
    document.title = 'MineNode'
  }
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

.start-card { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 16px 18px; }
.start-card b { font-size: 14px; }
.start-icon { display: grid; place-items: center; width: 40px; height: 40px; flex: none; border-radius: 10px;
  background: var(--surface-2); color: var(--accent); border: 1px solid var(--border); }
.start-icon svg { width: 18px; height: 18px; }

.tile { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.tl { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--text-2); font-weight: 550; }
.tl svg { width: 15px; height: 15px; color: var(--muted); }
.tv { font-size: 24px; font-weight: 650; letter-spacing: -0.02em; }
.tv small { font-size: 13px; color: var(--muted); font-weight: 500; }

.head-img { width: 32px; height: 32px; border-radius: 7px; flex: none; image-rendering: pixelated; background: var(--surface-2); }
.pname { font-weight: 550; font-size: 13.5px; }
.pname.clickable { cursor: pointer; color: var(--accent); }
.plist { list-style: none; margin: 0; padding: 6px 20px; }
.plist li { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); min-width: 0; }
.plist li:last-child { border-bottom: 0; }
.dot.green { background: var(--accent); }
.pms { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--muted); padding: 2px 8px; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border); white-space: nowrap; }
.pms svg { width: 12px; height: 12px; }
.pms.deaths { color: var(--text-2); }
.pms.green { color: var(--accent); }
.pms.amber { color: var(--warn); }
.pms.red { color: var(--danger); }
.empty.small { padding: 36px 20px; font-size: 13px; }

.chat-limit { white-space: nowrap; }
.chat-list { max-height: 380px; overflow-y: auto; padding: 8px 10px 12px; overscroll-behavior: contain; }
.chat-msg { display: flex; gap: 11px; padding: 7px 10px; border-radius: var(--radius-sm); animation: chat-in 0.22s ease-out; }
.chat-msg:hover { background: var(--surface-2); }
.chat-head { width: 32px; height: 32px; border-radius: 7px; flex: none; image-rendering: pixelated; background: var(--surface-2); }
.chat-body { min-width: 0; flex: 1; }
.chat-meta { display: flex; align-items: baseline; gap: 8px; }
.chat-name { font-weight: 650; font-size: 13px; letter-spacing: -0.01em; }
.chat-time { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.chat-text { font-size: 13.5px; line-height: 1.45; color: var(--text-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.chat-empty { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 38px 20px 42px; text-align: center; color: var(--muted); }
.chat-empty > svg { width: 28px; height: 28px; opacity: 0.55; }
.chat-empty-title { color: var(--text-2); font-size: 13.5px; font-weight: 600; margin-bottom: 2px; }
@keyframes chat-in { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }

.steps { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 8px; color: var(--text-2); }
.addr { display: inline-flex; align-items: center; gap: 6px; margin-left: 4px; border: 1px solid var(--border); background: var(--surface-2);
  color: var(--text); border-radius: 6px; padding: 2px 8px; cursor: pointer; font-size: 12px; vertical-align: middle; }
.addr:hover { border-color: var(--border-strong); }
.addr svg { width: 13px; height: 13px; color: var(--muted); }

.lod { display: flex; flex-direction: column; gap: 8px; padding-top: 14px; border-top: 1px solid var(--border); }
.lod-head { display: flex; align-items: center; gap: 7px; font-size: 13px; }
.lod-head svg { width: 15px; height: 15px; color: var(--muted); }
.lod-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; padding: 8px 10px;
  border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.lod-name { font-weight: 600; font-size: 13px; }
.lod-links { display: flex; flex-wrap: wrap; gap: 6px; }
.lod-link { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; padding: 2px 8px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--surface); color: var(--text); text-decoration: none; }
.lod-link:hover { border-color: var(--border-strong); text-decoration: none; }
.lod-link svg { width: 12px; height: 12px; color: var(--muted); }
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
  .start-card .btn { width: 100%; justify-content: center; }
  .search { width: 140px; }
  .chat-limit { display: none; }
  .foot { flex-direction: column; gap: 4px; }
}
</style>
