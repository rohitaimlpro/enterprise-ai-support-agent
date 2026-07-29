export default function ConversationSidebar({ conversations, activeId, onSelect, onNewChat }) {
  return (
    <aside className="sidebar">
      <button className="new-chat-button" onClick={onNewChat}>
        + New conversation
      </button>
      <div className="conversation-list">
        {conversations.map((c) => (
          <button
            key={c.id}
            className={`conversation-item ${c.id === activeId ? 'active' : ''}`}
            onClick={() => onSelect(c.id)}
          >
            {c.title || 'New conversation'}
          </button>
        ))}
        {conversations.length === 0 && <p className="sidebar-empty">No conversations yet</p>}
      </div>
    </aside>
  )
}
