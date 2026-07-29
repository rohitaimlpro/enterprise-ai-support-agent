"""Shared state threaded through every node of the agent graph."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # add_messages appends new messages instead of overwriting the list,
    # and matches AIMessage tool_calls to their ToolMessage replies.
    messages: Annotated[list, add_messages]
    # RAG citations collected while answering this turn. Accumulated with
    # operator.add (list concatenation) since search_knowledge_base may be
    # called more than once in a single turn.
    sources: Annotated[list[dict], operator.add]
