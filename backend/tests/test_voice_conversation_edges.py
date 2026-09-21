"""One-to-one conversation edge cases: the agent hearing itself, acknowledgement
noises, a sentence split by a pause, endless monologues, goodbyes, and what
gets stored when a turn is cut short."""

import asyncio

from app.config import get_settings
from app.voice import phrases
from app.voice import session as session_module
from app.voice.phrases import phrase, tool_status
from app.voice.stt import Transcript
from tests.test_voice_session import (  # noqa: F401  (env is a pytest fixture)
    FakeSTT,
    FakeTTS,
    FakeWS,
    ScriptedGraph,
    drive,
    env,
    make_session,
    messages,
    run,
    token,
    wait_for,
)

SLOW = [("sleep", 30)]  # a turn that stays "running"


def graph_builder(graph):
    return lambda tools, system_prompt=None, tool_gate=None: graph


class DelayedSTT(FakeSTT):
    """FakeSTT whose calls take different amounts of time."""

    def __init__(self, results, delays):
        super().__init__(results)
        self.delays = list(delays)

    async def transcribe(self, pcm, language=None, vocabulary=None):
        await asyncio.sleep(self.delays.pop(0))
        return await super().transcribe(pcm, language, vocabulary)


# ---------------------------------------------------------------- the agent hearing itself


def test_echo_of_the_agents_own_voice_is_dropped_and_does_not_cancel_its_answer(env):
    ws = FakeWS()
    said = "Aapke orders mein Graphics Tablet Pro shipped ho chuka hai."
    stt = FakeSTT(["mera order kahan hai", "aapke orders mein graphics tablet pro shipped ho chuka hai"])
    session = make_session(env, ws, stt, FakeTTS(), [token(said + " "), ("sleep", 30)])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)  # the agent is speaking
        ws.push_audio()  # ...and its own voice comes back through the microphone
        await wait_for(lambda: any(m.get("reason") == "echo" for m in ws.json))
        assert not session.task.done()  # the answer was NOT cancelled by its own echo

    run(drive(session, ws, scenario))
    assert ws.types().count("transcript") == 1  # the echo never became a user message
    assert [m.content for m in messages(env) if m.role.value == "user"] == ["mera order kahan hai"]


def test_genuine_user_speech_that_is_not_an_echo_still_interrupts(env):
    ws = FakeWS()
    stt = FakeSTT(["mera order kahan hai", "mujhe refund chahiye"])
    session = make_session(env, ws, stt, FakeTTS(), [token("Aapke orders mein Graphics Tablet Pro hai. "), ("sleep", 30)])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)
        session._graph_builder = graph_builder(ScriptedGraph([token("Theek hai, refund ke liye order batayiye.")]))
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert [m.content for m in messages(env) if m.role.value == "user"] == ["mera order kahan hai", "mujhe refund chahiye"]


# ---------------------------------------------------------------- "hmm", "haan" while it talks


def test_acknowledgement_noises_while_the_agent_talks_are_ignored(env):
    ws = FakeWS()
    stt = FakeSTT(["mera order kahan hai", "hmm", "haan ji"])
    session = make_session(env, ws, stt, FakeTTS(), [token("Aapke teen orders hain. "), ("sleep", 30)])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)
        for n in (1, 2):
            ws.push_audio()
            await wait_for(lambda n=n: [m.get("reason") for m in ws.json].count("backchannel") == n)
        assert not session.task.done()

    run(drive(session, ws, scenario))
    assert ws.types().count("transcript") == 1


def test_a_lone_haan_when_nothing_is_happening_is_a_real_answer(env):
    """'haan' to 'shall I go ahead?' must reach the agent -- it is only noise
    while the agent is mid-sentence."""
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["haan"]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert [m.content for m in messages(env) if m.role.value == "user"] == ["haan"]


def test_unintelligible_audio_under_the_agents_voice_does_not_trigger_a_reprompt(env):
    ws = FakeWS()
    stt = FakeSTT(["mera order kahan hai", ""])  # second clip: only noise, STT returns nothing
    session = make_session(env, ws, stt, FakeTTS(), [token("Aapke teen orders hain. "), ("sleep", 30)])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)
        ws.push_audio()
        await wait_for(lambda: stt.calls == 2)
        await asyncio.sleep(0.1)
        assert "notice" not in ws.types()  # no "sorry, say that again" over its own speech
        assert not session.task.done()

    run(drive(session, ws, scenario))


# ---------------------------------------------------------------- a sentence split by a pause


def test_a_follow_up_before_the_agent_answers_is_merged_into_one_request(env):
    ws = FakeWS()
    stt = FakeSTT(["mujhe refund chahiye", "apne tablet order ka"])
    session = make_session(env, ws, stt, FakeTTS(), SLOW)  # first half: the agent is still thinking

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "turn_start" in ws.types())
        session._graph_builder = graph_builder(ScriptedGraph([token("Theek hai, Graphics Tablet Pro ka refund check karte hain.")]))
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    transcripts = [m for m in ws.json if m["type"] == "transcript"]
    assert transcripts[-1]["merged"] is True
    assert transcripts[-1]["text"] == "mujhe refund chahiye apne tablet order ka"
    # History holds ONE user message with the whole request, not two fragments.
    assert [m.content for m in messages(env) if m.role.value == "user"] == ["mujhe refund chahiye apne tablet order ka"]


