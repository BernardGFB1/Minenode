<template>
  <div class="col" style="gap: 16px; max-width: 980px">
    <div class="small muted">{{ $t('Opcjonalne usługi tego serwera. Każdą włączasz osobno – panel sam instaluje i konfiguruje to, czego potrzebuje.') }}</div>

    <section class="card">
      <div class="card-head">
        <LockKeyhole class="hi" /><h3>{{ $t('Autoryzacja graczy') }}</h3>
        <span v-if="live.summary.online_mode" class="badge">{{ $t('nie dotyczy (online-mode)') }}</span>
        <span v-else-if="authEnabled" class="badge green">{{ $t('włączona') }}</span>
        <span v-else class="badge amber">{{ $t('wyłączona') }}</span>
      </div>
      <div class="card-body col" style="gap: 12px">
        <Toggle :model-value="authEnabled" :disabled="authBusy" :label="$t('Wymagaj logowania na tym serwerze')"
                :hint="$t('Gracze bez sesji trafią do ukrytego lobby i użyją /register lub /login. Dotyczy tylko tego serwera offline-mode.')"
                @update:model-value="setAuth" />
        <div v-if="live.summary.online_mode" class="callout info small"><Info /><div>{{ $t('Serwer działa w trybie online-mode – konta weryfikuje Mojang, więc lobby logowania nie jest używane.') }}</div></div>
        <div v-else class="callout info small"><LockKeyhole /><div>{{ $t('Sesja trwa 30 minut od ostatniego wejścia lub wyjścia i jest przypisana do nicku oraz adresu IP.') }} <router-link to="/auth">{{ $t('Sesje i hasła graczy') }}</router-link></div></div>
      </div>
    </section>

    <VoiceChatCard :server-id="id" />

    <BedrockCard :server-id="id" />

    <WebMapCard :server-id="id" />

    <LodCard :server-id="id" />

    <ChunkyCard :server-id="id" />
  </div>
</template>
<script setup>
import { computed, inject, ref } from 'vue'
import { Info, LockKeyhole } from 'lucide-vue-next'
import Toggle from '../../components/Toggle.vue'
import VoiceChatCard from '../../components/VoiceChatCard.vue'
import BedrockCard from '../../components/BedrockCard.vue'
import WebMapCard from '../../components/WebMapCard.vue'
import LodCard from '../../components/LodCard.vue'
import ChunkyCard from '../../components/ChunkyCard.vue'
import { api } from '../../api'
import { toast, toastError } from '../../store'

const live = inject('live')
const id = live.summary.id
const authBusy = ref(false)
const authEnabled = computed(() => live.config?.auth_enabled ?? true)

async function setAuth(on) {
  authBusy.value = true
  try {
    const r = await api.patch(`/servers/${id}/config`, { auth_enabled: on })
    live.config = r.config
    toast(on ? 'Logowanie graczy włączone' : 'Logowanie graczy wyłączone', 'success')
  } catch (e) { toastError(e) } finally { authBusy.value = false }
}
</script>
<style scoped>
.hi { width: 16px; height: 16px; color: var(--muted); }
</style>
