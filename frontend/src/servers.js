import { reactive } from 'vue'
import { api } from './api'

/** Shared, polled server list used by the sidebar and dashboard. */
export const fleet = reactive({ servers: [], loaded: false, system: null })
let timer = null
let users = 0

export async function refreshFleet() {
  try {
    fleet.servers = await api.get('/servers')
    fleet.loaded = true
  } catch {}
}

export function useFleet() {
  users++
  if (!timer) {
    refreshFleet()
    timer = setInterval(() => document.visibilityState === 'visible' && refreshFleet(), 4000)
  }
  return () => {
    users--
    if (users <= 0 && timer) { clearInterval(timer); timer = null; users = 0 }
  }
}
