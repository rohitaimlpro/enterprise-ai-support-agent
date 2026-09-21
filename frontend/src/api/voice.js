import { apiFetch, getToken } from './client'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const WS_URL = API_BASE_URL.replace(/^http/, 'ws') + '/voice'

const SAMPLE_RATE = 16000
const PING_INTERVAL_MS = 20000
const PLAYBACK_PREBUFFER_S = 0.2 // avoids choppy starts when the first frame arrives
// Interrupting works in two steps ("duck, then confirm"): when the microphone hears
// clear speech over the agent for DUCK_AFTER_MS, its audio is PAUSED at once, so it
// stops the instant you talk. The server then transcribes what it heard: if it was
// really you, the agent stops for good; if it was the agent's own echo or a stray
// "hmm", the server says so and playback resumes where it left off.
const DUCK_AFTER_MS = 300
const DUCK_SPEECH_PROB = 0.8 // per-frame voice probability that counts as "clearly speech"
const DUCK_SAFETY_MS = 6000 // if the server never answers, carry on rather than stay silent
// After the agent's audio ends, speakers/room still ring for a moment. Speech that
// starts inside this window is treated as the agent's own echo, not the user.
const ECHO_COOLDOWN_MS = 500
const MAX_RECONNECTS = 6
// Close codes where reconnecting would be pointless or unwanted (see voice/session.py):
// 4401 auth/expired, 4408 idle/session limit, 4409 replaced by another tab,
// 4429 server busy, 4503 voice not configured.
const FATAL_CODES = new Set([4401, 4408, 4409, 4429, 4503])

// The VAD libraries are loaded as global scripts from index.html (see there).
const VAD_ASSET_BASE = 'https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.31/dist/'
const ORT_ASSET_BASE = 'https://cdn.jsdelivr.net/npm/onnxruntime-web/dist/'

function floatToPcm16(float32) {
  const out = new Int16Array(float32.length)
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return out.buffer
}

/**
 * One voice call: microphone + VAD -> WebSocket -> queued audio playback.
 *
 * State (reported via onState): 'connecting' | 'listening' | 'thinking' |
 * 'speaking' | 'reconnecting' | 'off'. Server JSON events are passed straight
 * to onEvent so the chat UI can render captions, tool badges and transcripts
 * the same way it does for typed chat.
 */
export class VoiceClient {
  /**
   * voice: an Azure voice name from /voice/voices (optional; server default otherwise).
   * allowInterrupt: let the user talk over the agent (pause -> confirm, see above).
   *   When false the microphone is ignored while the agent talks and for 0.5 s after.
   */
  constructor({ onEvent, onState, voice = null, allowInterrupt = true, duckSafetyMs = DUCK_SAFETY_MS }) {
    this.onEvent = onEvent
    this.onState = onState
    this.voice = voice
    this.allowInterrupt = allowInterrupt
    this.duckSafetyMs = duckSafetyMs
    this.playUntilMs = 0 // performance.now() when queued audio should finish
    this.speechDuringAgent = false // the utterance now in progress began while the agent was audible
    this.ducked = false // the agent's audio is paused, waiting for the server's verdict
    this.duckStartedMs = 0
    this.duckTimer = null
    this.speechRunStart = 0
    this.ws = null
    this.vad = null
    this.stream = null
    this.ctx = null
    this.sources = new Set()
    this.nextPlayTime = 0
    this.currentTurn = null // audio for any other turn id is stale and dropped
    this.conversationId = null
    this.stopped = true
    this.muted = false
    this.reconnects = 0
    this.pingTimer = null
    this.idleTimer = null
    this.state = 'off'
  }

  _setState(state) {
    if (this.state === state) return
    this.state = state
    this.onState?.(state)
  }

  _emit(type, extra = {}) {
    this.onEvent?.({ type, ...extra })
  }

  async start(conversationId) {
    this.stopped = false
    this.conversationId = conversationId ?? null
    this._setState('connecting')

    // Created inside the click handler's call stack: browsers refuse to
    // start audio playback otherwise (autoplay policy).
    this.ctx = new (window.AudioContext || window.webkitAudioContext)()
    await this.ctx.resume()

    try {
      await this._startMic()
    } catch (err) {
      this._emit('client_error', {
        code: 'mic_unavailable',
        message:
          err?.name === 'NotAllowedError'
            ? 'Microphone permission was denied. Allow it in the browser, or use text chat.'
            : `Could not open the microphone: ${err?.message || err}`,
      })
      this.stop()
      return
    }
    this._connect()
  }

  async _startMic() {
    if (!window.vad?.MicVAD) throw new Error('Voice detection library failed to load')
    this.vad = await window.vad.MicVAD.new({
      baseAssetPath: VAD_ASSET_BASE,
      onnxWASMBasePath: ORT_ASSET_BASE,
      redemptionMs: 800, // tolerate mid-sentence pauses before calling it end-of-utterance
      // echoCancellation matters here: without it the agent's own voice from
      // the speakers gets picked up and transcribed as the user speaking.
      getStream: async () => {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
        return this.stream
      },
      onSpeechStart: () => this._onSpeechStart(),
      onFrameProcessed: (probs) => this._onFrame(probs.isSpeech),
      onVADMisfire: () => this._resolveDuck(true), // too short to be speech: carry on
      onSpeechEnd: (audio) => this._onSpeechEnd(audio),
    })
    await this.vad.start()
  }

