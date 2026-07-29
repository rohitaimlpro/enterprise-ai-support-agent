import { useCallback, useEffect, useState } from 'react'
import ChatWindow from '../components/ChatWindow'
import ConversationSidebar from '../components/ConversationSidebar'
import { apiFetch, streamChat } from '../api/client'
import { useAuth } from '../context/AuthContext'

export default function ChatPage({ onShowEvalDashboard }) {
  const { user, logout } = useAuth()
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [streamingMessage, setStreamingMessage] = useState(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)

  const refreshConversations = useCallback(async () => {
    const data = await apiFetch('/chat/conversations', { auth: true })
    setConversations(data)
    return data
  }, [])

  useEffect(() => {
    refreshConversations().catch((err) => setError(err.message))
  }, [refreshConversations])

  async function selectConversation(id) {
    setActiveId(id)
    setStreamingMessage(null)
    const data = await apiFetch(`/chat/conversations/${id}`, { auth: true })
    setMessages(data.messages)
  }

  function startNewChat() {
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
            <button className="link-button" onClick={onShowEvalDashboard}>
              Eval dashboard
            </button>
            <button className="link-button" onClick={logout}>
              Sign out
            </button>
          </div>
        </header>
        {error && <p className="auth-error">{error}</p>}
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
