<template>
  <div class="col" style="gap: 16px; max-width: 980px">
    <div class="small muted">{{ $t('Tutaj decydujesz, co widzą gracze na publicznej stronie serwera. Ustawienia mapy są wspólne z zakładką Dodatki → Usługi.') }}</div>

    <PublicPageCard :server-id="id" />

    <WebMapCard :server-id="id" />
  </div>
</template>
<script setup>
import { inject } from 'vue'
import PublicPageCard from '../../components/PublicPageCard.vue'
import WebMapCard from '../../components/WebMapCard.vue'

const live = inject('live')
const id = live.summary.id
</script>
