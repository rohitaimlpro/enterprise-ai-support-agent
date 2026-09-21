"""A tool that reports an error must not kill the conversation turn, and its raw
error (which can contain SQL and user ids) must not reach the model. Found in
live testing: the model invented order id "MS001", Postgres raised on it, and the
user heard "something went wrong" instead of a helpful reply."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool, ToolException

import app.agent.graph as graph_module


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    async def ainvoke(self, messages):
        self.seen.append(messages)
        return self.replies.pop(0)


def _call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "c1"}])


def _build(monkeypatch, tool_fn, replies, name="get_invoice"):
    tool = StructuredTool.from_function(tool_fn, name=name, description="x")
    llm = ScriptedLLM(replies)
    monkeypatch.setattr(graph_module, "get_llm", lambda tools=None: llm)
    return graph_module.build_agent_graph([tool]), llm


def _state(text="invoice chahiye"):
    return {"messages": [HumanMessage(content=text)], "sources": [], "guardrail_flags": []}


SECRET = 'invalid input syntax for type uuid: "MS001" [SQL: SELECT ... user_id=82d35a9d-6482-4202]'


def test_a_tool_error_does_not_crash_the_turn(monkeypatch):
    def get_invoice(order_id: str) -> str:
        raise ToolException(SECRET)

    graph, llm = _build(
        monkeypatch, get_invoice, [_call("get_invoice", {"order_id": "MS001"}), AIMessage(content="Order nahi mila, main list karta hoon.")]
    )
    out = asyncio.run(graph.ainvoke(_state()))
    assert out["messages"][-1].content.startswith("Order nahi mila")  # the turn completed with a real answer


def test_the_model_gets_a_safe_message_not_the_raw_database_error(monkeypatch):
    def get_invoice(order_id: str) -> str:
        raise ToolException(SECRET)

    graph, llm = _build(monkeypatch, get_invoice, [_call("get_invoice", {"order_id": "MS001"}), AIMessage(content="ok")])
    asyncio.run(graph.ainvoke(_state()))
    tool_message = llm.seen[1][-1]  # what the model saw on its second call
    assert "could not complete" in tool_message.content
    for leaked in ("SQL", "uuid", "MS001", "82d35a9d", "user_id"):
        assert leaked not in tool_message.content


def test_a_failing_search_tool_does_not_crash_json_parsing(monkeypatch):
    """search_knowledge_base results are parsed as JSON; the failure message isn't."""

    def search_knowledge_base(query: str) -> str:
        raise ToolException("vector store unavailable")

    tool = StructuredTool.from_function(search_knowledge_base, name="search_knowledge_base", description="x")
    llm = ScriptedLLM([_call("search_knowledge_base", {"query": "refund"}), AIMessage(content="Abhi policy check nahi ho pa rahi.")])
    monkeypatch.setattr(graph_module, "get_llm", lambda tools=None: llm)
    monkeypatch.setattr(graph_module, "search_knowledge_base", tool)  # replace the real KB tool
    graph = graph_module.build_agent_graph([])
    out = asyncio.run(graph.ainvoke(_state("refund policy")))
    assert out["messages"][-1].content.startswith("Abhi policy")


def test_infrastructure_failures_still_propagate_so_the_session_gets_reset(monkeypatch):
    """A dead tools process is not a ToolException: hiding it would leave every
    later turn talking to a broken session."""

    def get_invoice(order_id: str) -> str:
        raise ConnectionError("tools subprocess died")

    graph, _ = _build(monkeypatch, get_invoice, [_call("get_invoice", {"order_id": "x"})])
    with pytest.raises(Exception, match="tools subprocess died"):
        asyncio.run(graph.ainvoke(_state()))


def test_a_healthy_tool_is_unaffected(monkeypatch):
    def get_invoice(order_id: str) -> str:
        return f"Invoice for {order_id}: $9.99"

    graph, llm = _build(monkeypatch, get_invoice, [_call("get_invoice", {"order_id": "abc"}), AIMessage(content="Yeh raha invoice.")])
    asyncio.run(graph.ainvoke(_state()))
    assert "Invoice for abc" in llm.seen[1][-1].content
