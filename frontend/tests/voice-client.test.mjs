// Tests for the browser voice client's interrupt handling ("duck, then confirm").
//
// There is no browser here, so the client is driven with a fake clock, a fake
// AudioContext and a fake WebSocket. voice.js is a Vite module (it reads
// import.meta.env), so it is loaded from patched temp copies.
//
//   run:  node --test tests/

import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test, beforeEach } from 'node:test'
import { pathToFileURL } from 'node:url'

// ---- load the real source, minus the Vite-only bits ----------------------------------
const dir = mkdtempSync(join(tmpdir(), 'voice-client-'))
writeFileSync(join(dir, 'package.json'), '{"type":"module"}')
for (const file of ['client.js', 'voice.js']) {
  const source = readFileSync(new URL(`../src/api/${file}`, import.meta.url), 'utf8')
  writeFileSync(
    join(dir, file),
    source
      .replaceAll('import.meta.env.VITE_API_BASE_URL', 'undefined') // Vite-only
      .replaceAll("from './client'", "from './client.js'"), // Vite resolves extensionless imports, Node does not
  )
}

// ---- fakes ---------------------------------------------------------------------------
let now = 1000
Object.defineProperty(globalThis, 'performance', { value: { now: () => now }, configurable: true, writable: true })
globalThis.WebSocket = { OPEN: 1 }
const advance = (ms) => {
  now += ms
}

const { VoiceClient } = await import(pathToFileURL(join(dir, 'voice.js')).href)

function setup(options = {}) {
  const events = []
  const states = []
  const sent = []
  const client = new VoiceClient({
    onEvent: (e) => events.push(e),
    onState: (s) => states.push(s),
    duckSafetyMs: 40,
    ...options,
  })
  const ctx = {
    currentTime: 0,
    suspendCalls: 0,
    resumeCalls: 0,
    suspend() {
      this.suspendCalls += 1
      return Promise.resolve()
    },
    resume() {
      this.resumeCalls += 1
      return Promise.resolve()
    },
    close: () => Promise.resolve(),
  }
  client.ctx = ctx
  client.ws = { readyState: 1, send: (data) => sent.push(data), close() {} }
  client.stopped = false
  return { client, ctx, sent, events, states }
}

/** Pretend the agent is mid-answer: 8 more seconds of audio queued. */
function agentIsSpeaking({ client, ctx }) {
  ctx.currentTime = 2
  client.nextPlayTime = 10
  client.playUntilMs = now + 8000
  client.currentTurn = 7
  client._setState('speaking')
}

/** Feed clear-speech frames (~100 ms apart) for `ms` milliseconds. */
function speakFor(client, ms, probability = 0.95) {
  for (let elapsed = 0; elapsed <= ms; elapsed += 100) {
    client._onFrame(probability)
    if (elapsed < ms) advance(100)
  }
}

beforeEach(() => {
  now = 1000
})

// ---- when the agent is paused ----------------------------------------------------------

test('does not pause anything when the agent is silent', () => {
  const t = setup()
  speakFor(t.client, 1000)
  assert.equal(t.ctx.suspendCalls, 0)
  assert.equal(t.client.ducked, false)
})

test('pauses the agent after 300 ms of clear speech over it, and not before', () => {
  const t = setup()
  agentIsSpeaking(t)
  speakFor(t.client, 200)
  assert.equal(t.client.ducked, false, 'too early: could be a cough')
  speakFor(t.client, 200)
  assert.equal(t.client.ducked, true)
  assert.equal(t.ctx.suspendCalls, 1, 'audio paused exactly once')
  assert.equal(t.client.state, 'listening', 'the UI says it is listening')
})

test('a brief blip that stops resets the timer', () => {
  const t = setup()
  agentIsSpeaking(t)
  t.client._onFrame(0.95)
  advance(200)
  t.client._onFrame(0.1) // the sound stopped
  advance(100)
  t.client._onFrame(0.95) // a new burst starts here
  advance(200)
  t.client._onFrame(0.95)
  assert.equal(t.client.ducked, false, 'only 200 ms of the second burst')
})

test('muted microphone never pauses the agent', () => {
  const t = setup()
  agentIsSpeaking(t)
  t.client.setMuted(true)
  speakFor(t.client, 1000)
  assert.equal(t.ctx.suspendCalls, 0)
})

test('with interrupting switched off nothing is ever paused', () => {
  const t = setup({ allowInterrupt: false })
  agentIsSpeaking(t)
  speakFor(t.client, 1000)
  assert.equal(t.ctx.suspendCalls, 0)
})

// ---- the server's verdict --------------------------------------------------------------

function pausedAgent() {
  const t = setup()
  agentIsSpeaking(t)
  const stopped = []
  t.client.sources = new Set([{ stop: () => stopped.push(1), onended: () => {} }])
  speakFor(t.client, 400)
  assert.equal(t.client.ducked, true)
  t.stopped = stopped
  return t
}

test('server says "ignored" (echo / hmm / noise): the agent resumes where it left off', () => {
  const t = pausedAgent()
  const finishTimeBefore = t.client.playUntilMs
  advance(1200)
  const pausedFor = now - t.client.duckStartedMs // however long the agent was actually paused
  t.client._onJson({ type: 'ignored', reason: 'echo' })
  assert.equal(t.client.ducked, false)
  assert.equal(t.ctx.resumeCalls, 1)
  assert.equal(t.client.nextPlayTime, 10, 'queued audio is kept')
  assert.equal(t.stopped.length, 0, 'nothing was thrown away')
  assert.ok(pausedFor >= 1200)
  assert.equal(t.client.playUntilMs, finishTimeBefore + pausedFor, 'the finish time slid by the paused duration')
  assert.equal(t.client.state, 'speaking')
})

