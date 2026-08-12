"""
Exercises the /chat SSE endpoint's plumbing (auth, conversation creation,
event formatting, Postgres persistence, Redis cache writes) using a fake
agent graph -- no real Gemini call or MCP subprocess involved. The graph
and MCP session's own behavior are covered separately (mcp_server tools
directly in test_mcp_tools.py; the graph's node wiring is exercised by
`python -m app.rag.ingest`/manual smoke tests since it needs a live model).
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import sse_starlette.sse

import app.routers.chat_router as chat_router_module
from app.models import Conversation, Message


@pytest.fixture(autouse=True)
def _reset_sse_starlette_exit_event():
    """sse-starlette caches a single anyio.Event bound to whichever event
    loop first created it. TestClient spins up a fresh event loop per
    test, so without this reset the second SSE test fails with
    'Event object is bound to a different event loop'."""
    sse_starlette.sse.AppStatus.should_exit_event = None


class FakeGraph:
    """Mimics langgraph's CompiledStateGraph.astream_events just enough
    for chat_router's event_generator to exercise every branch it has."""

    async def astream_events(self, initial_state, version="v2"):
        events = [
            {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="Hello ")}},
            {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="world!")}},
            {
                "event": "on_tool_start",
                "name": "search_knowledge_base",
                "data": {"input": {"query": "refund policy"}},
            },
            {
                "event": "on_chain_end",
                "name": "tools",
                "data": {
                    "output": {
                        "sources": [
                            {
                                "title": "Refund Policy",
                                "source_file": "refund_policy.md",
                                "snippet": "Full refund within 14 days.",
                            }
                        ],
                        "guardrail_flags": ["prompt_injection_phrasing"],
                    }
                },
            },
        ]
        for event in events:
            yield event


def _stub_redis_cache(monkeypatch):
    """No real Redis server in tests -- back the cache with a plain dict so
    chat_router's get/set/append calls succeed without network I/O."""
    store: dict[str, list[dict]] = {}

    def fake_get(conversation_id):
        return store.get(conversation_id)

    def fake_set(conversation_id, messages):
        store[conversation_id] = list(messages)

    def fake_append(conversation_id, role, content):
        store.setdefault(conversation_id, []).append({"role": role, "content": content})

    monkeypatch.setattr(chat_router_module, "get_cached_messages", fake_get)
    monkeypatch.setattr(chat_router_module, "set_cached_messages", fake_set)
    monkeypatch.setattr(chat_router_module, "append_message", fake_append)


def _register(client, email="chatuser@example.com"):
    resp = client.post(
        "/auth/register", json={"email": email, "password": "pw123456", "full_name": "Chat User"}
    )
    return resp.json()["access_token"]


def test_chat_streams_tokens_tool_call_and_done(client, db_session, monkeypatch):
    monkeypatch.setattr(chat_router_module, "build_agent_graph", lambda tools: FakeGraph())
    _stub_redis_cache(monkeypatch)

    @asynccontextmanager
    async def fake_mcp_tools_session(user_id):
        yield []

    monkeypatch.setattr(chat_router_module, "mcp_tools_session", fake_mcp_tools_session)

    token = _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    with client.stream(
        "POST", "/chat", json={"message": "Can I get a refund?"}, headers=headers
    ) as response:
        assert response.status_code == 200
        raw_events = list(response.iter_lines())

    body = "\n".join(raw_events)
    assert "Hello " in body
    assert "world!" in body
    assert "search_knowledge_base" in body
    assert "Refund Policy" in body
    assert "prompt_injection_phrasing" in body
    assert "event: done" in body


def test_chat_persists_user_and_assistant_messages(client, db_session, monkeypatch):
    monkeypatch.setattr(chat_router_module, "build_agent_graph", lambda tools: FakeGraph())
    _stub_redis_cache(monkeypatch)

    @asynccontextmanager
    async def fake_mcp_tools_session(user_id):
        yield []

    monkeypatch.setattr(chat_router_module, "mcp_tools_session", fake_mcp_tools_session)

    token = _register(client, email="persist@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    with client.stream(
        "POST", "/chat", json={"message": "Can I get a refund?"}, headers=headers
    ) as response:
        list(response.iter_lines())  # drain the stream so the generator runs to completion

    conversations = db_session.query(Conversation).all()
    assert len(conversations) == 1
    assert conversations[0].title.startswith("Can I get a refund?")

    messages = (
        db_session.query(Message)
        .filter(Message.conversation_id == conversations[0].id)
        .order_by(Message.created_at)
        .all()
    )
    assert [m.role.value for m in messages] == ["user", "assistant"]
    assert messages[1].content == "Hello world!"
    assert messages[1].sources[0]["title"] == "Refund Policy"
    assert messages[1].guardrail_flags == ["prompt_injection_phrasing"]


def test_chat_requires_auth(client):
    resp = client.post("/chat", json={"message": "hi"})
    assert resp.status_code in (401, 403)


class TestExtractText:
    """Newer Gemini models can stream `.content` as a list of content
    blocks (e.g. a 'thinking' block alongside a 'text' block) instead of
    a plain string -- caught live when gemini-2.5-flash returned a list
    and broke `full_answer += chunk.content` with a TypeError."""

    def test_plain_string(self):
        assert chat_router_module._extract_text("hello") == "hello"

    def test_list_of_text_blocks(self):
        content = [{"type": "text", "text": "hel"}, {"type": "text", "text": "lo"}]
        assert chat_router_module._extract_text(content) == "hello"

    def test_list_skips_non_text_blocks(self):
        content = [{"type": "thinking", "thinking": "reasoning..."}, {"type": "text", "text": "answer"}]
        assert chat_router_module._extract_text(content) == "answer"

    def test_list_of_plain_strings(self):
        assert chat_router_module._extract_text(["a", "b"]) == "ab"

    def test_empty_or_none(self):
        assert chat_router_module._extract_text(None) == ""
        assert chat_router_module._extract_text([]) == ""
        assert chat_router_module._extract_text("") == ""
