"""
The agent's reasoning loop -- the classic LangGraph "agent <-> tools" shape:

    START -> agent -> (requested a tool call?) -> tools -> agent -> ... -> END

`agent` asks Gemini (with tools bound) what to do next. If it requests tool
calls, `tools` executes them and loops back to `agent`; once the model
replies with plain text (no tool calls), the graph ends.

The graph is built fresh per chat request because the MCP-backed tools
(orders/tickets) come from a short-lived, per-request MCP session -- see
app/tools/mcp_client.py and app/routers/chat_router.py.
"""

import json
from collections.abc import Awaitable, Callable

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import ToolException
from langgraph.graph import END, StateGraph

from app.agent.guardrails import filter_tool_output
from app.agent.llm import get_llm
from app.agent.prompts import get_system_prompt
from app.agent.state import AgentState
from app.observability.logging_config import get_logger
from app.rag.retriever import search_knowledge_base

logger = get_logger(__name__)


# (tool_name, args) -> None to let the call run, or a string to return to
# the model *instead of* running the tool. Voice sessions use this to make
# refund/cancel wait for an explicit spoken "yes" -- see voice/confirm.py.
ToolGate = Callable[[str, dict], Awaitable[str | None]]


def build_agent_graph(
    mcp_tools: list,
    system_prompt: str | None = None,
    tool_gate: ToolGate | None = None,
):
    """mcp_tools: the live, per-request MCP-backed tools (orders/tickets/
    escalation). The RAG search tool is added here directly since it
    doesn't depend on a live MCP session."""
    tools = [search_knowledge_base, *mcp_tools]
    tools_by_name = {t.name: t for t in tools}
    llm = get_llm(tools=tools)

    async def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt or get_system_prompt()), *messages]
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    async def tools_node(state: AgentState) -> dict:
        last_message: AIMessage = state["messages"][-1]
        tool_messages = []
        new_sources = []
        new_flags = []

        for call in last_message.tool_calls:
            tool = tools_by_name[call["name"]]
            blocked = await tool_gate(call["name"], call["args"]) if tool_gate else None
            failed = False
            try:
                result = blocked if blocked is not None else await tool.ainvoke(call["args"])
            except ToolException as exc:
                failed = True
                # The tool ran and reported an error (bad input, a rejected request).
                # That must not kill the whole conversation turn, so the model gets a
                # short, safe message it can recover from. The raw error stays in the
                # log: it can contain SQL and user ids we don't want in model context.
                # Infrastructure failures (a dead tools process) are NOT ToolExceptions
                # and still propagate, so the session gets reset.
                logger.warning("tool_failed name=%s error=%s", call["name"], exc)
                result = (
                    "The tool could not complete that request. Tell the user briefly that it "
                    "didn't work and offer to list their orders or open a ticket. Do not guess "
                    "or invent details."
                )

            if call["name"] == "search_knowledge_base" and blocked is None and not failed:
                # This tool returns a JSON string of {"sources": [...],
                # "context": "..."}: pull the citations out into state for
                # the chat UI, but only forward the plain-text context to
                # the model so it isn't confused by the JSON wrapper.
                parsed = json.loads(result)
                new_sources.extend(parsed["sources"])
                result_text = parsed["context"]
            else:
                result_text = result

            # Every tool result is untrusted content from a document store
            # or a database, not from the user -- run it through the
            # guardrail (PII / injection-phrase check) and wrap it so the
            # model can't mistake it for an instruction. See guardrails.py.
            safe_text, flag_reason = filter_tool_output(result_text, tool_name=call["name"])
            if flag_reason:
                new_flags.append(flag_reason)

            tool_messages.append(
                ToolMessage(content=safe_text, tool_call_id=call["id"], name=call["name"])
            )

        return {"messages": tool_messages, "sources": new_sources, "guardrail_flags": new_flags}

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()
