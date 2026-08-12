import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import ToolCallBadge from '../components/ToolCallBadge'
import SourceCitations from '../components/SourceCitations'

export default function AdminTracePage({ onBack }) {
  const [conversations, setConversations] = useState(null)
  const [error, setError] = useState(null)
  const [flaggedOnly, setFlaggedOnly] = useState(false)

  const load = useCallback((onlyFlagged) => {
    setConversations(null)
    apiFetch(`/admin/traces?flagged_only=${onlyFlagged}`, { auth: true })
      .then(setConversations)
      .catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    load(flaggedOnly)
  }, [load, flaggedOnly])

  return (
    <div className="eval-page">
      <header className="chat-header">
        <h2>Admin: Agent Trace Viewer</h2>
        <div className="chat-header-actions">
          <label className="trace-filter">
            <input
              type="checkbox"
              checked={flaggedOnly}
              onChange={(e) => setFlaggedOnly(e.target.checked)}
            />
            Flagged by guardrail only
          </label>
          <button className="link-button" onClick={onBack}>
            Back to chat
          </button>
        </div>
      </header>

      {error && <p className="auth-error">{error}. Admin access is required for this page.</p>}
      {!conversations && !error && <p className="trace-status">Loading...</p>}

      {conversations && conversations.length === 0 && (
        <p className="trace-status">
          {flaggedOnly ? 'No conversations have been flagged by the guardrail yet.' : 'No conversations yet.'}
        </p>
      )}

      <div className="trace-list">
        {conversations?.map((c) => (
          <div key={c.id} className="trace-card">
            <div className="trace-card-header">
              <span className="trace-user">{c.user_email}</span>
              <span className="trace-title">{c.title}</span>
              <span className="trace-time">{new Date(c.created_at).toLocaleString()}</span>
            </div>
            <div className="trace-messages">
              {c.messages.map((m) => {
                const flagged = m.guardrail_flags && m.guardrail_flags.length > 0
                return (
                  <div key={m.id} className={`trace-message trace-message-${m.role}`}>
                    <div className="trace-message-meta">
                      <span className="trace-role">{m.role}</span>
                      {flagged && (
                        <span className="guardrail-badge" title={m.guardrail_flags.join(', ')}>
                          ⚠ guardrail flagged: {m.guardrail_flags.join(', ')}
                        </span>
                      )}
                    </div>
                    {m.tool_calls && m.tool_calls.length > 0 && (
                      <div className="tool-badges">
                        {m.tool_calls.map((call, i) => (
                          <ToolCallBadge key={`${call.name}-${i}`} name={call.name} />
                        ))}
                      </div>
                    )}
                    <p className="trace-content">{m.content}</p>
                    <SourceCitations sources={m.sources} />
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
