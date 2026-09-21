import { useCallback, useEffect, useRef, useState } from 'react'
import ChatWindow from '../components/ChatWindow'
import ConversationSidebar from '../components/ConversationSidebar'
import VoiceControls from '../components/VoiceControls'
import { apiFetch, streamChat } from '../api/client'
import { useAuth } from '../context/AuthContext'

export default function ChatPage({ onShowEvalDashboard, onShowAdminTraces }) {
  const { user, logout } = useAuth()
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [streamingMessage, setStreamingMessage] = useState(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)
  const voiceStopRef = useRef(null)
  // Mirrors the in-flight spoken reply so 'done'/'interrupted' can finalize
  // it synchronously (same reason handleSend tracks accumulatedText).
  const voiceDraftRef = useRef(null)

  const refreshConversations = useCallback(async () => {
    const data = await apiFetch('/chat/conversations', { auth: true })
    setConversations(data)
    return data
  }, [])

  useEffect(() => {
    refreshConversations().catch((err) => setError(err.message))
  }, [refreshConversations])

  async function selectConversation(id) {
    voiceStopRef.current?.() // a call is bound to one conversation
    setActiveId(id)
    setStreamingMessage(null)
    const data = await apiFetch(`/chat/conversations/${id}`, { auth: true })
    setMessages(data.messages)
  }

  function startNewChat() {
    voiceStopRef.current?.()
    setActiveId(null)
    setMessages([])
    setStreamingMessage(null)
  }

  async function handleSend(text) {
    setError(null)
    setSending(true)
    setMessages((prev) => [...prev, { id: `local-${Date.now()}`, role: 'user', content: text }])
    setStreamingMessage({ content: '', toolCalls: [] })

    // Tracked outside React state so onDone can read the final answer text
    // synchronously -- the "done" SSE event only carries metadata, not the
    // text itself (that already arrived as "token" events).
    let accumulatedText = ''

    await streamChat({
      message: text,
      conversationId: activeId,
      onToken: (chunk) => {
        accumulatedText += chunk
        setStreamingMessage((prev) => ({ ...prev, content: prev.content + chunk }))
      },
      onToolCall: (call) => {
        setStreamingMessage((prev) => ({ ...prev, toolCalls: [...prev.toolCalls, call] }))
      },
      onStatus: (text) => {
        setStreamingMessage((prev) => ({ ...prev, status: text }))
      },
      onDone: async (data) => {
        setStreamingMessage(null)
        setMessages((prev) => [
          ...prev,
          {
            id: data.message_id,
            role: 'assistant',
            content: accumulatedText,
            sources: data.sources,
            tool_calls: data.tool_calls,
          },
        ])
        const isNewConversation = !activeId
        setActiveId(data.conversation_id)
        if (isNewConversation) {
          await refreshConversations()
        }
        setSending(false)
      },
      onError: (message) => {
        setError(message)
        setStreamingMessage(null)
        setSending(false)
      },
    })
  }

  // Moves the in-flight spoken reply (if any) from "streaming" into the
  // message list, keeping exactly what was shown as captions.
  function finalizeVoiceDraft(extra = {}) {
    const draft = voiceDraftRef.current
    voiceDraftRef.current = null
    setStreamingMessage(null)
    if (draft && draft.content.trim()) {
      setMessages((prev) => [
        ...prev,
        {
          id: extra.id ?? `voice-${draft.turn}`,
          role: 'assistant',
          content: draft.content,
          sources: extra.sources ?? [],
          tool_calls: extra.tool_calls ?? draft.toolCalls,
        },
      ])
    }
  }

  async function handleVoiceEvent(evt) {
    switch (evt.type) {
      case 'ready':
        setError(null)
        if (evt.conversation_id) setActiveId(evt.conversation_id)
        break
      case 'transcript':
        setMessages((prev) => {
          // "merged": the user paused mid-sentence and the server joined both halves
          // into one request, so replace the earlier fragment instead of adding a second bubble.
          if (evt.merged) {
            const last = prev.findLastIndex((m) => m.role === 'user')
            if (last >= 0) return prev.map((m, i) => (i === last ? { ...m, content: evt.text, uncertain: evt.low_confidence } : m))
          }
          return [...prev, { id: `voice-user-${evt.turn_id}-${Date.now()}`, role: 'user', content: evt.text, uncertain: evt.low_confidence }]
        })
        break
      case 'turn_start':
        finalizeVoiceDraft() // a superseded turn never gets a 'done'
        voiceDraftRef.current = { turn: evt.turn_id, content: '', toolCalls: [] }
        setStreamingMessage({ content: '', toolCalls: [] })
        break
      case 'token':
        if (voiceDraftRef.current?.turn === evt.turn_id) {
          voiceDraftRef.current.content += evt.content
          setStreamingMessage({ ...voiceDraftRef.current })
        }
        break
      case 'tool_call':
        if (voiceDraftRef.current?.turn === evt.turn_id) {
          voiceDraftRef.current.toolCalls.push({ name: evt.name, args: evt.args })
          setStreamingMessage({ ...voiceDraftRef.current })
        }
        break
      case 'status': // "checking the knowledge base..." while a tool runs
        if (voiceDraftRef.current?.turn === evt.turn_id) {
          voiceDraftRef.current.status = evt.text
          setStreamingMessage({ ...voiceDraftRef.current })
        }
        break
      case 'notice': // canned spoken lines: reprompts, warnings, "are you still there?"
        setMessages((prev) => [...prev, { id: `notice-${Date.now()}`, role: 'assistant', content: evt.text }])
        break
      case 'interrupted':
        // cancelled=false means a refund/cancel is still finishing: keep the
        // streaming message, a 'done' will follow.
        if (evt.cancelled) finalizeVoiceDraft()
        break
      case 'done': {
        const isNew = !activeId
        finalizeVoiceDraft({ sources: evt.sources, tool_calls: evt.tool_calls })
        setActiveId(evt.conversation_id)
        if (isNew) await refreshConversations()
        break
      }
      case 'tts_unavailable':
      case 'client_error':
      case 'error':
        setError(evt.message)
        break
      default:
    }
  }

  return (
    <div className="chat-page">
      <ConversationSidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={selectConversation}
        onNewChat={startNewChat}
      />
      <div className="chat-main">
        <header className="chat-header">
          <h2>Meridian Suite Support</h2>
          <div className="chat-header-actions">
            <span className="chat-user">{user?.full_name}</span>
            {onShowAdminTraces && (
              <button className="link-button" onClick={onShowAdminTraces}>
                Admin traces
              </button>
            )}
            <button className="link-button" onClick={onShowEvalDashboard}>
              Eval dashboard
            </button>
            <button className="link-button" onClick={logout}>
              Sign out
            </button>
          </div>
        </header>
        {error && <p className="auth-error">{error}</p>}
        <VoiceControls conversationId={activeId} onEvent={handleVoiceEvent} stopRef={voiceStopRef} />
        <ChatWindow
          messages={messages}
          streamingMessage={streamingMessage}
          onSend={handleSend}
          sending={sending}
        />
      </div>
    </div>
  )
}
