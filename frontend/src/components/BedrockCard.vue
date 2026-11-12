<template>
  <section class="card">
    <div class="card-head">
      <Box class="hi" /><h3>{{ $t('Bedrock Edition (Geyser)') }}</h3>
      <span v-if="v?.enabled && v.gateway_listening" class="badge green"><span class="dot" />{{ $t('włączony') }}</span>
      <span v-else-if="v?.enabled && v.gateway_error" class="badge red">{{ $t('błąd') }}</span>
      <span v-else-if="v?.enabled" class="badge amber">{{ $t('Uruchamianie') }}</span>
      <span v-else-if="v" class="badge">{{ $t('wyłączony') }}</span>
    </div>
    <div v-if="!v" class="card-body"><Loader2 class="spin" style="width: 16px" /></div>
    <div v-else class="card-body col" style="gap: 14px">
      <Toggle :model-value="v.enabled" :disabled="busy || (!v.enabled && !v.supported)" :label="$t('Udostępnij przez Bedrock')"
              :hint="$t('Gracze z Minecraft Bedrock Edition wejdą na ten serwer przez wspólny port Geysera. Proxy Java działa bez zmian.')"
              @update:model-value="toggle" />

       <div v-if="!v.supported" class="callout warn small">
         <AlertTriangle />
         <div><b>{{ $t('Bedrock nie jest dostępny dla tego serwera') }}</b><br>{{ v.compat?.reason }}</div>
       </div>

       <div v-if="v.supported && v.compat?.state !== 'native'" class="callout small">
         <div>
           <b>{{ $t('Zgodność Geysera') }}</b>: {{ v.compat?.server_version }} ← {{ v.compat?.geyser_java_version }}
           <span v-if="v.compat?.addon_version"> · {{ v.compat.addon }} {{ v.compat.addon_version }}</span>
           <span v-if="v.compat?.restart_required" class="pending"> · {{ $t('wymagany restart serwera') }}</span>
           <span v-else-if="v.compat?.addon_ready" class="ok"> · {{ $t('gotowe') }}</span>
         </div>
       </div>

       <template v-if="v.enabled">
        <dl class="info">
          <dt>{{ $t('Adres dla graczy') }}</dt><dd class="mono">{{ v.address || '—' }}</dd>
          <dt>{{ $t('Port') }}</dt><dd class="mono">{{ v.public_port }} TCP + UDP</dd>
          <dt>{{ $t('Bramka Bedrock') }}</dt>
          <dd>
            <span v-if="v.gateway_listening" class="ok">{{ $t('działa') }}</span>
            <span v-else-if="v.gateway_error" class="err">{{ $t('błąd') }}</span>
            <span v-else class="pending"><Loader2 class="spin" style="width: 12px; height: 12px" /> {{ $t('Uruchamianie…') }}</span>
          </dd>
          <dt>{{ $t('Adres połączenia gry') }}</dt>
          <dd class="mono">{{ v.advertise?.length ? v.advertise.join(', ') : $t('wykrywany automatycznie (STUN)') }}</dd>
        </dl>

        <div v-if="v.advertise_source === 'stun'" class="callout warn small">
          <AlertTriangle />
          <div>{{ $t('Panel nie zna publicznego adresu, więc Geyser zgaduje go przez STUN. Za routerem lub w Dockerze często trafia na zły port i gracze widzą serwer, ale nie mogą wejść. Wpisz publiczny adres w ustawieniach panelu.') }}
            <router-link to="/settings">{{ $t('Ustawienia panelu') }}</router-link>
          </div>
        </div>

        <div v-if="v.hostnames.length > 1" class="col" style="gap: 6px">
          <span class="small muted">{{ $t('Dostępne adresy') }}</span>
          <div class="row wrap" style="gap: 6px">
            <span v-for="h in v.hostnames" :key="h" class="chip mono">{{ h }}</span>
          </div>
        </div>

        <div v-if="!v.hostnames.length" class="callout warn small">
          <AlertTriangle />
          <div>{{ $t('Ustaw domenę bazową, publiczny adres lub alias hosta – bez tego gracze nie mają się z czym połączyć.') }}
            <router-link to="/settings">{{ $t('Ustawienia panelu') }}</router-link>
          </div>
        </div>

        <div v-if="v.gateway_error" class="callout warn small">
          <AlertTriangle />
          <div class="grow">
            <b>{{ $t('Bramka Bedrock nie działa:') }}</b> {{ v.gateway_error }}
            <div class="tiny muted" style="margin-top: 4px">{{ $t('Panel ponowi próbę automatycznie. Sprawdź, czy port nie jest zajęty przez inny program.') }}</div>
          </div>
        </div>
        <div v-else-if="!v.gateway_listening" class="callout small">
          <Loader2 class="spin" />
          <div>{{ $t('Uruchamianie Geysera… Pierwsze uruchomienie pobiera Geyser i Javę, może to potrwać kilka minut.') }}</div>
        </div>

        <pre v-if="v.gateway_error && v.gateway_log?.length" class="logbox mono">{{ v.gateway_log.join('\n') }}</pre>

        <div class="tiny muted">
          {{ $t('Gracze łączą się z tym samym adresem co w Java Edition, wybierając wersję Bedrock. Otwórz port {port} TCP i UDP na routerze/zaporze.', { port: v.public_port }) }}
           <span v-if="v.gateway_version"> Geyser {{ v.gateway_version }}.</span>
           <span v-if="v.router_version"> MineNodeRouter {{ v.router_version }}.</span>
        </div>
      </template>

      <div v-else class="small text-2">
        {{ $t('Bedrock jest niezależny od proxy Java: możesz go włączyć tylko dla wybranych serwerów. Bramka startuje automatycznie, gdy pierwszy serwer ma włączony Bedrock, i zatrzymuje się, gdy żaden go nie używa.') }}
      </div>
    </div>
  </section>
</template>
<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { Box, Loader2, AlertTriangle } from 'lucide-vue-next'
import Toggle from './Toggle.vue'
import { api } from '../api'
import { toast, toastError } from '../store'

const props = defineProps({ serverId: String })
const v = ref(null)
const busy = ref(false)
let timer = null

function stopPolling() {
  if (timer) clearTimeout(timer)
  timer = null
}

function schedulePoll(delay = 3000) {
  stopPolling()
  timer = setTimeout(() => load(), delay)
}

function needsPolling() {
  return !!v.value?.enabled
}

async function load() {
  try {
    v.value = await api.get(`/servers/${props.serverId}/bedrock`)
    if (needsPolling()) schedulePoll(v.value.gateway_error ? 10000 : 5000)
    else stopPolling()
  } catch (e) {
    stopPolling()
    toastError(e)
  }
}

onMounted(load)
onUnmounted(stopPolling)

async function toggle(enabled) {
  busy.value = true
  try {
    v.value = await api.post(`/servers/${props.serverId}/bedrock`, { enabled })
    toast(enabled ? 'Włączono Bedrock' : 'Wyłączono Bedrock', 'success')
    if (needsPolling()) schedulePoll(1500)
    else stopPolling()
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
.badge .dot { width: 7px; height: 7px; background: currentColor; }
.info { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; font-size: 13px; }
.info dt { color: var(--muted); }
.info dd { margin: 0; }
.ok { color: var(--accent); }
.err { color: var(--danger); }
.pending { display: inline-flex; align-items: center; gap: 6px; color: var(--warn); }
.logbox { margin: 0; padding: 10px 12px; border-radius: var(--radius); background: var(--surface-2); border: 1px solid var(--border);
          font-size: 11px; line-height: 1.45; max-height: 160px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
</style>
