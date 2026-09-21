import SourceCitations from './SourceCitations'
import ToolCallBadge from './ToolCallBadge'

export default function MessageBubble({ role, content, sources, toolCalls, streaming, status, uncertain }) {
  const isUser = role === 'user'

  return (
    <div className={`message-row ${isUser ? 'from-user' : 'from-assistant'}`}>
      <div className="message-bubble">
        {toolCalls && toolCalls.length > 0 && (
          <div className="tool-badges">
            {toolCalls.map((call, i) => (
              <ToolCallBadge key={`${call.name}-${i}`} name={call.name} />
            ))}
          </div>
        )}
        {streaming && !content && status && <p className="message-status">{status}</p>}
        <p className="message-content">
          {content}
          {streaming && <span className="cursor-blink">|</span>}
        </p>
        {uncertain && <p className="message-uncertain">The microphone may have misheard this. Say it again if it looks wrong.</p>}
        <SourceCitations sources={sources} />
      </div>
    </div>
  )
}
