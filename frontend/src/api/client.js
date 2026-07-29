const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const TOKEN_STORAGE_KEY = 'support_agent_token'

export function getToken() {
  return localStorage.getItem(TOKEN_STORAGE_KEY)
}

export function setToken(token) {
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_STORAGE_KEY)
}

/** Plain JSON request helper for the non-streaming endpoints. */
export async function apiFetch(path, { method = 'GET', body, auth = false } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (auth) {
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail || `Request failed: ${response.status}`)
  }
  if (response.status === 204) return null
  return response.json()
}

/**
 * Streams one chat turn from POST /chat as Server-Sent Events, invoking
 * the matching callback as each event arrives. SSE frames look like:
 *
 *   event: token
 *   data: {"content": "Hello"}
 *   <blank line>
 *
 * The fetch Streams API gives us the raw bytes; we decode and split them
 * into frames ourselves since the browser has no built-in SSE-over-POST
 * client (EventSource only supports GET).
 */
export async function streamChat({ message, conversationId, onToken, onToolCall, onDone, onError }) {
  const token = getToken()

  let response
  try {
    response = await fetch(`${API_BASE_URL}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ message, conversation_id: conversationId ?? null }),
    })
  } catch (err) {
    onError?.(err.message)
    return
  }

  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => ({}))
    onError?.(detail.detail || `Request failed: ${response.status}`)
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n')
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? '' // last chunk may be incomplete; keep for next read

    for (const frame of frames) {
      if (!frame.trim()) continue
      const eventLine = frame.split('\n').find((line) => line.startsWith('event:'))
      const dataLine = frame.split('\n').find((line) => line.startsWith('data:'))
      if (!eventLine || !dataLine) continue

      const eventType = eventLine.slice('event:'.length).trim()
      const data = JSON.parse(dataLine.slice('data:'.length).trim())

      if (eventType === 'token') onToken?.(data.content)
      else if (eventType === 'tool_call') onToolCall?.(data)
      else if (eventType === 'done') onDone?.(data)
    }
  }
}
