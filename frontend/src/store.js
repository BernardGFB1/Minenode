import { reactive } from 'vue'

export const auth = reactive({ user: null, setupRequired: false, version: '', ready: false })

// ---------------------------------------------------------------- toasts
export const toasts = reactive([])
let toastId = 0
export function toast(message, kind = 'info', timeout = 4000) {
  const id = ++toastId
  toasts.push({ id, message, kind })
  if (timeout) setTimeout(() => dismissToast(id), timeout)
}
export const toastError = (e) => toast(e?.message || String(e), 'error', 6000)
export function dismissToast(id) {
  const i = toasts.findIndex((t) => t.id === id)
  if (i >= 0) toasts.splice(i, 1)
}

// ---------------------------------------------------------------- confirm dialog
export const dialog = reactive({ open: false, title: '', message: '', confirmText: 'Potwierdź', danger: false, typeToConfirm: '', resolve: null })
export function confirmDialog({ title, message = '', confirmText = 'Potwierdź', danger = false, typeToConfirm = '' }) {
  return new Promise((resolve) => {
    Object.assign(dialog, { open: true, title, message, confirmText, danger, typeToConfirm, resolve })
  })
}

// ---------------------------------------------------------------- theme
const saved = (() => { try { return localStorage.getItem('minenode-theme') } catch { return null } })()
export const ui = reactive({ theme: saved || 'dark', sidebarOpen: false })
export function applyTheme() {
  document.documentElement.dataset.theme = ui.theme
}
export function toggleTheme() {
  ui.theme = ui.theme === 'dark' ? 'light' : 'dark'
  try { localStorage.setItem('minenode-theme', ui.theme) } catch {}
  applyTheme()
}