  // ---- speaking / interrupting ----------------------------------------------

  /** True while the agent's audio is playing, or has only just stopped. */
  _agentAudible() {
    return this._isPlaying() || performance.now() < this.playUntilMs + ECHO_COOLDOWN_MS
  }

  /** Runs on every audio frame the VAD analyses (~30-100 ms). */
  _onFrame(speechProbability) {
    if (this.muted || !this.allowInterrupt || !this._agentAudible()) {
      this.speechRunStart = 0
      return
    }
    if (speechProbability >= DUCK_SPEECH_PROB) {
      const now = performance.now()
      if (!this.speechRunStart) this.speechRunStart = now
      if (!this.ducked && now - this.speechRunStart >= DUCK_AFTER_MS) this._duck()
    } else if (speechProbability < 0.4) {
      this.speechRunStart = 0
    }
  }

  /** Pause the agent's audio immediately. It is resumed or discarded once the server decides. */
  _duck() {
    if (this.ducked || !this.ctx) return
    console.info('[voice] you are speaking over the agent: paused it, waiting to confirm')
    this.ducked = true
    this.duckStartedMs = performance.now()
    this.ctx.suspend()
    this._setState('listening')
    clearTimeout(this.duckTimer)
    this.duckTimer = setTimeout(() => this._resolveDuck(true), this.duckSafetyMs)
  }

  /**
   * End a pause. keepAudio=true: it was a false alarm (echo, "hmm", noise), carry on
   * from where the agent stopped. keepAudio=false: you really interrupted, drop the rest.
   */
  _resolveDuck(keepAudio) {
    if (!this.ducked) return
    this.ducked = false
    clearTimeout(this.duckTimer)
    this.speechRunStart = 0
    if (keepAudio) {
      // the audio clock stood still while paused, so the finish time moved by that long
      this.playUntilMs += performance.now() - this.duckStartedMs
      if (this._isPlaying()) this._setState('speaking')
    } else {
      this._flushPlayback()
      this.currentTurn = null
    }
    this.ctx?.resume()
  }

  _onSpeechStart() {
    // Remember whether this speech began under the agent's voice: if so it is either
    // an interruption (when allowed) or the agent's own echo -- the server tells them apart.
    this.speechDuringAgent = this._agentAudible()
  }

  _onSpeechEnd(audio) {
    const duringAgent = this.speechDuringAgent
    this.speechDuringAgent = false
    this.speechRunStart = 0
    if (this.muted) {
      this._resolveDuck(true)
      return
    }
    if (duringAgent && !this.allowInterrupt) {
      // Interrupting is off: while the agent talks the microphone is ignored, so its own
      // voice coming back through the speakers can never cut off its answer.
      console.info('[voice] ignored speech that started while the agent was talking')
      return
    }
    if (this.ws?.readyState !== WebSocket.OPEN) {
      this._resolveDuck(true)
      this._emit('client_error', { code: 'offline', message: 'Reconnecting... please repeat that in a moment.' })
      return
    }
    // Sent even while paused: the server decides whether this really was an interruption.
    this.ws.send(floatToPcm16(audio))
    if (!this.ducked) this._setState('thinking')
  }

  /** The user pressed Stop: silence the agent right now, no confirmation needed. */
  interruptNow() {
    this._resolveDuck(false)
    this._flushPlayback()
    this.currentTurn = null
    this._send({ type: 'interrupt' })
    this._setState('listening')
  }

  setAllowInterrupt(allow) {
    this.allowInterrupt = allow
    if (!allow) this._resolveDuck(true)
  }

  setMuted(muted) {
    this.muted = muted
    if (muted) this._resolveDuck(true)
  }

  // ---- websocket ----------------------------------------------------------

  _connect() {
    const ws = new WebSocket(WS_URL)
    ws.binaryType = 'arraybuffer'
    this.ws = ws

    ws.onopen = () => {
      // Token goes in the first message, not the URL, so it never lands in server logs.
      ws.send(JSON.stringify({ type: 'auth', token: getToken(), conversation_id: this.conversationId, voice: this.voice }))
      this.pingTimer = setInterval(() => this._send({ type: 'ping' }), PING_INTERVAL_MS)
    }
    ws.onmessage = (e) => (typeof e.data === 'string' ? this._onJson(JSON.parse(e.data)) : this._onAudio(e.data))
    ws.onclose = (e) => this._onClose(e)
    ws.onerror = () => {} // onclose always follows and carries the code
  }

