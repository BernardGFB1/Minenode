<template>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" :stroke-width="strokeWidth" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <template v-if="engine === 'paper'">
      <path d="M21 3L3 10.5l8 2.5 2.5 8z" /><path d="M21 3L11 13" /><path d="M3 17.5h3M2.5 21H7" />
    </template>
    <template v-else-if="engine === 'purpur'">
      <rect x="4.5" y="3" width="15" height="3.5" rx="1" /><rect x="4.5" y="17.5" width="15" height="3.5" rx="1" />
      <path d="M7 6.5v11M17 6.5v11M10.3 6.5v11M13.7 6.5v11" />
    </template>
    <template v-else-if="engine === 'fabric'">
      <rect x="3.5" y="3" width="12" height="3.5" rx="1" /><rect x="3.5" y="17.5" width="12" height="3.5" rx="1" />
      <path d="M5.5 6.5v11M13.5 6.5v11" /><path d="M5.5 10l8-1M5.5 14l8-1" /><path d="M18.5 2.5l2.5 11" />
      <path d="M13.5 12c3.5 0 5.5 2.5 4 5.5-.8 1.6.2 2.8 2 2.5" />
    </template>
    <template v-else-if="engine === 'quilt'">
      <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" /><path d="M12 3.5v17M3.5 12h17" />
      <path d="M6.5 6.5h2M15.5 15.5h2M6.5 17.5h2M15.5 6.5h2" stroke-dasharray="0.01 2.4" />
    </template>
    <template v-else-if="engine === 'forge'">
      <path :d="ANVIL" /><path d="M17 3.5l-1 2.5M20.5 5.5l-2.2 1.4M13.5 3l.4 2.6" />
    </template>
    <template v-else-if="engine === 'neoforge'">
      <path :d="ANVIL" /><rect x="6" y="6.5" width="7" height="3.5" rx="1" />
      <path d="M19 2.5l.8 2.2 2.2.8-2.2.8L19 8.5l-.8-2.2-2.2-.8 2.2-.8z" />
    </template>
    <template v-else-if="engine === 'custom'">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" />
      <path d="M10 12.5l-2 2 2 2M14 12.5l2 2-2 2" />
    </template>
    <template v-else>
      <!-- vanilla (and any unknown engine): a grass block -->
      <path d="M12 3l8 4.5-8 4.5-8-4.5z" /><path d="M4 7.5v9l8 4.5 8-4.5v-9" /><path d="M12 12v9" />
      <path d="M4 10.8l2 1.1 1.6-.6 2 1.6 2.4.9 2.4-.9 2-1.6 1.6.6 2-1.1" />
    </template>
  </svg>
</template>
<script setup>
/** Outline engine icons drawn on the same 24 px grid as the Lucide icons used across the panel. */
defineProps({ engine: { type: String, default: 'vanilla' }, strokeWidth: { type: [Number, String], default: 1.6 } })
const ANVIL = 'M2.5 10h12c2.5 0 4 1.1 4.5 2.5h-5V15h-2l1.5 3h2.5v3H7v-3h2.5L11 15H9.5C5.5 15 2.5 13.5 2.5 10z'
</script>
