<template>
  <div class="page" style="max-width: 1080px">
    <div class="page-head">
      <div class="grow">
        <h1>{{ $t('Autoryzacja') }}</h1>
        <div class="sub">{{ $t('Sesje graczy, hasła i odcisk klienta.') }}</div>
      </div>
      <button class="btn" :disabled="loading" @click="refresh"><RotateCcw :class="{ spin: loading }" /> {{ $t('Odśwież') }}</button>
    </div>

    <div class="col" style="gap: 16px">
      <section class="card">
        <div class="card-head">
          <LockKeyhole />
          <h3>{{ $t('Jak działa lobby autoryzacji') }}</h3>
          <span v-if="lobby?.running" class="badge green">{{ $t('lobby działa') }}</span>
          <span v-else class="badge amber">{{ $t('lobby uruchamiane') }}</span>
        </div>
        <div class="card-body col" style="gap: 16px">
          <div class="small text-2">{{ $t('Autoryzacja jest włączana osobno w ustawieniach każdego serwera offline-mode. Serwery online-mode korzystają z logowania Mojang.') }}</div>

          <div class="auth-flow">
            <div class="flow-step">
              <span class="step-num">1</span>
              <div><b>{{ $t('Gracz łączy się z serwerem offline-mode.') }}</b>
                <div class="small text-2">{{ $t('Proxy sprawdza, czy nick i adres IP mają ważną sesję.') }}</div></div>
            </div>
            <div class="flow-step">
              <span class="step-num">2</span>
              <div><b>{{ $t('Bez sesji gracz trafia do ukrytego lobby Paper.') }}</b>
                <div class="small text-2">{{ $t('Lobby działa w pustym świecie w trybie spectator i nie zajmuje miejsca na serwerze docelowym. Wydajność i zgodność wersji zapewniają ViaVersion, ViaBackwards i ViaRewind.') }}</div></div>
            </div>
            <div class="flow-step">
              <span class="step-num">3</span>
              <div><b>{{ $t('Gracz używa /register <hasło> <hasło> lub /login <hasło>.') }}</b>
                <div class="small text-2">{{ $t('Po poprawnym logowaniu lobby rozłącza gracza, a następne połączenie trafia już do właściwego serwera.') }}</div></div>
            </div>
            <div class="flow-step">
              <span class="step-num">4</span>
              <div><b>{{ $t('Sesja trwa 30 minut i jest przypisana do nicku oraz adresu IP.') }}</b>
                <div class="small text-2">{{ $t('Wejście i wyjście z serwera odświeżają sesję. Zmiana hasła lub wylogowanie w panelu kończy ją natychmiast.') }}</div></div>
            </div>
          </div>

          <div class="auth-meta">
            <div><span class="muted small">{{ $t('Status lobby') }}</span><b>{{ lobby?.running ? $t('lobby działa') : $t('lobby uruchamiane') }}</b></div>
            <div><span class="muted small">{{ $t('Port wewnętrzny lobby:') }}</span><span class="mono">{{ lobby?.port || '—' }}</span></div>
            <div><span class="muted small">{{ $t('Czas sesji') }}</span><span>{{ lobby?.session_minutes || 30 }} min</span></div>
            <div><span class="muted small">{{ $t('Zarejestrowani gracze') }}</span><span>{{ playerAccounts.length }}</span></div>
          </div>

          <div class="callout info small"><Info /><div>
            {{ $t('Hasła są przechowywane jako PBKDF2-HMAC-SHA256 (120 000 iteracji) i nigdy nie są pokazywane w panelu ani zwracane przez API. Usunięcie hasła pozwala graczowi zarejestrować się ponownie.') }}
          </div></div>
          <div v-if="lobby?.error" class="callout warn small"><Info />{{ lobby.error }}</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><Users /><h3>{{ $t('Aktywne sesje graczy') }}</h3>
          <span class="badge green">{{ filteredSessions.length }}</span>
        </div>
        <div class="card-body col" style="gap: 10px">
          <div class="row wrap">
            <div class="searchbox grow" style="max-width: 340px">
              <Search /><input v-model="sessionSearch" :placeholder="$t('Szukaj po nicku lub adresie IP')" />
            </div>
            <span class="spacer" />
            <button class="btn sm ghost" :disabled="loading" @click="load"><RotateCcw :class="{ spin: loading }" /> {{ $t('Odśwież') }}</button>
          </div>
          <table class="table">
            <thead><tr><th>{{ $t('Gracz') }}</th><th>{{ $t('Adres IP') }}</th><th>{{ $t('Odcisk sesji') }}</th><th>{{ $t('Sesja wygasa') }}</th><th /></tr></thead>
            <tbody>
              <tr v-for="sess in filteredSessions" :key="sess.name" class="clickable" :title="$t('Kliknij, aby zobaczyć szczegóły sesji')" @click="openSession(sess)">
                <td><b>{{ sess.name }}</b></td>
                <td class="mono small">{{ sess.ip }}</td>
                <td>
                  <span v-if="sess.fingerprint" class="badge green mono" :title="sess.fingerprint.hash">{{ sess.fingerprint.hash.slice(0, 12) }}</span>
                  <span v-else class="badge">{{ $t('brak danych') }}</span>
                </td>
                <td class="small">{{ formatDate(sess.expires_at) }}</td>
                <td style="text-align: right">
                  <button class="btn sm ghost" @click.stop="logoutPlayer(sess.name)"><LogOut /> {{ $t('Wyloguj') }}</button>
                </td>
              </tr>
              <tr v-if="!filteredSessions.length"><td colspan="5" class="muted small">{{ $t('Brak aktywnych sesji.') }}</td></tr>
            </tbody>
          </table>
          <div class="row small text-2" style="gap: 8px; align-items: center">
            <Fingerprint style="width: 15px; height: 15px" />
            <span>{{ $t('Kliknij sesję, aby zobaczyć ustawienia klienta, markę i odebrane pakiety Plugin Message.') }}</span>
          </div>
          <div class="small text-2">{{ $t('Odcisk jest zbierany wyłącznie dla sesji na serwerach offline-mode z włączoną autoryzacją.') }}</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><KeyRound /><h3>{{ $t('Zarejestrowani gracze') }}</h3>
          <span class="badge">{{ filteredAccounts.length }}</span>
        </div>
        <div class="card-body col" style="gap: 10px">
          <div class="row wrap">
            <div class="searchbox grow" style="max-width: 340px">
              <Search /><input v-model="accountSearch" :placeholder="$t('Szukaj po nicku lub adresie IP')" />
            </div>
            <span class="spacer" />
            <button class="btn sm ghost" :disabled="loading" @click="load"><RotateCcw :class="{ spin: loading }" /> {{ $t('Odśwież') }}</button>
          </div>
          <table class="table">
            <thead><tr><th>{{ $t('Gracz') }}</th><th>{{ $t('Ostatni adres IP') }}</th><th>{{ $t('Status') }}</th><th /></tr></thead>
            <tbody>
              <tr v-for="acc in filteredAccounts" :key="acc.name">
                <td><b>{{ acc.name }}</b></td>
                <td class="mono small">{{ acc.ip || '—' }}</td>
                <td>
                  <span v-if="acc.active" class="badge green">{{ $t('zalogowany') }}</span>
                  <span v-else class="badge">{{ $t('wymaga logowania') }}</span>
                </td>
                <td style="text-align: right; white-space: nowrap">
                  <button class="btn sm" @click="changePlayerPw(acc.name)"><KeyRound /> {{ $t('Zmień hasło') }}</button>
                  <button class="btn sm ghost icon" :title="$t('Usuń hasło')" @click="deletePlayerPw(acc.name)"><Trash2 /></button>
                </td>
              </tr>
              <tr v-if="!filteredAccounts.length"><td colspan="4" class="muted small">{{ $t('Brak zarejestrowanych graczy.') }}</td></tr>
            </tbody>
          </table>
          <div class="small text-2">{{ $t('Usunięcie hasła pozwala graczowi zarejestrować się ponownie. Hasła nie są nigdy pokazywane.') }}</div>
        </div>
      </section>
    </div>

    <Modal :open="!!detailName" :title="$t('Szczegóły sesji – {name}', { name: detailName })" width="780px" @close="closeDetail">
      <div v-if="detailLoading" class="row" style="justify-content: center; padding: 20px"><Loader2 class="spin" /> {{ $t('Ładowanie szczegółów…') }}</div>
      <div v-else-if="detailError" class="callout warn small"><Info />{{ detailError }}</div>
      <div v-else-if="detail" class="col" style="gap: 16px">
        <div class="session-facts">
          <div><span class="muted small">{{ $t('Gracz') }}</span><b>{{ detail.session.name }}</b></div>
          <div><span class="muted small">{{ $t('Adres IP') }}</span><span class="mono">{{ detail.session.ip }}</span></div>
          <div><span class="muted small">{{ $t('Sesja wygasa') }}</span><span>{{ formatDate(detail.session.expires_at) }}</span></div>
          <div><span class="muted small">{{ $t('Status') }}</span><span class="badge green">{{ $t('zalogowany') }}</span></div>
        </div>

        <template v-if="detail.fingerprint">
          <div class="fingerprint-box">
            <div class="row" style="gap: 10px; align-items: flex-start">
              <Fingerprint style="width: 20px; height: 20px; color: var(--accent); flex: none; margin-top: 2px" />
              <div class="grow">
                <div class="section-title" style="margin: 0">{{ $t('Odcisk klienta') }}</div>
                <div class="mono small hash">{{ detail.fingerprint.hash }}</div>
              </div>
              <button class="btn sm ghost" @click="copyFingerprint"><Copy /> {{ $t('Kopiuj') }}</button>
            </div>
            <div class="small text-2" style="margin-top: 8px">
              {{ $t('Odcisk powstaje z ustawień klienta, marki oraz nazw kanałów Plugin Message. Jest liczony tylko informacyjnie – logowanie nie jest jeszcze blokowane na jego podstawie.') }}
            </div>
            <div class="row wrap small text-2" style="gap: 14px; margin-top: 8px">
              <span>{{ $t('Protokół') }}: <span class="mono">{{ detail.fingerprint.protocol }}</span></span>
              <span>{{ $t('Ostatnia aktualizacja') }}: {{ formatDate(detail.fingerprint.updated_at) }}</span>
              <span>{{ $t('Przechwycone ramki') }}: {{ detail.fingerprint.frames }}</span>
            </div>
          </div>

          <div class="grid grid-2" style="align-items: start">
            <div class="detail-box">
              <div class="section-title">{{ $t('Ustawienia klienta') }}</div>
              <dl v-if="detail.fingerprint.client_info" class="fact-list">
                <dt>{{ $t('Język') }}</dt><dd class="mono">{{ detail.fingerprint.client_info.locale || '—' }}</dd>
                <dt>{{ $t('Zasięg renderowania') }}</dt><dd>{{ detail.fingerprint.client_info.view_distance ?? '—' }}</dd>
                <dt>{{ $t('Tryb czatu') }}</dt><dd>{{ chatModeLabel(detail.fingerprint.client_info.chat_mode) }}</dd>
                <dt>{{ $t('Kolory czatu') }}</dt><dd>{{ yesNo(detail.fingerprint.client_info.chat_colors) }}</dd>
                <dt>{{ $t('Ręka główna') }}</dt><dd>{{ mainHandLabel(detail.fingerprint.client_info.main_hand) }}</dd>
                <dt>{{ $t('Filtrowanie tekstu') }}</dt><dd>{{ yesNo(detail.fingerprint.client_info.text_filtering) }}</dd>
                <dt>{{ $t('Serwer na liście') }}</dt><dd>{{ yesNo(detail.fingerprint.client_info.allow_listings) }}</dd>
                <dt>{{ $t('Widoczne części skóry') }}</dt><dd>{{ skinPartsLabel(detail.fingerprint.client_info.skin_parts_enabled) }}</dd>
              </dl>
              <div v-else class="small muted">{{ $t('Nie odebrano pakietu Client Information.') }}</div>
            </div>

            <div class="detail-box">
              <div class="section-title">{{ $t('Marka klienta') }}</div>
              <div class="brand-value mono">{{ detail.fingerprint.brand || '—' }}</div>
              <div class="small text-2" style="margin-top: 6px">{{ $t('Marka jest odczytywana z kanału minecraft:brand lub MC|Brand.') }}</div>
              <div class="section-title" style="margin-top: 18px">{{ $t('Kanały Plugin Message') }}</div>
              <div class="chips">
                <span v-for="m in detail.fingerprint.plugin_messages" :key="m.channel" class="chip mono">{{ m.channel }}</span>
                <span v-if="!detail.fingerprint.plugin_messages.length" class="small muted">{{ $t('Brak odebranych kanałów.') }}</span>
              </div>
            </div>
          </div>

          <div>
            <div class="section-title">{{ $t('Odebrane pakiety Plugin Message') }}</div>
            <table class="table">
              <thead><tr><th>{{ $t('Kanał') }}</th><th>{{ $t('Rozmiar') }}</th><th>{{ $t('Liczba') }}</th><th>{{ $t('Skrót') }}</th></tr></thead>
              <tbody>
                <tr v-for="m in detail.fingerprint.plugin_messages" :key="m.channel">
                  <td class="mono small">{{ m.channel }}</td>
                  <td class="small">{{ formatBytes(m.bytes) }}</td>
                  <td class="small">{{ m.count }}</td>
                  <td class="mono small" :title="m.sha256">{{ (m.sha256 || '').slice(0, 16) }}…</td>
                </tr>
                <tr v-if="!detail.fingerprint.plugin_messages.length"><td colspan="4" class="muted small">{{ $t('Brak odebranych pakietów.') }}</td></tr>
              </tbody>
            </table>
            <div class="small text-2" style="margin-top: 8px">{{ $t('Treść pakietów nie jest zapisywana – panel pokazuje tylko nazwę kanału, rozmiar i skrót SHA-256.') }}</div>
          </div>
        </template>
        <div v-else class="callout info small"><Info /><div>
          {{ $t('Nie przechwycono jeszcze ustawień klienta dla tej sesji. Pojawią się po wejściu gracza na serwer lub do lobby autoryzacji.') }}
        </div></div>
      </div>
    </Modal>

    <Modal :open="!!playerPw.name" :title="$t('Nowe hasło gracza – {user}', { user: playerPw.name })" width="420px" @close="playerPw.name = null">
      <input v-model="playerPw.value" class="input" type="password" :placeholder="$t('Min. 6 znaków')" autocomplete="new-password" @keydown.enter="savePlayerPw" />
      <template #footer><button class="btn" @click="playerPw.name = null">{{ $t('Anuluj') }}</button><button class="btn primary" :disabled="playerPw.value.length < 6" @click="savePlayerPw">{{ $t('Zmień') }}</button></template>
    </Modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Copy, Fingerprint, Info, KeyRound, Loader2, LockKeyhole, LogOut, RotateCcw, Search, Trash2, Users } from 'lucide-vue-next'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { t } from '../i18n'
