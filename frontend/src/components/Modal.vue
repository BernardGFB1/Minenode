<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="overlay" @mousedown.self="$emit('close')">
        <div class="modal card" :style="{ maxWidth: width }" role="dialog" aria-modal="true">
          <div class="modal-head">
            <h2 class="grow">{{ title }}</h2>
            <button class="btn ghost sm icon" @click="$emit('close')"><X /></button>
          </div>
          <div class="modal-body"><slot /></div>
          <div v-if="$slots.footer" class="modal-foot"><slot name="footer" /></div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
<script setup>
import { onMounted, onUnmounted } from 'vue'
import { X } from 'lucide-vue-next'
const props = defineProps({ open: Boolean, title: String, width: { type: String, default: '520px' } })
const emit = defineEmits(['close'])
const onKey = (e) => props.open && e.key === 'Escape' && emit('close')
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>
<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(5, 8, 6, 0.6); display: grid; place-items: center; z-index: 80; padding: 16px; }
.modal { width: 100%; max-height: calc(100vh - 32px); display: flex; flex-direction: column; }
.modal-head { display: flex; align-items: center; gap: 10px; padding: 16px 20px; border-bottom: 1px solid var(--border); }
.modal-body { padding: 18px 20px; overflow-y: auto; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 14px 20px; border-top: 1px solid var(--border); }
</style>
