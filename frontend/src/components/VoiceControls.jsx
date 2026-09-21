import { useEffect, useRef, useState } from 'react'
import { VoiceClient, fetchVoices, playVoiceSample } from '../api/voice'

const STATE_LABEL = {
  off: '',
  connecting: 'Connecting...',
  reconnecting: 'Reconnecting...',
  listening: 'Listening',
  thinking: 'Thinking...',
  speaking: 'Speaking',
}

const VOICE_KEY = 'support_agent_voice'
// v2: interrupting used to default to OFF; a saved 'false' from then shouldn't carry over.
const INTERRUPT_KEY = 'support_agent_allow_interrupt_v2'

// localStorage can throw (private windows, blocked storage): preferences are a
// convenience, never a requirement.
function readPref(key, fallback) {
  try {
    const value = localStorage.getItem(key)
    return value === null ? fallback : value
  } catch {
    return fallback
  }
}

function writePref(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // ignore
  }
}

/**
 * Start/end-call button, voice picker and status. All audio work lives in
 * VoiceClient; this only wires its events to the chat page.
 *
 * `stopRef` lets the parent end the call (e.g. when the user switches
 * conversation) without remounting this component mid-call.
 */
export default function VoiceControls({ conversationId, onEvent, stopRef }) {
  const [state, setState] = useState('off')
  const [muted, setMuted] = useState(false)
  const [voices, setVoices] = useState([])
  const [voice, setVoice] = useState(() => readPref(VOICE_KEY, ''))
  const [allowInterrupt, setAllowInterrupt] = useState(() => readPref(INTERRUPT_KEY, 'true') === 'true')
  const [previewing, setPreviewing] = useState(false)
  const [notice, setNotice] = useState(null)
  const clientRef = useRef(null)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  const active = state !== 'off'

  useEffect(() => {
    let cancelled = false
    fetchVoices()
      .then((data) => {
        if (cancelled) return
        setVoices(data.voices)
        // Keep the saved choice only if the server still offers it.
        setVoice((current) => (data.voices.some((v) => v.id === current) ? current : data.default))
      })
      .catch(() => {}) // the picker is optional; a call still works with the server default
    return () => {
      cancelled = true
    }
  }, [])

  function stop() {
    clientRef.current?.stop()
    clientRef.current = null
    setMuted(false)
  }

  async function start() {
    setNotice(null)
    const client = new VoiceClient({
      voice: voice || null,
      allowInterrupt,
      onEvent: (evt) => onEventRef.current?.(evt),
      onState: (s) => {
        setState(s)
        if (s === 'off') clientRef.current = null
      },
    })
    clientRef.current = client
    await client.start(conversationId)
  }

  function toggleMute() {
    const next = !muted
    setMuted(next)
    clientRef.current?.setMuted(next)
  }

  function changeVoice(id) {
    setVoice(id)
    writePref(VOICE_KEY, id)
  }

  function changeInterrupt(allow) {
    setAllowInterrupt(allow)
    writePref(INTERRUPT_KEY, String(allow))
    clientRef.current?.setAllowInterrupt(allow)
  }

  async function preview() {
    if (!voice || previewing) return
    setNotice(null)
    setPreviewing(true)
    try {
      await playVoiceSample(voice)
    } catch (err) {
      setNotice(err.message)
    } finally {
      setPreviewing(false)
    }
  }

  // Keep the parent's handle pointing at the latest stop(); no cleanup here,
  // or every re-render would end the call.
  useEffect(() => {
    if (stopRef) stopRef.current = stop
  })

  // End the call only when this component actually unmounts.
  useEffect(
    () => () => {
      clientRef.current?.stop()
    },
    [],
  )

  return (
    <div className="voice-controls">
      {!active ? (
        <button type="button" onClick={start} title="Talk to the assistant">
          Start voice
        </button>
      ) : (
        <>
          <button type="button" className="voice-end" onClick={stop}>
            End call
          </button>
          <button type="button" onClick={toggleMute}>
            {muted ? 'Unmute mic' : 'Mute mic'}
          </button>
          {(state === 'speaking' || state === 'thinking') && (
            <button type="button" onClick={() => clientRef.current?.interruptNow()} title="Make the agent stop talking right now">
              Stop
            </button>
          )}
          <span className={`voice-status voice-status-${muted ? 'muted' : state}`}>
            {muted ? 'Mic muted' : STATE_LABEL[state]}
          </span>
        </>
      )}

      {voices.length > 0 && (
        <label className="voice-picker">
          Voice
          <select value={voice} onChange={(e) => changeVoice(e.target.value)} disabled={active}>
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
          <button type="button" onClick={preview} disabled={previewing || !voice}>
            {previewing ? 'Playing...' : 'Preview'}
          </button>
        </label>
      )}

      <label
        className="voice-interrupt"
        title="On: talk over the agent and it pauses at once, then stops if it really heard you. Off: the microphone is ignored while it talks (use this if your speakers make it interrupt itself)."
      >
        <input type="checkbox" checked={allowInterrupt} onChange={(e) => changeInterrupt(e.target.checked)} />
        Let me interrupt
      </label>

      {notice && <span className="voice-notice">{notice}</span>}
    </div>
  )
}
