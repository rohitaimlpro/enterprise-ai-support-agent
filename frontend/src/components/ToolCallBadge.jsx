const TOOL_LABELS = {
  search_knowledge_base: 'Searched knowledge base',
  lookup_order: 'Looked up order',
  cancel_order: 'Cancelled order',
  refund_request: 'Filed refund request',
  get_invoice: 'Fetched invoice',
  create_ticket: 'Created support ticket',
  escalate_issue: 'Escalated to a human',
  schedule_callback: 'Scheduled callback',
}

export default function ToolCallBadge({ name }) {
  return <span className="tool-badge">{TOOL_LABELS[name] || `Called ${name}`}</span>
}
