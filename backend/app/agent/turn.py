"""
Runs one agent turn and yields simple typed events.

Extracted from routers/chat_router.py so the text endpoint (SSE) and the
voice endpoint (WebSocket) share exactly one implementation of "feed the
graph a message and watch what comes out" -- the two transports differ only
in how they deliver these events, not in how the agent behaves.
"""

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnEvent:
    kind: str  # "token" | "tool_start" | "tool_output"
    text: str = ""
    tool: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def extract_text(content: Any) -> str:
    """A streamed chunk's `.content` is usually a plain string, but newer
    Gemini models can return a list of content blocks instead (e.g. a
    separate "thinking" block alongside a "text" block) -- pull out just
    the user-facing text, in whichever shape it arrives."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


_WRAPPER_TAG = re.compile(r"</?untrusted_tool_output[^>]*>", re.IGNORECASE)
_WRAPPER_PREFIXES = ("<untrusted_tool_output", "</untrusted_tool_output")


class WrapperTagFilter:
    """Removes the guardrail's <untrusted_tool_output ...> delimiters if the
    model echoes them into its reply.

    Those tags exist to mark tool results as data (agent/guardrails.py); they
    are internal plumbing, never user-facing -- and on a voice call they'd be
    read aloud. Streamed text can split a tag across chunks ("</untr" +
    "usted_tool_output>"), so text that could still turn into one is held
    back until it's resolved. Any other "<" (e.g. "<3", "a < b") passes
    straight through.
    """

    def __init__(self) -> None:
        self._buf = ""

    def _could_become_tag(self, s: str) -> bool:
        low = s.lower()
        return any(p.startswith(low) or low.startswith(p) for p in _WRAPPER_PREFIXES)

    def feed(self, text: str) -> str:
        self._buf += text
        out: list[str] = []
        while self._buf:
            i = self._buf.find("<")
            if i == -1:
                out.append(self._buf)
                self._buf = ""
                break
            out.append(self._buf[:i])
            self._buf = self._buf[i:]
            j = self._buf.find(">")
            if j == -1:
                if len(self._buf) <= 64 and self._could_become_tag(self._buf):
                    break  # wait for more text
                out.append(self._buf[0])
                self._buf = self._buf[1:]
                continue
            candidate = self._buf[: j + 1]
            if not _WRAPPER_TAG.fullmatch(candidate):
                out.append(self._buf[0])  # some other "<": keep it, keep scanning
                self._buf = self._buf[1:]
                continue
            self._buf = self._buf[j + 1 :]  # a wrapper tag: drop it
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return rest


async def stream_turn(graph, initial_state: dict) -> AsyncIterator[TurnEvent]:
    tag_filter = WrapperTagFilter()
    # astream_events gives us both token-level streaming (from the chat
    # model inside the "agent" node) and tool-call lifecycle events (from
    # the "tools" node) in one unified event stream.
    async for event in graph.astream_events(initial_state, version="v2"):
        kind = event["event"]

        if kind == "on_chat_model_stream":
            text = tag_filter.feed(extract_text(event["data"]["chunk"].content))
            if text:
                yield TurnEvent("token", text=text)

        elif kind == "on_tool_start":
            yield TurnEvent(
                "tool_start",
                tool={"name": event["name"], "args": event["data"].get("input", {})},
            )

        elif kind == "on_chain_end" and event.get("name") == "tools":
            output = event["data"].get("output") or {}
            # Always emitted (even with no sources/flags) so callers can tell
            # when a tools step has finished, not just when it had results.
            if isinstance(output, dict):
                yield TurnEvent(
                    "tool_output",
                    sources=output.get("sources") or [],
                    flags=output.get("guardrail_flags") or [],
                )

    leftover = tag_filter.flush()  # text held back while it might have been a tag
    if leftover:
        yield TurnEvent("token", text=leftover)
