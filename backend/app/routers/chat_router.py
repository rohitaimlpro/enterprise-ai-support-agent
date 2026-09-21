"""
Chat endpoint: runs a user message through the LangGraph agent and streams
the response back over Server-Sent Events (SSE).

One request = one full turn:
    1. load/create the conversation + recent history (Redis, falling back to Postgres)
    2. open a per-request MCP session (scoped to the current user) for the
       order/ticket tools, and build the agent graph with those tools + RAG
    3. stream tokens/tool-call events to the client via SSE as the graph runs
    4. persist the user + assistant messages to Postgres and refresh the Redis cache
"""

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.agent.graph import build_agent_graph
from app.agent.turn import extract_text as _extract_text
from app.agent.turn import stream_turn
from app.cache.redis_client import append_message, get_cached_messages, set_cached_messages
from app.database import get_db
from app.deps import get_current_user
from app.models import Conversation, Message, MessageRole, User
from app.observability.logging_config import get_logger, log_event
from app.observability.metrics import (
    chat_turn_latency_seconds,
    rag_retrievals_total,
    tool_calls_total,
)
from app.schemas import ChatRequest, ConversationResponse
# Borrowed from a per-user pool of warm subprocesses instead of spawning one
# per turn (~1 s saved). Kept under the old local name on purpose: it is the
# seam the chat tests patch.
from app.tools.mcp_client import shared_mcp_tools_session as mcp_tools_session
from app.voice.phrases import tool_status

router = APIRouter(prefix="/chat", tags=["chat"])
logger = get_logger(__name__)


def _get_or_create_conversation(
    db: Session, user: User, conversation_id: str | None, first_message: str
) -> Conversation:
    if conversation_id:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
            .first()
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation

    conversation = Conversation(user_id=user.id, title=first_message[:60])
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def _load_history_as_messages(db: Session, conversation: Conversation) -> list:
    """Returns prior turns as LangChain messages for the model's context.
    Tool-call plumbing is intentionally not replayed across turns -- each
    turn's own agent loop handles its tool calls in-memory; history only
    needs the final user/assistant text."""
    cached = get_cached_messages(conversation.id)
    if cached is None:
        rows = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
            .all()
        )
        cached = [{"role": r.role.value, "content": r.content} for r in rows if r.role != MessageRole.tool]
        set_cached_messages(conversation.id, cached)

    history = []
    for item in cached:
        if item["role"] == "user":
            history.append(HumanMessage(content=item["content"]))
        elif item["role"] == "assistant":
            history.append(AIMessage(content=item["content"]))
    return history


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == current_user.id)
        .order_by(Conversation.created_at.desc())
        .all()
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.user_id == current_user.id)
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.post("")
async def chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_or_create_conversation(
        db, current_user, payload.conversation_id, payload.message
    )
    history_messages = _load_history_as_messages(db, conversation)

    user_message = Message(
        conversation_id=conversation.id, role=MessageRole.user, content=payload.message
    )
    db.add(user_message)
    db.commit()
    append_message(conversation.id, "user", payload.message)

    async def event_generator():
        start = time.monotonic()
        full_answer = ""
        collected_sources: list[dict] = []
        collected_tool_calls: list[dict] = []
        collected_guardrail_flags: list[str] = []

        async with mcp_tools_session(current_user.id) as mcp_tools:
            graph = build_agent_graph(mcp_tools)
            initial_state = {
                "messages": [*history_messages, HumanMessage(content=payload.message)],
                "sources": [],
                "guardrail_flags": [],
            }

            async for event in stream_turn(graph, initial_state):
                if event.kind == "token":
                    full_answer += event.text
                    yield {"event": "token", "data": json.dumps({"content": event.text})}

                elif event.kind == "tool_start":
                    collected_tool_calls.append(event.tool)
                    tool_calls_total.labels(tool_name=event.tool["name"]).inc()
                    if event.tool["name"] == "search_knowledge_base":
                        rag_retrievals_total.inc()
                    yield {"event": "tool_call", "data": json.dumps(event.tool)}
                    # A human-readable "working on it" line, so the user isn't
                    # staring at a blank bubble while the tool runs.
                    yield {"event": "status", "data": json.dumps({"text": tool_status(event.tool["name"])})}

                elif event.kind == "tool_output":
                    collected_sources.extend(event.sources)
                    collected_guardrail_flags.extend(event.flags)

        latency_seconds = time.monotonic() - start
        chat_turn_latency_seconds.observe(latency_seconds)
        latency_ms = int(latency_seconds * 1000)

        assistant_message = Message(
            conversation_id=conversation.id,
            role=MessageRole.assistant,
            content=full_answer,
            sources=collected_sources,
            tool_calls=collected_tool_calls,
            guardrail_flags=collected_guardrail_flags,
        )
        db.add(assistant_message)
        db.commit()
        db.refresh(assistant_message)
        append_message(conversation.id, "assistant", full_answer)

        log_event(
            logger,
            "chat_turn",
            conversation_id=conversation.id,
            latency_ms=latency_ms,
            tool_calls=[t["name"] for t in collected_tool_calls],
            sources_used=len(collected_sources),
            answer_length=len(full_answer),
            guardrail_flags=collected_guardrail_flags,
        )

        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "conversation_id": conversation.id,
                    "message_id": assistant_message.id,
                    "sources": collected_sources,
                    "tool_calls": collected_tool_calls,
                    "guardrail_flags": collected_guardrail_flags,
                }
            ),
        }

    return EventSourceResponse(event_generator())
