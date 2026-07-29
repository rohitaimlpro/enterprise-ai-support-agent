"""
Spawns app/tools/mcp_server.py as a subprocess (talking MCP over stdio) and
exposes its tools as LangChain tools the LangGraph agent can call.

Each chat turn opens its own short-lived MCP session, scoped to the
authenticated user via the SUPPORT_AGENT_USER_ID environment variable --
see mcp_server.py for why that's safer than an LLM-supplied user_id.
"""

import os
import sys
from contextlib import asynccontextmanager

from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _server_params(user_id: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.tools.mcp_server"],
        env={**os.environ, "SUPPORT_AGENT_USER_ID": user_id},
    )


@asynccontextmanager
async def mcp_tools_session(user_id: str):
    """Yields a list of LangChain tools backed by a live MCP session.

    Usage:
        async with mcp_tools_session(user.id) as tools:
            ...build/run the agent graph with these tools...

    The subprocess and its stdio pipes stay alive for as long as the `with`
    block is open -- tool calls made after it exits will fail, so the
    agent run must happen entirely inside this context.
    """
    async with stdio_client(_server_params(user_id)) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            yield tools