import { confirmDialog, toast, toastError } from '../store'
import { copyText, formatBytes, formatDate } from '../utils'

const loading = ref(false)
const lobby = ref(null)
const playerSessions = ref([])
const playerAccounts = ref([])
const sessionSearch = ref('')
const accountSearch = ref('')
const playerPw = reactive({ name: null, value: '' })
const detailName = ref(null)
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')

const filteredSessions = computed(() => {
  const q = sessionSearch.value.trim().toLowerCase()
  if (!q) return playerSessions.value
  return playerSessions.value.filter((p) => p.name.toLowerCase().includes(q) || p.ip.toLowerCase().includes(q))
})
const filteredAccounts = computed(() => {
  const q = accountSearch.value.trim().toLowerCase()
  if (!q) return playerAccounts.value
  return playerAccounts.value.filter((p) => p.name.toLowerCase().includes(q) || (p.ip || '').toLowerCase().includes(q))
})

let timer = null
onMounted(() => {
  load()
  timer = setInterval(load, 30000)
})
onUnmounted(() => clearInterval(timer))

async function load() {
  loading.value = true
  try {
    const [settings, sessions, accounts] = await Promise.all([
      api.get('/settings'), api.get('/player-auth/sessions'), api.get('/player-auth/accounts'),
    ])
    lobby.value = settings.auth_lobby || null
    playerSessions.value = sessions
    playerAccounts.value = accounts
  } catch (e) { toastError(e) } finally { loading.value = false }
}

