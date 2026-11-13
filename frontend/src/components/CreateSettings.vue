<template>
  <div class="col" style="gap: 20px">
    <div class="group">
      <div class="section-title">{{ $t('Gracze i dostęp') }}</div>
      <div class="field narrow">
        <label>{{ $t('Maks. liczba graczy') }}</label>
        <input v-model.number="model.max_players" class="input" type="number" min="1" max="1000" />
      </div>
      <Toggle v-model="model.online_mode" :label="$t('Tylko konta premium (online-mode)')"
              :hint="$t('Wejdą tylko gracze z kupionym Minecraftem. Wyłącz, jeśli grają też osoby bez konta premium.')" />
      <Toggle v-if="!model.online_mode" v-model="model.auth_enabled" :label="$t('Logowanie na serwerze')"
              :hint="$t('Bez premium każdy może wpisać dowolny nick. Z logowaniem gracz ustawia hasło przy pierwszym wejściu i podaje je przy kolejnych.')" />
      <div v-if="!model.online_mode && !model.auth_enabled" class="callout warn small"><AlertTriangle />{{ $t('Bez logowania i bez premium każdy może wejść jako dowolny gracz – także jako operator.') }}</div>
      <Toggle v-model="model.whitelist" :label="$t('Whitelista')"
              :hint="$t('Wejdą tylko gracze z listy. Próby wejścia pozostałych zobaczysz w zakładce Gracze i dodasz ich jednym kliknięciem.')" />
      <Toggle v-model="model.bedrock" :disabled="!bedrock.supported && !model.bedrock" :label="$t('Bedrock Edition')"
              :hint="bedrock.supported ? $t('Gracze z telefonów i konsol wejdą tym samym adresem przez wspólny port Geysera.') : bedrock.reason ? $tm(bedrock.reason) : $t('Sprawdzanie zgodności…')" />
      <div v-if="model.bedrock" class="callout info small"><Info />{{ $t('Bedrock wymaga wyłączonego trybu online, więc gracze wchodzą bez weryfikacji Microsoftu – zostaw włączone logowanie.') }}</div>
    </div>

    <div class="group">
      <div class="section-title">{{ $t('Rozgrywka') }}</div>
      <div class="grid grid-2">
        <div class="field">
          <label>{{ $t('Domyślny tryb gry') }}</label>
          <select v-model="model.gamemode" class="select">
            <option v-for="[v, l] in GAMEMODES" :key="v" :value="v">{{ $t(l) }}</option>
          </select>
        </div>
        <div class="field">
          <label>{{ $t('Poziom trudności') }}</label>
          <select v-model="model.difficulty" class="select">
            <option v-for="[v, l] in DIFFICULTIES" :key="v" :value="v">{{ $t(l) }}</option>
          </select>
        </div>
      </div>
      <div class="toggles">
        <Toggle v-model="model.pvp" :label="$t('PvP')" :hint="$t('Walka między graczami')" />
        <Toggle v-model="model.hardcore" :label="$t('Hardcore')" :hint="$t('Śmierć = tryb obserwatora')" />
        <Toggle :model-value="model.allow_flight" :label="$t('Zezwalaj na latanie')" @update:model-value="setFlight" :hint="$t('Wymagane przez część modów – bez tego serwer wyrzuca graczy za „latanie”.')" />
      </div>
      <div class="field">
        <label>{{ $t('Zasięg widzenia (chunki)') }} <span class="mono muted">{{ model.view_distance }}</span></label>
        <input v-model.number="model.view_distance" type="range" min="2" max="32" />
        <span class="hint">{{ $t('Duży wpływ na wydajność') }}</span>
      </div>
    </div>

    <div class="group">
      <div class="section-title">{{ $t('Zachowanie serwera') }}</div>
      <Toggle v-model="model.start_on_join" :label="$t('Uruchamiaj, gdy ktoś chce wejść')"
              :hint="$t('Wyłączony serwer wystartuje sam, kiedy gracz spróbuje się połączyć.')" />
      <div class="field narrow">
        <label>{{ $t('Wyłączaj bez graczy') }}</label>
        <select v-model.number="model.idle_shutdown_minutes" class="select">
          <option v-for="[v, l] in IDLE" :key="v" :value="v">{{ $t(l) }}</option>
        </select>
        <span class="hint">{{ $t('Oszczędza RAM i CPU. Razem z opcją powyżej serwer działa tylko wtedy, gdy ktoś gra.') }}</span>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, watch } from 'vue'
import { AlertTriangle, Info } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'

const props = defineProps({ engine: String, mc: String })
const model = defineModel({ type: Object, required: true })

const GAMEMODES = [['survival', 'Przetrwanie'], ['creative', 'Kreatywny'], ['adventure', 'Przygoda'], ['spectator', 'Obserwator']]
const DIFFICULTIES = [['peaceful', 'Pokojowy'], ['easy', 'Łatwy'], ['normal', 'Normalny'], ['hard', 'Trudny']]
const IDLE = [[0, 'Nigdy'], [10, 'Po 10 min'], [30, 'Po 30 min'], [60, 'Po godzinie']]

const bedrock = ref({ supported: false, reason: null })
let seq = 0
async function checkBedrock() {
  const mine = ++seq
  bedrock.value = { supported: false, reason: null }
  if (!props.mc) return
  try {
    // Bedrock always runs with online-mode off, so ask about that setup.
    const r = await api.get(`/bedrock/check?${new URLSearchParams({ engine: props.engine, mc: props.mc, online_mode: 'false' })}`)
    if (mine !== seq) return
    bedrock.value = r
    if (!r.supported) model.value.bedrock = false
  } catch { /* keep it disabled */ }
}
watch(() => [props.engine, props.mc], checkBedrock, { immediate: true })

// Bedrock needs online-mode off (and then a login keeps nicknames safe); premium-only turns Bedrock off.
watch(() => model.value.bedrock, (on) => { if (on) { model.value.online_mode = false; model.value.auth_enabled = true } })
watch(() => model.value.online_mode, (on) => { if (on) model.value.bedrock = false })
// Mod engines need flight allowed (jetpacks, gliders…); follow the engine until the user decides.
let flightTouched = false
function setFlight(v) { model.value.allow_flight = v; flightTouched = true }
watch(() => props.engine, (e) => {
  if (!flightTouched) model.value.allow_flight = ['fabric', 'quilt', 'forge', 'neoforge'].includes(e)
}, { immediate: true })
</script>
<style scoped>
.group { display: flex; flex-direction: column; gap: 12px; }
.narrow { max-width: 260px; }
.toggles { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
</style>
