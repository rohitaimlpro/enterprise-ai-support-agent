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

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from app.agent.llm import get_llm
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import AgentState
from app.rag.retriever import search_knowledge_base


def build_agent_graph(mcp_tools: list):
    """mcp_tools: the live, per-request MCP-backed tools (orders/tickets/
    escalation). The RAG search tool is added here directly since it
    doesn't depend on a live MCP session."""
    tools = [search_knowledge_base, *mcp_tools]
    tools_by_name = {t.name: t for t in tools}
    llm = get_llm(tools=tools)

    async def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    async def tools_node(state: AgentState) -> dict:
        last_message: AIMessage = state["messages"][-1]
        tool_messages = []
        new_sources = []

        for call in last_message.tool_calls:
            tool = tools_by_name[call["name"]]
            result = await tool.ainvoke(call["args"])

            if call["name"] == "search_knowledge_base":
                # This tool returns a JSON string of {"sources": [...],
                # "context": "..."}: pull the citations out into state for
                # the chat UI, but only forward the plain-text context to
                # the model so it isn't confused by the JSON wrapper.
                parsed = json.loads(result)
                new_sources.extend(parsed["sources"])
                result_text = parsed["context"]
            else:
                result_text = result

            tool_messages.append(
                ToolMessage(content=result_text, tool_call_id=call["id"], name=call["name"])
            )

        return {"messages": tool_messages, "sources": new_sources}

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
