import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'

const METRIC_LABELS = {
  faithfulness: 'Faithfulness',
  answer_relevancy: 'Answer Relevancy',
  context_precision: 'Context Precision',
  context_recall: 'Context Recall',
}

export default function EvalDashboardPage({ onBack }) {
  const [results, setResults] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    apiFetch('/admin/eval', { auth: true })
      .then(setResults)
      .catch((err) => setError(err.message))
  }, [])

  return (
    <div className="eval-page">
      <header className="chat-header">
        <h2>RAG Evaluation Dashboard</h2>
        <button className="link-button" onClick={onBack}>
          Back to chat
        </button>
      </header>

      {error && (
        <p className="auth-error">
          {error}. Run <code>python eval/run_eval.py</code> in the backend container first.
        </p>
      )}

      {!results && !error && <p>Loading...</p>}

      {results && (
        <>
          <p className="eval-meta">
            Last run: {new Date(results.generated_at).toLocaleString()} -- {results.num_examples} examples
          </p>
          <div className="eval-grid">
            {Object.entries(METRIC_LABELS).map(([key, label]) => (
              <div key={key} className="eval-tile">
                <span className="eval-tile-label">{label}</span>
                <span className="eval-tile-value">
                  {results.metrics[key] != null ? results.metrics[key].toFixed(2) : '--'}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
