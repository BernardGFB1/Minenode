<template>
  <div class="lang" role="group" :aria-label="$t('Język')">
    <Languages class="ic" />
    <button v-for="l in LANGS" :key="l.id" type="button" :class="{ active: i18n.locale === l.id }" :title="l.name" @click="setLocale(l.id)">{{ l.label }}</button>
  </div>
</template>
<script setup>
import { Languages } from 'lucide-vue-next'
import { i18n, setLocale } from '../i18n'
const LANGS = [{ id: 'pl', label: 'PL', name: 'Polski' }, { id: 'en', label: 'EN', name: 'English' }]
</script>
<style scoped>
.lang { display: inline-flex; align-items: center; gap: 2px; padding: 2px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.ic { width: 14px; height: 14px; color: var(--muted); margin: 0 4px 0 6px; }
button { height: 24px; min-width: 32px; padding: 0 8px; border: 0; border-radius: 6px; background: none; color: var(--text-2); font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
button:hover { color: var(--text); }
button.active { background: var(--surface-3); color: var(--accent); }
</style>
