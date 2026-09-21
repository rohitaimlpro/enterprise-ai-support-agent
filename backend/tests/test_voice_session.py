"""
Drives a real VoiceSession through a fake WebSocket, with fake STT / TTS /
agent graph, so the turn-taking edge cases (barge-in, writes that must not be
cancelled, degradation when STT or TTS fails, ...) are tested without any
network, audio device or model.
"""

import array
import asyncio
import struct
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.routers.chat_router as chat_router_module
import app.voice.session as session_module
from app.models import Conversation, Message, User
from app.voice.phrases import phrase, tool_status
from app.voice.stt import STTError, Transcript
from app.voice.tts import TTSError

SPEECH = array.array("h", [8000 if i % 2 else -8000 for i in range(16000)]).tobytes()  # 1 s, loud
SILENCE = bytes(32000)


class FakeWS:
    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.json: list[dict] = []
        self.audio: list[tuple[int, int, bytes]] = []
        self.closed_with: int | None = None

    async def receive(self):
        return await self.inbox.get()

    async def send_json(self, payload):
        self.json.append(payload)

    async def send_bytes(self, data):
        turn_id, seq = struct.unpack(">II", data[:8])
        self.audio.append((turn_id, seq, data[8:]))

    async def close(self, code=1000, reason=""):
        self.closed_with = code
        await self.inbox.put({"type": "websocket.disconnect"})

    # helpers used by tests
    def push_audio(self, pcm=SPEECH):
        self.inbox.put_nowait({"type": "websocket.receive", "bytes": pcm})

    def push_json(self, text):
        self.inbox.put_nowait({"type": "websocket.receive", "text": text})

    def hangup(self):
        self.inbox.put_nowait({"type": "websocket.disconnect"})

    def types(self):
        return [m["type"] for m in self.json]


class FakeSTT:
    configured = True

    def __init__(self, results):
        self.results = list(results)  # str | (str, confidence) | Exception
        self.calls = 0
        self.vocabularies = []

    async def transcribe(self, pcm, language=None, vocabulary=None):
        self.calls += 1
        self.vocabularies.append(vocabulary)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, tuple):
            return Transcript(r[0], confidence=r[1])
        return Transcript(r)

    async def close(self):
        pass


class FakeTTS:
    def __init__(self, configured=True, fail=False, delay=0.0):
        self.configured = configured
        self.fail = fail
        self.delay = delay
        self.spoken: list[str] = []

    async def synthesize(self, text):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise TTSError("boom")
        self.spoken.append(text)
        return bytes(4000)


def token(t):
    return {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content=t)}}


class ScriptedGraph:
    """`script` is a list of events, or ("sleep", seconds) pauses."""

    def __init__(self, script):
        self.script = script

    async def astream_events(self, state, version="v2"):
        for item in self.script:
            if isinstance(item, tuple):
                await asyncio.sleep(item[1])
            else:
                yield item


@asynccontextmanager
async def fake_tools(user_id):
    yield []


@pytest.fixture()
def env(db_session, monkeypatch):
    """A user in SQLite plus Redis stubbed to a dict."""
    store: dict = {}
    monkeypatch.setattr(chat_router_module, "get_cached_messages", lambda cid: store.get(cid))
    monkeypatch.setattr(chat_router_module, "set_cached_messages", lambda cid, m: store.__setitem__(cid, list(m)))
    monkeypatch.setattr(
        session_module, "append_message", lambda cid, role, content: store.setdefault(cid, []).append({"role": role, "content": content})
    )
    def fake_pop(cid, role, content):
        if store.get(cid) and store[cid][-1] == {"role": role, "content": content}:
            store[cid].pop()

    monkeypatch.setattr(session_module, "pop_last_message", fake_pop)
    user = User(email="v@example.com", hashed_password="x", full_name="Vee")
    db_session.add(user)
    db_session.commit()
    return SimpleNamespace(db=db_session, user=user)


