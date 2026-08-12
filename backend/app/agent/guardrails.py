"""
An output guardrail: scans tool results *before* they re-enter the
agent's context, since that's the one point where untrusted external
content -- retrieved documents, database rows -- could try to influence
the model.

This is a second, active layer of defense on top of the passive one in
prompts.py (which tells the model to treat tool output as data, never
instructions -- see the "Security -- content isolation" rules there). A
layer that only tells the model "don't listen to this" is only as good
as the model's own compliance; actually stripping suspicious content
before the model ever sees it doesn't depend on that.

Scope, deliberately: this only inspects TOOL output (the knowledge base,
order data) -- the one channel an attacker can poison indirectly (e.g. by
getting malicious text into a document that later gets retrieved). It
does not, and cannot usefully, scan the user's own chat messages the
same way -- that's the user's legitimate input channel, and filtering it
would just break normal conversation. Attempts to smuggle fake
instructions directly in a user message are the passive prompt layer's
job, not this one's -- see prompts.py rule 5-7. That split is real: the
red-team suite in security/red_team.py has scenarios for both, and only
the tool-output ones are guaranteed to be caught here.

Known tradeoff, found by that red-team suite rather than assumed away:
search_knowledge_base joins several retrieved chunks into one context
string before it reaches this filter (see graph.py), so a single
poisoned chunk causes the *entire* batch to be replaced with the safe
placeholder -- including the legitimate chunks retrieved alongside it.
That's a "fail closed" choice, not an oversight: losing a few legitimate
snippets on a flagged batch is preferable to letting an injected
instruction through. A more surgical version would filter each snippet
individually before joining, at the cost of more retrieval round trips
through this check.
"""

import re

from app.observability.logging_config import get_logger, log_event
from app.observability.metrics import guardrail_flags_total

logger = get_logger(__name__)

FILTERED_PLACEHOLDER = (
    "[Content removed by security guardrail -- flagged as potentially unsafe.]"
)

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_DIGIT_RUN_RE = re.compile(r"(?:\d[ -]?){13,19}")

# Deliberately simple substring matching, not a classifier -- this is the
# demonstrable, explainable first pass. A real production system would
# layer a trained classifier or a second LLM-as-judge call on top; the
# tradeoff is documented in the red-team findings, not hidden.
_INJECTION_PHRASES = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore the above",
    "disregard previous",
    "disregard your instructions",
    "new instructions:",
    "system override",
    "system:",
    "[system]",
    "developer mode",
    "you are now",
]


def _coerce_to_text(value) -> str:
    """Tool results are usually a plain string, but MCP tool results can
    come back as a list of content parts -- the MCP spec allows
    multi-part content, and langchain-mcp-adapters mirrors that shape
    through. Same lesson as the Gemini streaming content-block fix in
    chat_router.py's _extract_text: normalize defensively instead of
    assuming a shape, since this is the second time a "content" field
    from an LLM-adjacent library turned out not to always be a string."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", str(item)))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(value) if value is not None else ""


def check_output_safety(text) -> tuple[bool, str | None]:
    """Returns (is_safe, reason). reason is one of "pii_ssn_pattern",
    "pii_number_sequence_pattern", or "prompt_injection_phrasing" when
    unsafe, else None."""
    text = _coerce_to_text(text)
    if not text:
        return True, None

    if _SSN_RE.search(text):
        return False, "pii_ssn_pattern"

    for match in _DIGIT_RUN_RE.findall(text):
        if len(re.sub(r"[ -]", "", match)) >= 13:
            return False, "pii_number_sequence_pattern"

    lowered = text.lower()
    for phrase in _INJECTION_PHRASES:
        if phrase in lowered:
            return False, "prompt_injection_phrasing"

    return True, None


def filter_tool_output(text, *, tool_name: str) -> tuple[str, str | None]:
    """The single entry point graph.py calls for every tool result.
    Returns (safe_text, reason) -- reason is None when nothing was
    flagged, so graph.py can both use the text and record the flag (into
    AgentState.guardrail_flags) in one call."""
    text = _coerce_to_text(text)
    is_safe, reason = check_output_safety(text)

    if not is_safe:
        guardrail_flags_total.labels(reason=reason).inc()
        log_event(
            logger,
            "guardrail_flagged_tool_output",
            tool_name=tool_name,
            reason=reason,
            text_preview=text[:200],
        )
        return FILTERED_PLACEHOLDER, reason

    return f'<untrusted_tool_output source="{tool_name}">\n{text}\n</untrusted_tool_output>', None