test('server accepts it as a real request ("transcript"): the old answer is discarded', () => {
  const t = pausedAgent()
  t.client._onJson({ type: 'transcript', text: 'mujhe refund chahiye', turn_id: 8 })
  assert.equal(t.client.ducked, false)
  assert.equal(t.stopped.length, 1, 'queued audio was stopped')
  assert.equal(t.client.nextPlayTime, 0)
  assert.equal(t.client.currentTurn, null)
  assert.equal(t.ctx.resumeCalls, 1, 'the audio clock is running again for the next answer')
})

test('server reports the turn was cancelled ("interrupted"): the audio is discarded', () => {
  const t = pausedAgent()
  t.client._onJson({ type: 'interrupted', turn_id: 7, cancelled: true })
  assert.equal(t.stopped.length, 1)
  assert.equal(t.client.nextPlayTime, 0)
  assert.equal(t.client.state, 'listening')
})

test('a VAD misfire (too short to be speech) resumes the agent', () => {
  const t = pausedAgent()
  t.client._resolveDuck(true) // what the onVADMisfire callback calls
  assert.equal(t.client.ducked, false)
  assert.equal(t.stopped.length, 0)
  assert.equal(t.ctx.resumeCalls, 1)
})

test('if the server never answers, the agent carries on by itself', async () => {
  const t = pausedAgent()
  await new Promise((resolve) => setTimeout(resolve, 90)) // safety timer is 40 ms in tests
  assert.equal(t.client.ducked, false)
  assert.equal(t.ctx.resumeCalls, 1)
  assert.equal(t.stopped.length, 0)
})

test('turning interrupting off while paused resumes the agent', () => {
  const t = pausedAgent()
  t.client.setAllowInterrupt(false)
  assert.equal(t.client.ducked, false)
  assert.equal(t.ctx.resumeCalls, 1)
})

// ---- sending what was said -------------------------------------------------------------

test('speech over the agent is sent to the server while the agent stays paused', () => {
  const t = pausedAgent()
  t.client._onSpeechStart()
  t.client._onSpeechEnd(new Float32Array(1600))
  assert.equal(t.sent.length, 1, 'the server needs it to decide')
  assert.equal(t.client.ducked, true, 'still paused until the verdict arrives')
})

test('with interrupting off, speech that began under the agent is dropped but normal speech is sent', () => {
  const t = setup({ allowInterrupt: false })
  agentIsSpeaking(t)
  t.client._onSpeechStart()
  t.client._onSpeechEnd(new Float32Array(1600))
  assert.equal(t.sent.length, 0)

  t.client.nextPlayTime = 0 // the agent finished...
  t.client.playUntilMs = 0
  advance(2000) // ...and the echo tail passed
  t.client._onSpeechStart()
  t.client._onSpeechEnd(new Float32Array(1600))
  assert.equal(t.sent.length, 1)
})

test('speech right after the agent stops is treated as its echo tail', () => {
  const t = setup({ allowInterrupt: false })
  t.ctx.currentTime = 10
  t.client.nextPlayTime = 10 // audio just ended
  t.client.playUntilMs = now - 100
  assert.equal(t.client._agentAudible(), true)
  advance(600)
  assert.equal(t.client._agentAudible(), false)
})

test('speech while the connection is down resumes the agent and reports it', () => {
  const t = pausedAgent()
  t.client.ws.readyState = 3
  t.client._onSpeechStart()
  t.client._onSpeechEnd(new Float32Array(1600))
  assert.equal(t.client.ducked, false)
  assert.ok(t.events.some((e) => e.type === 'client_error' && e.code === 'offline'))
})

// ---- manual stop, goodbye --------------------------------------------------------------

test('the Stop button silences the agent immediately and tells the server', () => {
  const t = setup()
  agentIsSpeaking(t)
  const stopped = []
  t.client.sources = new Set([{ stop: () => stopped.push(1), onended: () => {} }])
  t.client.interruptNow()
  assert.equal(stopped.length, 1)
  assert.equal(t.client.nextPlayTime, 0)
  assert.equal(t.client.currentTurn, null)
  assert.deepEqual(JSON.parse(t.sent[0]), { type: 'interrupt' })
  assert.equal(t.client.state, 'listening')
})

test('Stop also works while the agent is already paused', () => {
  const t = pausedAgent()
  t.client.interruptNow()
  assert.equal(t.client.ducked, false)
  assert.equal(t.stopped.length, 1)
})

test('hangup ends the call after the farewell has finished playing', async () => {
  const t = setup()
  t.client.playUntilMs = now // nothing left to play
  t.client._onJson({ type: 'hangup' })
  assert.equal(t.client.stopped, false, 'not immediately')
  await new Promise((resolve) => setTimeout(resolve, 500))
  assert.equal(t.client.stopped, true)
  assert.equal(t.client.state, 'off')
})

test('stop() clears a pending pause so nothing fires after the call ends', async () => {
  const t = pausedAgent()
  t.client.stop()
  const resumesAtStop = t.ctx.resumeCalls
  await new Promise((resolve) => setTimeout(resolve, 90))
  assert.equal(t.ctx.resumeCalls, resumesAtStop)
})
