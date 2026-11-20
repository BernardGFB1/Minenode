<template>
  <section class="card">
    <div class="card-head">
      <BarChart3 class="hi" /><h3>{{ $t('Publiczna strona serwera') }}</h3>
      <span v-if="v && !v.enabled" class="badge amber">{{ $t('wyłączona') }}</span>
      <a v-if="v?.enabled" :href="pageUrl" target="_blank" rel="noopener" class="btn sm ghost"><ExternalLink /> {{ $t('Otwórz') }}</a>
    </div>
    <div class="card-body col" style="gap: 16px">
      <div v-if="!v" class="row small muted"><Loader2 class="spin" style="width: 15px" /> {{ $t('Ładowanie ustawień…') }}</div>
      <template v-else>
        <div class="col" style="gap: 8px">
          <div class="small text-2">{{ $t('Strona ze statystykami jest dostępna bez logowania. Wyślij link graczom: zobaczą tylko to, co włączysz poniżej.') }}</div>
          <div class="input-group">
            <input class="input mono" :value="fullUrl" readonly @focus="$event.target.select()" />
            <button class="btn addon-btn" :title="$t('Kopiuj link')" @click="copyLink"><Copy /></button>
          </div>
        </div>

        <div class="divider" />

        <Toggle :model-value="v.enabled" :disabled="busy" :label="$t('Pokazuj publiczną stronę serwera')"
                :hint="$t('Wyłącz, aby nikt nie mógł otworzyć strony ani odczytać jej danych – łącznie z listą graczy i czatem.')"
                @update:model-value="(on) => save({ enabled: on })" />

        <template v-if="v.enabled">
          <div class="divider" />
          <div class="col" style="gap: 2px">
            <b class="small">{{ $t('Co pokazać na stronie') }}</b>
            <span class="tiny muted">{{ $t('Wyłączone sekcje znikają ze strony i nie są wysyłane w danych publicznych.') }}</span>
          </div>
          <Toggle v-for="o in OPTIONS" :key="o.key" :model-value="v[o.key]" :disabled="busy"
                  :label="$t(o.label)" :hint="$t(o.hint)" @update:model-value="(on) => save({ [o.key]: on })" />
        </template>

        <div v-else class="callout info small"><Info /><div>{{ $t('Publiczna strona jest wyłączona. Link powyżej nadal możesz skopiować – po włączeniu strony zacznie znów działać.') }}</div></div>
      </template>
    </div>
  </section>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { BarChart3, Copy, ExternalLink, Info, Loader2 } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { t } from '../i18n'
import { toast, toastError } from '../store'
import { copyText } from '../utils'

const props = defineProps({ serverId: String })
const v = ref(null)
const busy = ref(false)

const OPTIONS = [
  { key: 'online_players', label: 'Podgląd graczy online', hint: 'Lista graczy, którzy są teraz na serwerze.' },
  { key: 'join_guide', label: 'Poradnik, jak dołączyć', hint: 'Kroki dodania serwera w Minecraft i przycisk kopiowania adresu.' },
  { key: 'chat', label: 'Czat graczy', hint: 'Ostatnie wiadomości z czatu widoczne publicznie.' },
  { key: 'recent_players', label: 'Gracze ostatnio na serwerze', hint: 'Lista osób, które grały wcześniej, wraz z czasem ostatniej wizyty.' },
  { key: 'online_count', label: 'Aktualna liczba graczy online', hint: 'Kafelek z liczbą graczy online i limitem miejsc.' },
  { key: 'history_players', label: 'Gracze w historii', hint: 'Kafelek z łączną liczbą znanych graczy.' },
]

const pageUrl = computed(() => v.value?.public_url || '')
const fullUrl = computed(() => pageUrl.value ? `${window.location.origin}${pageUrl.value}` : '')

async function load() {
  try { v.value = await api.get(`/servers/${props.serverId}/public-page`) } catch (e) { toastError(e) }
}
onMounted(load)

async function copyLink() {
  if (fullUrl.value && await copyText(fullUrl.value)) toast(t('Skopiowano link'), 'success', 1800)
}

async function save(patch) {
  busy.value = true
  try {
    v.value = await api.post(`/servers/${props.serverId}/public-page`, patch)
    const key = Object.keys(patch)[0]
    if (key === 'enabled') toast(patch.enabled ? t('Publiczna strona włączona') : t('Publiczna strona wyłączona'), 'success')
    else toast(t('Ustawienia strony zapisane'), 'success', 1500)
  } catch (e) { toastError(e) } finally { busy.value = false }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); flex: none; }
.addon-btn { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; margin-left: -1px; height: auto; }
</style>
