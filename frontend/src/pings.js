import { onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from './api'

export const PING_INTERVAL = 30000

/**
 * Player pings of one server, refreshed every 30 s – only while the view using it is mounted and
 * the browser tab is visible. A hidden tab stops polling; showing it again refreshes at once.
 * `names` (optional getter) refreshes early when someone joins, so new players do not wait 30 s.
 */
export function usePlayerPings(serverId, names) {
  const pings = ref({})
  let timer = null
  let inflight = false

  async function refresh() {
    if (inflight || document.visibilityState !== 'visible') return
    inflight = true
    try {
      pings.value = (await api.get(`/servers/${serverId()}/players/ping`)).pings || {}
    } catch {
      // keep the last values; the next tick retries
    } finally {
      inflight = false
    }
  }
  function start() {
    if (timer || document.visibilityState !== 'visible') return
    refresh()
    timer = setInterval(refresh, PING_INTERVAL)
  }
  function stop() {
    clearInterval(timer)
    timer = null
  }
  const onVisibility = () => (document.visibilityState === 'visible' ? start() : stop())

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    start()
  })
  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisibility)
    stop()
  })
  if (names) {
    watch(() => (names() || []).map((n) => n.toLowerCase()).sort().join('\n'), (now, before) => {
      const old = new Set((before || '').split('\n'))
      if (now && now.split('\n').some((n) => !old.has(n))) refresh()
    })
  }
  return { pings, refresh }
}

/** Badge colour for a ping in ms (null = not measured). */
export function pingClass(ms) {
  if (ms == null) return ''
  if (ms < 80) return 'green'
  if (ms < 200) return 'amber'
  return 'red'
}
