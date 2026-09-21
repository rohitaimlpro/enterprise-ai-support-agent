"""
Spawns app/tools/mcp_server.py as a subprocess (talking MCP over stdio) and
exposes its tools as LangChain tools the LangGraph agent can call.

Each subprocess is scoped to one authenticated user via the
SUPPORT_AGENT_USER_ID environment variable -- see mcp_server.py for why
that's safer than an LLM-supplied user_id.

Two ways to get a session:

* `mcp_tools_session(user_id)` -- opens a fresh subprocess and closes it on
  exit. Simple and self-contained; used by the eval and red-team scripts.
* `shared_mcp_tools_session(user_id)` -- borrows a pooled, long-lived
  subprocess for that user. Starting one costs ~1 s (spawn Python, import
  the app, MCP handshake), which was paid on every single chat turn; the
  pool pays it once and keeps the process warm for a few minutes. Used by
  the live chat and voice endpoints.
"""

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.observability.logging_config import get_logger

logger = get_logger(__name__)


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


# ---------------------------------------------------------------------------
# Session pool
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _Entry:
    user_id: str
    stop: asyncio.Event
    loop: asyncio.AbstractEventLoop
    tools: list = field(default_factory=list)
    task: asyncio.Task | None = None
    refs: int = 0  # turns/calls currently borrowing this session
    last_used: float = 0.0
    dead: bool = False


class McpSessionPool:
    """One warm MCP subprocess per user, shared by that user's requests.

    Why each session lives in its own background "owner" task: the MCP stdio
    client is built on anyio task groups, which must be entered and exited
    by the *same* task. Request handlers (and voice turns) come and go on
    different tasks, so a handler can never own the session; a dedicated task
    opens it, parks until told to stop, then closes it.

    Safety notes:
    * The pool key is the authenticated user id and each subprocess is
      launched with that id in its environment, so one user's requests can
      never reach another user's session.
    * Idle sessions are closed after `idle_seconds` and the pool is capped at
      `max_size` (idle least-recently-used evicted first), bounding the
      number of live Python subprocesses.
    """

    def __init__(self, opener=None, idle_seconds: float = 300, max_size: int = 20, start_timeout: float = 30):
        self._opener = opener or mcp_tools_session
        self.idle_seconds = idle_seconds
        self.max_size = max_size
        self.start_timeout = start_timeout
        self._entries: dict[str, _Entry] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reaper: asyncio.Task | None = None

    # -- borrowing ---------------------------------------------------------

    async def acquire(self, user_id: str) -> _Entry:
        loop = asyncio.get_running_loop()
        self._adopt_loop(loop)
        # Per-user lock: starting user A's subprocess mustn't block user B.
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(user_id)
            if entry is not None and (entry.dead or entry.task is None or entry.task.done()):
                await self._discard(entry)
                entry = None
            if entry is None:
                await self._make_room()
                entry = await self._start(user_id, loop)
                self._entries[user_id] = entry
            entry.refs += 1
            entry.last_used = time.monotonic()
        self._ensure_reaper(loop)
        return entry

    def release(self, entry: _Entry) -> None:
        entry.refs = max(0, entry.refs - 1)
        entry.last_used = time.monotonic()

    async def invalidate(self, user_id: str) -> None:
        """Drop a user's session so the next request starts a fresh one.
        Called when a turn failed -- the subprocess may be wedged."""
        entry = self._entries.get(user_id)
        if entry is not None:
            await self._discard(entry)

    @asynccontextmanager
    async def session(self, user_id: str):
        entry = await self.acquire(user_id)
        try:
            yield entry.tools
        except Exception:
            # A failed turn may mean a broken subprocess; don't hand it to
            # the user's next request. (CancelledError is BaseException and
            # deliberately not caught: a barge-in cancel isn't a fault.)
            if not entry.dead:
                await self.invalidate(user_id)
            raise
        finally:
            self.release(entry)

    # -- lifecycle ----------------------------------------------------------

    def _adopt_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        # asyncio primitives and tasks are bound to one loop. Production has
        # a single loop; tests spin up many. On a change, forget everything
        # from the old loop (it's gone; its tasks can't be awaited anymore).
        if self._loop is not loop:
            self._loop = loop
            self._entries.clear()
            self._user_locks.clear()
            self._reaper = None

    async def _start(self, user_id: str, loop: asyncio.AbstractEventLoop) -> _Entry:
        entry = _Entry(user_id=user_id, stop=asyncio.Event(), loop=loop)
        ready: asyncio.Future = loop.create_future()

        async def owner() -> None:
            try:
                async with self._opener(user_id) as tools:
                    entry.tools = tools
                    if not ready.done():
                        ready.set_result(None)
                    await entry.stop.wait()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # startup failure, or the subprocess died later
                if not ready.done():
                    ready.set_exception(exc)
                else:
                    logger.warning("mcp_session_died user=%s error=%s", user_id, exc)
            finally:
                entry.dead = True
                if not ready.done():
                    ready.set_exception(RuntimeError("MCP session closed before it was ready"))

        entry.task = asyncio.create_task(owner())
        try:
            await asyncio.wait_for(ready, timeout=self.start_timeout)
        except BaseException:
            entry.stop.set()
            entry.task.cancel()
            entry.dead = True
            raise
        return entry

    async def _discard(self, entry: _Entry) -> None:
        entry.dead = True
        if self._entries.get(entry.user_id) is entry:
            del self._entries[entry.user_id]
        entry.stop.set()
        task = entry.task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.TimeoutError, Exception):
                task.cancel()

    async def _make_room(self) -> None:
        if len(self._entries) < self.max_size:
            return
        idle = [e for e in self._entries.values() if e.refs == 0]
        if idle:  # if everything is busy we exceed the cap rather than fail a request
            await self._discard(min(idle, key=lambda e: e.last_used))

    async def reap(self) -> int:
        """Close sessions nobody has used for `idle_seconds`. Returns how many."""
        now = time.monotonic()
        stale = [e for e in self._entries.values() if e.refs == 0 and now - e.last_used > self.idle_seconds]
        for entry in stale:
            await self._discard(entry)
        return len(stale)

    def _ensure_reaper(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = loop.create_task(self._reap_forever())

    async def _reap_forever(self) -> None:
        while True:
            await asyncio.sleep(max(1.0, min(30.0, self.idle_seconds / 2)))
            try:
                await self.reap()
            except Exception:
                logger.exception("mcp_pool_reap_failed")

    async def close_all(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for entry in list(self._entries.values()):
            await self._discard(entry)


_pool = McpSessionPool()


def shared_mcp_tools_session(user_id: str):
    """Async context manager yielding the user's pooled, warm MCP tools."""
    return _pool.session(user_id)


async def invalidate_shared_session(user_id: str) -> None:
    await _pool.invalidate(user_id)


async def close_shared_sessions() -> None:
    """Called on app shutdown so no MCP subprocess is left running."""
    await _pool.close_all()