def make_session(env, ws, stt, tts, script=None, expires=None, conversation_id=None):
    graph = ScriptedGraph(script or [token("Your order ships tomorrow. "), token("Anything else?")])
    calls = []

    def builder(tools, system_prompt=None, tool_gate=None):
        calls.append(system_prompt)
        return graph

    s = session_module.VoiceSession(
        ws, env.db, env.user, expires, conversation_id, stt, tts, tools_session=fake_tools, graph_builder=builder
    )
    s.builder_calls = calls
    return s


async def wait_for(pred, timeout=3.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.01)


async def drive(session, ws, scenario):
    runner = asyncio.create_task(session.run())
    try:
        await scenario()
    finally:
        ws.hangup()
        await asyncio.wait_for(runner, 5)
        await session.cleanup()


def run(coro):
    return asyncio.run(coro)


def messages(env):
    return env.db.query(Message).order_by(Message.created_at).all()


# ---------------------------------------------------------------- happy path


def test_full_turn_streams_transcript_tokens_audio_done_and_persists(env):
    ws, stt, tts = FakeWS(), FakeSTT(["where is my order"]), FakeTTS()
    session = make_session(env, ws, stt, tts)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))

    types = ws.types()
    assert types[0] == "ready"
    assert types.index("transcript") < types.index("turn_start") < types.index("token") < types.index("done")
    assert "speaking_end" in types and ws.audio
    assert tts.spoken == ["Your order ships tomorrow.", "Anything else?"]  # sentence-by-sentence TTS
    assert session.builder_calls and "Voice conversation rules" in session.builder_calls[0]
    rows = messages(env)
    assert [m.role.value for m in rows] == ["user", "assistant"]
    assert rows[1].content == "Your order ships tomorrow. Anything else?"
    assert session.conversation and next(m for m in ws.json if m["type"] == "done")["conversation_id"] == session.conversation.id