  _send(obj) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj))
  }

  _onClose(e) {
    clearInterval(this.pingTimer)
    this._flushPlayback()
    if (this.stopped) return
    if (FATAL_CODES.has(e.code) || this.reconnects >= MAX_RECONNECTS) {
      if (!FATAL_CODES.has(e.code)) {
        this._emit('client_error', { code: 'connection_lost', message: 'Voice connection lost. Use text chat or restart the call.' })
      }
      this.stop()
      return
    }
    // Flaky network: reconnect with the same conversation; the server
    // rehydrates its history, so the call picks up where it left off.
    this._setState('reconnecting')
    const delay = Math.min(500 * 2 ** this.reconnects, 8000)
    this.reconnects += 1
    setTimeout(() => !this.stopped && this._connect(), delay)
  }

  _onJson(msg) {
    switch (msg.type) {
      case 'ready':
        this.reconnects = 0
        if (msg.conversation_id) this.conversationId = msg.conversation_id
        this._setState('listening')
        break
      case 'transcript':
        // The server accepted this as a real request, so the agent's old answer is
        // superseded: discard whatever was still queued (and un-pause the audio clock).
        this._resolveDuck(false)
        break
      case 'turn_start':
        this.currentTurn = msg.turn_id
        this._setState('thinking')
        break
      case 'done':
        if (msg.conversation_id) this.conversationId = msg.conversation_id
        if (!this._isPlaying()) this._setState('listening')
        break
      case 'speaking_end':
        this._scheduleListening()
        break
      case 'hangup': {
        // The user said goodbye. Let the farewell finish playing, then end the call.
        const remainingMs = Math.max(0, this.playUntilMs - performance.now())
        setTimeout(() => !this.stopped && this.stop(), remainingMs + 400)
        break
      }
      case 'interrupted':
        this._resolveDuck(false)
        this._flushPlayback()
        this.currentTurn = null
        this._setState('listening')
        break
      case 'ignored':
        // Echo, a stray "hmm", noise: not an interruption. If we paused the agent, resume it.
        this._resolveDuck(true)
        if (this.state === 'thinking') this._setState('listening')
        break
      case 'error':
        if (msg.code === 'stt_failed' || !msg.recoverable) this._setState(this.stopped ? 'off' : 'listening')
        break
      default:
    }
    this.onEvent?.(msg)
  }

  // ---- playback -----------------------------------------------------------

  _isPlaying() {
    return !!this.ctx && this.ctx.currentTime < this.nextPlayTime
  }

  _onAudio(buffer) {
    const view = new DataView(buffer)
    const turnId = view.getUint32(0) // big-endian, matches struct ">II" on the server
    if (turnId !== this.currentTurn || !this.ctx) return // late audio from an interrupted turn

    const pcm = new Int16Array(buffer.slice(8))
    const samples = new Float32Array(pcm.length)
    for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 0x8000

    const audioBuffer = this.ctx.createBuffer(1, samples.length, SAMPLE_RATE)
    audioBuffer.copyToChannel(samples, 0)
    const source = this.ctx.createBufferSource()
    source.buffer = audioBuffer
    source.connect(this.ctx.destination)

    const now = this.ctx.currentTime
    if (this.nextPlayTime < now) this.nextPlayTime = now + PLAYBACK_PREBUFFER_S
    source.start(this.nextPlayTime)
    this.nextPlayTime += audioBuffer.duration
    this.playUntilMs = performance.now() + (this.nextPlayTime - this.ctx.currentTime) * 1000
    this.sources.add(source)
    source.onended = () => this.sources.delete(source)
    this._setState('speaking')
  }

  _scheduleListening() {
    clearTimeout(this.idleTimer)
    const wait = Math.max(0, (this.nextPlayTime - (this.ctx?.currentTime ?? 0)) * 1000) + 50
    this.idleTimer = setTimeout(() => {
      if (!this.stopped && this.state === 'speaking') this._setState('listening')
    }, wait)
  }

  _flushPlayback() {
    clearTimeout(this.idleTimer)
    for (const s of this.sources) {
      try {
        s.onended = null
        s.stop()
      } catch {
        // already finished
      }
    }
    this.sources.clear()
    this.nextPlayTime = 0
    this.playUntilMs = 0
  }

  // ---- teardown -----------------------------------------------------------

  stop() {
    this.stopped = true
    clearTimeout(this.duckTimer)
    this.ducked = false
    clearInterval(this.pingTimer)
    clearTimeout(this.idleTimer)
    this._flushPlayback()
    try {
      this.vad?.destroy()
    } catch {
      // already torn down
    }
    this.stream?.getTracks().forEach((t) => t.stop())
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close(1000)
    this.ctx?.close().catch(() => {})
    this.vad = this.stream = this.ws = this.ctx = null
    this._setState('off')
  }
}

/** Voices the server offers, plus which is the default. */
export function fetchVoices() {
  return apiFetch('/voice/voices', { auth: true })
}

/** Plays a short server-generated sample of a voice, without starting a call. */
export async function playVoiceSample(voice) {
  const response = await fetch(`${API_BASE_URL}/voice/sample?voice=${encodeURIComponent(voice)}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail || `Preview failed (${response.status})`)
  }
  const url = URL.createObjectURL(await response.blob())
  const audio = new Audio(url)
  audio.onended = () => URL.revokeObjectURL(url)
  await audio.play()
}