async function refresh() {
  await load()
  if (detailName.value) await openSession({ name: detailName.value })
}

async function openSession(session) {
  detailName.value = session.name
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    detail.value = await api.get(`/player-auth/sessions/${encodeURIComponent(session.name)}`)
  } catch (e) {
    detailError.value = e?.message || t('Nie udało się pobrać szczegółów sesji.')
  } finally { detailLoading.value = false }
}

function closeDetail() {
  detailName.value = null
  detail.value = null
  detailError.value = ''
}

async function logoutPlayer(name) {
  const ok = await confirmDialog({ title: t('Wylogować gracza {name}?', { name }), message: t('Przy następnym wejściu gracz będzie musiał ponownie się zalogować. Hasło pozostanie zapisane.'), confirmText: t('Wyloguj'), danger: true })
  if (!ok) return
  try {
    await api.del(`/player-auth/sessions/${encodeURIComponent(name)}`)
    toast(t('Wylogowano gracza'), 'success')
    if (detailName.value === name) closeDetail()
    load()
  } catch (e) { toastError(e) }
}

function changePlayerPw(name) { playerPw.name = name; playerPw.value = '' }
async function savePlayerPw() {
  try {
    await api.put(`/player-auth/accounts/${encodeURIComponent(playerPw.name)}/password`, { password: playerPw.value })
    toast(t('Zmieniono hasło gracza'), 'success')
    playerPw.name = null
    if (detailName.value) closeDetail()
    load()
  } catch (e) { toastError(e) }
}