def test_no_merge_once_the_agent_has_started_answering(env):
    ws = FakeWS()
    stt = FakeSTT(["mera order kahan hai", "aur invoice bhi chahiye"])
    session = make_session(env, ws, stt, FakeTTS(), [token("Aapke teen orders hain. "), ("sleep", 30)])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)
        session._graph_builder = graph_builder(ScriptedGraph([token("Invoice bhej dete hain.")]))
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert all(m["merged"] is False for m in ws.json if m["type"] == "transcript")
    assert len([m for m in messages(env) if m.role.value == "user"]) == 2


def test_no_merge_after_the_window_has_passed(env, monkeypatch):
    monkeypatch.setattr(session_module, "_MERGE_WINDOW_SECONDS", 0.0)
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["pehli baat", "doosri baat"]), FakeTTS(), SLOW)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "turn_start" in ws.types())
        session._graph_builder = graph_builder(ScriptedGraph([token("Theek hai.")]))
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert all(m["merged"] is False for m in ws.json if m["type"] == "transcript")


def test_utterances_are_handled_in_the_order_they_were_spoken(env):
    ws = FakeWS()
    stt = DelayedSTT(["pehli baat", "doosri baat"], delays=[0.25, 0.0])  # the first takes longer to transcribe
    session = make_session(env, ws, stt, FakeTTS(), SLOW)

    async def scenario():
        ws.push_audio()
        ws.push_audio()
        await wait_for(lambda: ws.types().count("transcript") >= 1)
        await asyncio.sleep(0.3)

    run(drive(session, ws, scenario))
    texts = [m["text"] for m in ws.json if m["type"] == "transcript"]
    assert texts[0] == "pehli baat"  # never "doosri baat" first


# ---------------------------------------------------------------- what gets stored


def test_a_cut_off_turn_does_not_store_the_checking_line_as_something_the_agent_said(env):
    ws, tts = FakeWS(), FakeTTS()
    script = [
        {"event": "on_tool_start", "name": "lookup_order", "data": {"input": {}}},
        ("sleep", 30),
    ]
    session = make_session(env, ws, FakeSTT(["mera order kahan hai"]), tts, script)

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: tts.spoken)  # "Ek second, aapka order dekhte hain." was spoken
        ws.push_json('{"type":"interrupt"}')
        await wait_for(lambda: "interrupted" in ws.types())

    run(drive(session, ws, scenario))
    assert tts.spoken == [tool_status("lookup_order")]
    assert [m for m in messages(env) if m.role.value == "assistant"] == []


# ---------------------------------------------------------------- monologues


def test_long_answers_are_only_partly_spoken_and_say_the_rest_is_in_chat(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "voice_max_spoken_chars", 70)
    ws, tts = FakeWS(), FakeTTS()
    sentences = ["Pehla order processing mein hai.", "Doosra order shipped ho chuka hai.", "Teesra order deliver ho gaya hai."]
    session = make_session(env, ws, FakeSTT(["mere orders batao"]), tts, [token(s + " ") for s in sentences])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert tts.spoken == [sentences[0], sentences[1], phrase("more_in_chat")]
    captions = "".join(m["content"] for m in ws.json if m["type"] == "token")
    assert sentences[2] in captions  # nothing is lost from the screen
    stored = [m.content for m in messages(env) if m.role.value == "assistant"][0]
    assert sentences[2] in stored and phrase("more_in_chat") not in stored


def test_a_cap_of_zero_means_no_limit(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "voice_max_spoken_chars", 0)
    ws, tts = FakeWS(), FakeTTS()
    sentences = [f"Yeh order number {i} ki lambi si detail hai." for i in range(8)]
    session = make_session(env, ws, FakeSTT(["batao"]), tts, [token(s + " ") for s in sentences])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert tts.spoken == sentences


# ---------------------------------------------------------------- goodbye and numbers


def test_saying_goodbye_ends_the_call_after_the_farewell(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["theek hai, bas itna hi, bye"]), FakeTTS(), [token("Dhanyavad, aapka din shubh ho!")])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "hangup" in ws.types())

    run(drive(session, ws, scenario))
    types = ws.types()
    assert types.index("done") < types.index("hangup")  # the farewell is delivered first


def test_a_normal_message_never_hangs_up(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["that's all I paid for my order last week, please check it"]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert "hangup" not in ws.types()


def test_hindi_number_words_become_digits_before_the_agent_sees_them(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["mera number nau aath saat chhe paanch hai"]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert messages(env)[0].content == "mera number 98765 hai"


def test_reprompt_still_works_when_the_agent_is_idle(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT([""]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: any(m.get("text") == phrase("reprompt") for m in ws.json))

    run(drive(session, ws, scenario))
    assert phrases.phrase("reprompt")
