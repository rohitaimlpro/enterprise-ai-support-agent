"""The per-user MCP subprocess pool: reuse, isolation, lifecycle, and the
anyio requirement that a session is opened and closed by the same task."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.tools.mcp_client import McpSessionPool


class FakeOpener:
    """Stands in for mcp_tools_session and records how it was used."""

    def __init__(self, fail_for: set[str] | None = None):
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.enter_task: dict[str, asyncio.Task] = {}
        self.exit_task: dict[str, asyncio.Task] = {}
        self.fail_for = fail_for or set()

    @asynccontextmanager
    async def __call__(self, user_id):
        if user_id in self.fail_for:
            raise RuntimeError("spawn failed")
        await asyncio.sleep(0.01)  # pretend subprocess start-up
        self.opened.append(user_id)
        self.enter_task[user_id] = asyncio.current_task()
        try:
            yield [f"tools-for-{user_id}"]
        finally:
            self.closed.append(user_id)
            self.exit_task[user_id] = asyncio.current_task()


def run(coro):
    return asyncio.run(coro)


def test_second_use_reuses_the_warm_session():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener)
        async with pool.session("u1") as t1:
            pass
        async with pool.session("u1") as t2:
            pass
        assert t1 == t2 == ["tools-for-u1"]
        assert opener.opened == ["u1"]  # spawned once, borrowed twice
        await pool.close_all()

    run(go())


def test_users_never_share_a_session():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener)
        async with pool.session("alice") as a, pool.session("bob") as b:
            assert a == ["tools-for-alice"] and b == ["tools-for-bob"]
        assert sorted(opener.opened) == ["alice", "bob"]
        await pool.close_all()

    run(go())


def test_concurrent_requests_for_one_user_start_a_single_session():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener)

        async def use():
            async with pool.session("u1"):
                await asyncio.sleep(0.02)

        await asyncio.gather(*(use() for _ in range(6)))
        assert opener.opened == ["u1"]
        await pool.close_all()

    run(go())


def test_session_is_opened_and_closed_by_the_same_task_even_from_different_callers():
    """The bug this pool exists to prevent: opened in one request task,
    closed from another. anyio task groups forbid that."""

    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener)

        async def borrow_in_own_task():
            async with pool.session("u1"):
                pass

        await asyncio.create_task(borrow_in_own_task())
        await asyncio.create_task(borrow_in_own_task())
        await pool.close_all()
        assert opener.enter_task["u1"] is opener.exit_task["u1"]
        assert opener.enter_task["u1"] is not asyncio.current_task()

    run(go())


def test_idle_sessions_are_reaped_but_busy_ones_are_not():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener, idle_seconds=0.05)
        async with pool.session("busy"):
            async with pool.session("idle"):
                pass
            await asyncio.sleep(0.1)
            assert await pool.reap() == 1  # "idle" only; "busy" is still borrowed
            assert opener.closed == ["idle"]
        await pool.close_all()

    run(go())


def test_reaped_session_is_restarted_on_next_use():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener, idle_seconds=0.01)
        async with pool.session("u1"):
            pass
        await asyncio.sleep(0.03)
        await pool.reap()
        async with pool.session("u1"):
            pass
        assert opener.opened == ["u1", "u1"]
        await pool.close_all()

    run(go())


def test_a_failed_turn_discards_the_session_so_the_next_one_starts_fresh():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener)
        with pytest.raises(ValueError):
            async with pool.session("u1"):
                raise ValueError("tool call blew up")
        async with pool.session("u1"):
            pass
        assert opener.opened == ["u1", "u1"]
        assert opener.closed[0] == "u1"
        await pool.close_all()

    run(go())


def test_a_cancelled_turn_does_not_discard_the_session():
    """Barge-in cancels turns constantly; that must not churn subprocesses."""

    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener)

        async def turn():
            async with pool.session("u1"):
                await asyncio.sleep(10)

        t = asyncio.create_task(turn())
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        async with pool.session("u1"):
            pass
        assert opener.opened == ["u1"]
        await pool.close_all()

    run(go())


def test_session_that_died_on_its_own_is_replaced():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener)
        async with pool.session("u1"):
            pass
        entry = pool._entries["u1"]
        entry.task.cancel()  # simulate the subprocess/owner task dying
        await asyncio.sleep(0.02)
        async with pool.session("u1") as tools:
            assert tools == ["tools-for-u1"]
        assert opener.opened == ["u1", "u1"]
        await pool.close_all()

    run(go())


def test_startup_failure_propagates_and_is_not_cached():
    async def go():
        opener = FakeOpener(fail_for={"bad"})
        pool = McpSessionPool(opener=opener)
        with pytest.raises(RuntimeError, match="spawn failed"):
            async with pool.session("bad"):
                pass
        assert "bad" not in pool._entries
        await pool.close_all()

    run(go())


def test_pool_is_capped_and_evicts_least_recently_used_idle_session():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener, max_size=2)
        for user in ("a", "b", "c"):
            async with pool.session(user):
                await asyncio.sleep(0.01)
        assert set(pool._entries) == {"b", "c"}
        assert "a" in opener.closed
        await pool.close_all()

    run(go())


def test_close_all_stops_every_subprocess():
    async def go():
        opener = FakeOpener()
        pool = McpSessionPool(opener=opener)
        for user in ("a", "b"):
            async with pool.session(user):
                pass
        await pool.close_all()
        assert sorted(opener.closed) == ["a", "b"] and not pool._entries

    run(go())