async function deletePlayerPw(name) {
  const ok = await confirmDialog({ title: t('Usunąć hasło gracza {name}?', { name }), message: t('Gracz nie będzie mógł się zalogować i przy następnym wejściu zarejestruje się ponownie. Obecna sesja również zostanie zakończona.'), confirmText: t('Usuń hasło'), danger: true })
  if (!ok) return
  try {
    await api.del(`/player-auth/accounts/${encodeURIComponent(name)}`)
    toast(t('Usunięto hasło gracza'), 'success')
    if (detailName.value === name) closeDetail()
    load()
  } catch (e) { toastError(e) }
}

async function copyFingerprint() {
  if (detail.value?.fingerprint?.hash && await copyText(detail.value.fingerprint.hash)) toast(t('Skopiowano odcisk'), 'success')
}

const chatModeLabel = (value) => ({ full: t('pełny'), commands: t('tylko komendy'), hidden: t('ukryty') }[value] || value || '—')
const mainHandLabel = (value) => ({ right: t('prawa'), left: t('lewa') }[value] || value || '—')
const yesNo = (value) => value == null ? '—' : (value ? t('tak') : t('nie'))
const skinPartsLabel = (parts) => parts?.length ? parts.map((p) => ({
  cape: t('peleryna'), jacket: t('kurtka'), left_sleeve: t('lewy rękaw'), right_sleeve: t('prawy rękaw'),
  left_pants: t('lewa nogawka'), right_pants: t('prawa nogawka'), hat: t('nakrycie głowy'),
}[p] || p)).join(', ') : '—'
</script>