def test_audio_frames_are_tagged_with_the_turn_id(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["hi there"]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    turn = next(m for m in ws.json if m["type"] == "turn_start")["turn_id"]
    assert ws.audio and {t for t, _, _ in ws.audio} == {turn}
    assert [s for _, s, _ in ws.audio] == list(range(len(ws.audio)))


def test_tool_call_speaks_filler_then_answer(env):
    ws, tts = FakeWS(), FakeTTS()
    script = [
        {"event": "on_tool_start", "name": "lookup_order", "data": {"input": {"order_id": "1"}}},
        {"event": "on_chain_end", "name": "tools", "data": {"output": {"sources": [], "guardrail_flags": []}}},
        token("Your order is on its way."),
    ]
    session = make_session(env, ws, FakeSTT(["where is my order"]), tts, script)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    line = tool_status("lookup_order")
    assert tts.spoken[0] == line  # spoken while the tool runs...
    status = next(m for m in ws.json if m["type"] == "status")
    assert status["text"] == line  # ...and shown on screen
    assert ws.types().index("status") < ws.types().index("token")
    assert "tool_call" in ws.types()


# --------------------------------------------------------------- capture edge cases


def test_silence_is_dropped_without_calling_stt(env):
    ws, stt = FakeWS(), FakeSTT([])
    session = make_session(env, ws, stt, FakeTTS())

    async def scenario():
        ws.push_audio(SILENCE)
        await wait_for(lambda: ws.types().count("ignored") == 1)
        ws.push_audio(b"\x00\x01" * 100)  # far too short
        await wait_for(lambda: ws.types().count("ignored") == 2)

    run(drive(session, ws, scenario))
    assert stt.calls == 0 and not messages(env)


def test_empty_transcripts_reprompt_then_give_up_after_cap(env):
    ws, stt = FakeWS(), FakeSTT(["", "", ""])
    tts = FakeTTS()
    session = make_session(env, ws, stt, tts)

    async def scenario():
        for _ in range(3):
            session._speaking_until = 0.0  # the previous reprompt has finished playing
            ws.push_audio()
            await wait_for(lambda n=_: ws.types().count("turn_start") == n + 1)
            await session.task
        await wait_for(lambda: any(m.get("code") == "stt_failed" for m in ws.json))

    run(drive(session, ws, scenario))
    notices = [m["text"] for m in ws.json if m["type"] == "notice"]
    assert notices[0] == phrase("reprompt") and notices[1] == phrase("reprompt")
    assert notices[2] == phrase("give_up")
    assert not messages(env)  # nothing reached the agent


def test_stt_outage_is_recoverable_reprompt(env):
    ws, stt = FakeWS(), FakeSTT([STTError("429"), "hello"])
    session = make_session(env, ws, stt, FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "notice" in ws.types())
        await session.task
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert [m.role.value for m in messages(env)] == ["user", "assistant"]


def test_card_number_is_redacted_and_user_warned(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["my card is 4111 1111 1111 1111 and I want a refund"]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert any(m.get("text") == phrase("card_warning") for m in ws.json if m["type"] == "notice")
    stored = messages(env)[0].content
    assert "4111" not in stored and "[card number removed]" in stored
    assert "4111" not in next(m for m in ws.json if m["type"] == "transcript")["text"]


def test_spoken_digits_are_normalized_before_the_agent_sees_them(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["cancel order one two three four"]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert messages(env)[0].content == "cancel order 1234"


# ---------------------------------------------------------------- TTS degradation


def test_tts_failure_falls_back_to_captions_and_conversation_continues(env):
    ws, tts = FakeWS(), FakeTTS(fail=True)
    session = make_session(env, ws, FakeSTT(["hello"]), tts)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert "tts_unavailable" in ws.types() and not ws.audio
    assert "".join(m["content"] for m in ws.json if m["type"] == "token")  # captions still flowed
    assert messages(env)[1].content == "Your order ships tomorrow. Anything else?"


def test_tts_not_configured_announces_captions_only_at_start(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT([]), FakeTTS(configured=False))

    async def scenario():
        await wait_for(lambda: "tts_unavailable" in ws.types())

    run(drive(session, ws, scenario))
    assert next(m for m in ws.json if m["type"] == "ready")["tts"] is False


def test_stt_not_configured_closes_with_clear_error(env):
    ws, stt = FakeWS(), FakeSTT([])
    stt.configured = False
    session = make_session(env, ws, stt, FakeTTS())
    run(session.run())
    assert any(m.get("code") == "stt_unavailable" for m in ws.json) and ws.closed_with == 4503


# ---------------------------------------------------------------- turn-taking


def test_barge_in_cancels_turn_and_persists_only_what_was_spoken(env):
    ws, tts = FakeWS(), FakeTTS()
    script = [token("First sentence is spoken. "), token("Second is never heard"), ("sleep", 30), token(" end.")]
    session = make_session(env, ws, FakeSTT(["tell me a story"]), tts, script)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)  # first sentence is playing
        ws.push_json('{"type":"interrupt"}')
        await wait_for(lambda: "interrupted" in ws.types())
        await asyncio.wait_for(asyncio.shield(session.task), 3) if not session.task.done() else None

    run(drive(session, ws, scenario))
    assistant = [m for m in messages(env) if m.role.value == "assistant"]
    assert len(assistant) == 1 and assistant[0].content == "First sentence is spoken."
    assert next(m for m in ws.json if m["type"] == "interrupted")["cancelled"] is True
    assert "done" not in ws.types()  # a cancelled turn never claims completion


def test_new_utterance_supersedes_the_running_turn(env):
    ws = FakeWS()
    stt = FakeSTT(["first question", "second question"])
    slow = [token("Working on the first one. "), ("sleep", 30)]
    session = make_session(env, ws, stt, FakeTTS(), slow)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)
        session._graph_builder = lambda tools, system_prompt=None, tool_gate=None: ScriptedGraph([token("Second answer here.")])
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    done = [m for m in ws.json if m["type"] == "done"]
    assert len(done) == 1 and done[0]["turn_id"] == 2
    users = [m.content for m in messages(env) if m.role.value == "user"]
    assert users == ["first question", "second question"]


def test_interrupt_during_refund_mutes_but_never_cancels_the_write(env):
    ws, tts = FakeWS(), FakeTTS()
    finished = []

    class WriteGraph:
        async def astream_events(self, state, version="v2"):
            yield {"event": "on_tool_start", "name": "refund_request", "data": {"input": {"order_id": "abc"}}}
            await asyncio.sleep(0.3)  # the write is "in flight"
            finished.append("write")
            yield {"event": "on_chain_end", "name": "tools", "data": {"output": {"sources": [], "guardrail_flags": []}}}
            yield token("Your refund has been filed.")

    session = make_session(env, ws, FakeSTT(["yes go ahead", "and what else"]), tts)
    session._graph_builder = lambda tools, system_prompt=None, tool_gate=None: WriteGraph()

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "tool_call" in ws.types())
        ws.push_json('{"type":"interrupt"}')
        session._graph_builder = lambda tools, system_prompt=None, tool_gate=None: ScriptedGraph([token("Nothing else.")])
        ws.push_audio()  # user talks again while the write is still running
        await wait_for(lambda: ws.types().count("done") == 2)

    run(drive(session, ws, scenario))
    assert finished == ["write"]  # the write completed
    assert any(m.get("reason") == "queued" for m in ws.json)  # the browser is told it can stop waiting
    assert next(m for m in ws.json if m["type"] == "interrupted")["cancelled"] is False  # a `done` still follows
    assistant = [m.content for m in messages(env) if m.role.value == "assistant"]
    assert assistant[0] == "Your refund has been filed."  # outcome recorded, not lost
    assert "Nothing else." in assistant  # queued utterance ran afterwards
    first_turn = next(m for m in ws.json if m["type"] == "turn_start")["turn_id"]
    # Only the pre-interrupt filler was voiced; the muted answer stayed captions-only.
    assert "Your refund has been filed." not in tts.spoken
    assert any(m["type"] == "token" and m["turn_id"] == first_turn for m in ws.json)


