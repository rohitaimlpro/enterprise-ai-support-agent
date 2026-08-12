"""
Prometheus metrics.

HTTP-level metrics (request count/latency by route) come for free from
prometheus-fastapi-instrumentator. The counters/histogram below cover
what a generic HTTP instrumentator can't see: which tools the agent
actually calls, how often it hits the knowledge base, and end-to-end
turn latency.
"""

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator


def instrument_app(app: FastAPI) -> None:
    """Adds default HTTP request metrics and exposes them at /metrics."""
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")


tool_calls_total = Counter(
    "support_agent_tool_calls_total",
    "Number of times the agent called a tool",
    ["tool_name"],
)

rag_retrievals_total = Counter(
    "support_agent_rag_retrievals_total",
    "Number of knowledge-base searches performed",
)

chat_turn_latency_seconds = Histogram(
    "support_agent_chat_turn_latency_seconds",
    "End-to-end latency of one chat turn, including all tool calls and model round trips",
)

guardrail_flags_total = Counter(
    "support_agent_guardrail_flags_total",
    "Number of times a tool result was flagged and filtered by the output guardrail",
    ["reason"],
)
