"""The tools connection is opened as soon as a call connects (so the first
question isn't slow), and a fast first utterance must not open a second one."""

import asyncio
from contextlib import asynccontextmanager

from tests.test_voice_session import (  # noqa: F401  (env is a pytest fixture)
    FakeSTT,
    FakeTTS,
    FakeWS,
    ScriptedGraph,
    drive,
    env,
    make_session,
    run,
    token,
    wait_for,
)


def counting_tools(delay=0.0):
    opened = []

    @asynccontextmanager
    async def tools_session(user_id):
        await asyncio.sleep(delay)
        opened.append(user_id)
        yield []

    return tools_session, opened


def test_tools_are_opened_at_connect_before_any_speech(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT([]), FakeTTS())
    tools_session, opened = counting_tools()
    session._tools_session = tools_session

    async def scenario():
        await wait_for(lambda: opened)  # nobody has spoken yet

    run(drive(session, ws, scenario))
    assert opened == [str(env.user.id)]


def test_fast_first_utterance_does_not_open_a_second_connection(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["hello"]), FakeTTS())
    tools_session, opened = counting_tools(delay=0.2)  # slow start: the warm-up is still in flight
    session._tools_session = tools_session

    async def scenario():
        ws.push_audio()  # user speaks immediately
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert len(opened) == 1


def test_a_failing_warmup_is_not_fatal_and_the_first_turn_retries(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["hello"]), FakeTTS())
    attempts = []

    @asynccontextmanager
    async def flaky(user_id):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("subprocess failed to start")
        yield []

    session._tools_session = flaky

    async def scenario():
        await wait_for(lambda: attempts)  # warm-up failed quietly
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert len(attempts) == 2 and "done" in ws.types()