def test_repeat_request_replays_last_answer_without_calling_agent(env):
    ws, tts = FakeWS(), FakeTTS()
    session = make_session(env, ws, FakeSTT(["hello", "say that again"]), tts)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())
        await session.task
        ws.push_audio()
        await wait_for(lambda: ws.types().count("speaking_end") == 2)

    run(drive(session, ws, scenario))
    assert len(session.builder_calls) == 1  # agent ran once; the repeat was replayed locally
    assert tts.spoken[-1].startswith("Your order ships tomorrow")


# ---------------------------------------------------------------- session lifecycle


def test_expired_token_closes_with_reauth_signal(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["x"]), FakeTTS(), expires=datetime.now(timezone.utc) - timedelta(seconds=1))

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.closed_with is not None)

    run(drive(session, ws, scenario))
    assert ws.closed_with == 4401 and any(m.get("code") == "token_expired" for m in ws.json)


def test_reconnect_resumes_existing_conversation_with_history(env):
    conv = Conversation(user_id=env.user.id, title="earlier")
    env.db.add(conv)
    env.db.commit()
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["one more thing"]), FakeTTS(), conversation_id=conv.id)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert next(m for m in ws.json if m["type"] == "ready")["conversation_id"] == conv.id
    assert env.db.query(Conversation).count() == 1


def test_someone_elses_conversation_id_is_ignored(env):
    other = User(email="o@example.com", hashed_password="x", full_name="Other")
    env.db.add(other)
    env.db.commit()
    conv = Conversation(user_id=other.id, title="private")
    env.db.add(conv)
    env.db.commit()
    session = make_session(env, FakeWS(), FakeSTT([]), FakeTTS(), conversation_id=conv.id)
    assert session.conversation is None


def test_ping_gets_pong(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT([]), FakeTTS())

    async def scenario():
        ws.push_json('{"type":"ping"}')
        await wait_for(lambda: "pong" in ws.types())

    run(drive(session, ws, scenario))


def test_disconnect_mid_turn_cleans_up_without_error(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["hello"]), FakeTTS(), [token("Starting to answer. "), ("sleep", 30)])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)

    run(drive(session, ws, scenario))  # drive() hangs up mid-turn, then cleanup() must not raise or hang
    assert session.task.done()
