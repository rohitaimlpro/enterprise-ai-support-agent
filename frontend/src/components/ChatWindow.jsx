import { useEffect, useRef, useState } from 'react'
import MessageBubble from './MessageBubble'

export default function ChatWindow({ messages, streamingMessage, onSend, sending }) {
  const [input, setInput] = useState('')
  const scrollRef = useRef(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, streamingMessage])

  function handleSubmit(e) {
    e.preventDefault()
    const trimmed = input.trim()
    if (!trimmed || sending) return
    onSend(trimmed)
    setInput('')
  }

  return (
    <div className="chat-window">
      <div className="message-list" ref={scrollRef}>
        {messages.length === 0 && !streamingMessage && (
          <div className="empty-state">
            <p>Ask about pricing, refunds, your order status, or anything else.</p>
            <p className="empty-state-examples">
              Try: "How do I upgrade my plan?" or "Where is my order?"
            </p>
          </div>
        )}

        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            role={m.role}
            content={m.content}
            sources={m.sources}
            toolCalls={m.tool_calls}
          />
        ))}

        {streamingMessage && (
          <MessageBubble
            role="assistant"
            content={streamingMessage.content}
            toolCalls={streamingMessage.toolCalls}
            sources={[]}
            streaming
          />
        )}
      </div>

      <form className="chat-input-row" onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type your message..."
          disabled={sending}
        />
        <button type="submit" disabled={sending || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
