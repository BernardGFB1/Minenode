import { auth } from './store'

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

async function request(method, url, body, { raw = false, form = false } = {}) {
  const headers = { 'X-Requested-With': 'minenode' }
  let payload
  if (body !== undefined) {
    if (form) payload = body
    else {
      headers['Content-Type'] = 'application/json'
      payload = JSON.stringify(body)
    }
  }
  const res = await fetch('/api' + url, { method, headers, body: payload, credentials: 'same-origin' })
  if (res.status === 401 && !url.startsWith('/auth')) {
    auth.user = null
    window.dispatchEvent(new CustomEvent('minenode:unauthorized'))
  }
  if (!res.ok) {
    let msg = res.statusText
    try {
      const data = await res.json()
      msg = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join('; ') : msg
    } catch {}
    throw new ApiError(msg || `Błąd ${res.status}`, res.status)
  }
  if (raw) return res
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res.text()
}

export const api = {
  get: (u) => request('GET', u),
  post: (u, b = {}) => request('POST', u, b),
  put: (u, b = {}) => request('PUT', u, b),
  patch: (u, b = {}) => request('PATCH', u, b),
  del: (u) => request('DELETE', u),
  upload: (u, formData) => request('POST', u, formData, { form: true }),
}

/** Upload with progress (fetch has no upload progress). */
export function uploadWithProgress(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api' + url)
    xhr.setRequestHeader('X-Requested-With', 'minenode')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total)
    xhr.onload = () => {
      let data = null
      try { data = JSON.parse(xhr.responseText) } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(data)
      else reject(new ApiError(data?.detail || `Błąd ${xhr.status}`, xhr.status))
    }
    xhr.onerror = () => reject(new ApiError('Błąd sieci', 0))
    xhr.send(formData)
  })
}

export function download(url) {
  const a = document.createElement('a')
  a.href = '/api' + url
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/** Reconnecting WebSocket for a server's live channel. */
export function connectServer(id, onMessage, onStatus) {
  let ws
  let closed = false
  let retry = 0
  let timer
  const open = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/api/servers/${id}/ws`)
    ws.onopen = () => { retry = 0; onStatus?.('open') }
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'batch') msg.messages.forEach(onMessage)
      else onMessage(msg)
    }
    ws.onclose = (e) => {
      onStatus?.('closed')
      if (closed || e.code === 4401 || e.code === 4404) return
      timer = setTimeout(open, Math.min(10000, 500 * 2 ** retry++))
    }
  }
  open()
  return {
    send: (obj) => ws?.readyState === 1 && ws.send(JSON.stringify(obj)),
    close: () => { closed = true; clearTimeout(timer); ws?.close() },
  }
}