<style scoped>
.card-head > svg { width: 17px; height: 17px; color: var(--muted); }
.auth-flow { display: flex; flex-direction: column; gap: 12px; }
.flow-step { display: flex; gap: 12px; align-items: flex-start; }
.step-num {
  width: 24px; height: 24px; flex: none; border-radius: 50%; display: grid; place-items: center;
  background: var(--accent-soft); color: var(--accent); font-size: 12px; font-weight: 650;
}
.auth-meta {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px;
  padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2);
}
.auth-meta > div { display: flex; flex-direction: column; gap: 3px; }
.auth-meta b, .auth-meta span:last-child { font-size: 13px; }
.searchbox { display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 11px; border-radius: var(--radius-sm); background: var(--bg-sunken); border: 1px solid var(--border-strong); min-width: 220px; }
.searchbox:focus-within { border-color: var(--accent); box-shadow: var(--ring); }
.searchbox svg { width: 15px; height: 15px; color: var(--muted); flex: none; }
.searchbox input { flex: 1; min-width: 0; background: none; border: 0; outline: none; color: var(--text); font: inherit; font-size: 13px; }
.clickable { cursor: pointer; }
.session-facts {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px;
  padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2);
}
.session-facts > div { display: flex; flex-direction: column; gap: 3px; }
.fingerprint-box { padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); }
.hash { word-break: break-all; color: var(--text-2); }
.detail-box { padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); min-width: 0; }
.fact-list { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); gap: 6px 12px; margin: 0; font-size: 13px; }
.fact-list dt { color: var(--muted); }
.fact-list dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.brand-value { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 3px 7px; border-radius: 5px; background: var(--bg-sunken); border: 1px solid var(--border); font-size: 11.5px; overflow-wrap: anywhere; }
@media (max-width: 900px) {
  .auth-meta, .session-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid-2 { grid-template-columns: 1fr; }
}
</style>
