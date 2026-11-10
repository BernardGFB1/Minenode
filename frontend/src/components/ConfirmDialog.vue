<template>
  <Modal :open="dialog.open" :title="dialog.title" width="460px" @close="finish(false)">
    <p v-if="dialog.message" class="text-2" style="white-space: pre-line">{{ dialog.message }}</p>
    <div v-if="dialog.typeToConfirm" class="field" style="margin-top: 14px">
      <label>Wpisz <b class="mono">{{ dialog.typeToConfirm }}</b>, aby potwierdzić</label>
      <input v-model="typed" class="input" autofocus @keydown.enter="ok && finish(true)" />
    </div>
    <template #footer>
      <button class="btn" @click="finish(false)">Anuluj</button>
      <button class="btn" :class="dialog.danger ? 'danger solid' : 'primary'" :disabled="!ok" @click="finish(true)">{{ dialog.confirmText }}</button>
    </template>
  </Modal>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import Modal from './Modal.vue'
import { dialog } from '../store'
const typed = ref('')
watch(() => dialog.open, () => (typed.value = ''))
const ok = computed(() => !dialog.typeToConfirm || typed.value === dialog.typeToConfirm)
function finish(v) {
  dialog.open = false
  dialog.resolve?.(v)
}
</script>
