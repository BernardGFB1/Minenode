<template>
  <div class="toasts">
    <TransitionGroup name="fade">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind" @click="dismissToast(t.id)">
        <CheckCircle2 v-if="t.kind === 'success'" />
        <AlertTriangle v-else-if="t.kind === 'error'" />
        <Info v-else />
        <span>{{ t.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>
<script setup>
import { CheckCircle2, AlertTriangle, Info } from 'lucide-vue-next'
import { toasts, dismissToast } from '../store'
</script>
<style scoped>
.toasts { position: fixed; right: 18px; bottom: 18px; display: flex; flex-direction: column; gap: 8px; z-index: 100; max-width: min(420px, calc(100vw - 36px)); }
.toast { display: flex; gap: 10px; align-items: flex-start; padding: 11px 14px; border-radius: var(--radius); background: var(--surface-2);
  border: 1px solid var(--border-strong); box-shadow: var(--shadow); cursor: pointer; font-size: 13.5px; }
.toast svg { width: 18px; height: 18px; flex: none; margin-top: 1px; color: var(--info); }
.toast.success svg { color: var(--accent); }
.toast.error { border-color: color-mix(in srgb, var(--danger) 45%, var(--border)); }
.toast.error svg { color: var(--danger); }
</style>
