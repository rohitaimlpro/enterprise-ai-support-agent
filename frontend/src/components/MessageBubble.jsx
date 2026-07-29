import SourceCitations from './SourceCitations'
import ToolCallBadge from './ToolCallBadge'

export default function MessageBubble({ role, content, sources, toolCalls, streaming }) {
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
        <p className="message-content">
          {content}
          {streaming && <span className="cursor-blink">|</span>}
        </p>
        <SourceCitations sources={sources} />
      </div>
    </div>
  )
}
