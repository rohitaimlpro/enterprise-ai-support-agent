"""Speech-recognition word errors: correcting known misrecognitions, not acting
on low-confidence guesses, and giving Whisper this user's vocabulary."""

import asyncio

import httpx
import pytest

from app.config import get_settings
from app.models import Order, OrderStatus
from app.voice.phrases import phrase
from app.voice.stt import GroqTranscriber, judge_result
from app.voice.text_utils import correct_transcript
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


# ------------------------------------------------------------------ correcting known errors


class TestCorrectTranscript:
    @pytest.mark.parametrize(
        "heard, expected",
        [
            ("Nahi nahi, rene do", "Nahi nahi, rehne do"),
            ("Mujhe apne order kaa refund chahiye", "Mujhe apne order ka refund chahiye"),
            ("मुझे मेरा रिफंड चाहिए", "मुझे मेरा refund चाहिए"),
            ("मेरा ऑर्डर कैंसल कर दो", "मेरा order cancel कर दो"),
            ("Mujhe rifund chahiye", "Mujhe refund chahiye"),
            ("Please send my invoyce", "Please send my invoice"),
            ("I want to call back later", "I want to callback later"),
            ("mere ordar ka status", "mere order ka status"),
            ("Refund chahiye", "Refund chahiye"),
            # Whisper spells "rehne do" differently every time
            ("Nahi nahi, rehnye do", "Nahi nahi, rehne do"),
            ("rehnay do", "rehne do"),
            ("rahne do", "rehne do"),
        ],
    )
    def test_known_misrecognitions_are_repaired(self, heard, expected):
        assert correct_transcript(heard) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "my order was delivered and cancelled and refunded",  # inflections of key words stay as spoken
            "the border is closed",  # a real word that merely looks like 'order'
            "Graphics Tablet Pro order",  # product names untouched
            "subscription plan",
            "Aapke orders ki details",
            "haan kar do",
            "please renew do it",  # looks a bit like "rehne do" but isn't
        ],
    )
    def test_correct_words_are_left_alone(self, text):
        assert correct_transcript(text) == text

    def test_capitalisation_is_kept(self):
        assert correct_transcript("Rifund chahiye") == "Refund chahiye"


# ------------------------------------------------------------------ confidence + vocabulary (STT layer)


def test_confidence_is_the_mean_of_segment_scores():
    r = judge_result({"text": "mera order", "segments": [{"avg_logprob": -0.2}, {"avg_logprob": -0.6}]})
    assert r.confidence == pytest.approx(-0.4)


def test_confidence_is_none_without_segments():
    assert judge_result({"text": "mera order", "segments": []}).confidence is None


def _capture_request(monkeypatch, vocabulary):
    monkeypatch.setattr(get_settings(), "groq_api_key", "test")
    seen = {}

    def handler(request):
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ok", "segments": []})

    stt = GroqTranscriber(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(stt.transcribe(bytes(32000), vocabulary=vocabulary))
    return seen["body"]


def test_users_product_names_are_sent_to_whisper_as_vocabulary(monkeypatch):
    body = _capture_request(monkeypatch, ["Graphics Tablet Pro", "Premium Font & Template Pack"])
    assert b"Graphics Tablet Pro, Premium Font & Template Pack." in body


def test_no_vocabulary_leaves_the_prompt_unchanged(monkeypatch):
    body = _capture_request(monkeypatch, None)
    assert b"Graphics Tablet Pro" not in body and b"Meridian Suite support call" in body


# ------------------------------------------------------------------ session behaviour


def test_low_confidence_transcript_is_not_acted_on_and_the_user_is_asked_again(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT([("refund chahiye", -1.5)]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: any(m.get("text") == phrase("reprompt") for m in ws.json))

    run(drive(session, ws, scenario))
    assert "transcript" not in ws.types() and not messages(env)  # a guess never reached the agent


def test_medium_confidence_is_acted_on_but_flagged_as_possibly_misheard(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT([("mera order kahan hai", -0.9)]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert next(m for m in ws.json if m["type"] == "transcript")["low_confidence"] is True


def test_clear_speech_is_not_flagged(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT([("mera order kahan hai", -0.25)]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert next(m for m in ws.json if m["type"] == "transcript")["low_confidence"] is False


def test_word_errors_are_repaired_before_the_agent_and_history_see_them(env):
    ws = FakeWS()
    session = make_session(env, ws, FakeSTT(["mujhe apne order kaa rifund chahiye"]), FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert messages(env)[0].content == "mujhe apne order ka refund chahiye"


def test_the_users_product_names_are_passed_to_speech_recognition(env):
    env.db.add(Order(user_id=env.user.id, product_name="Graphics Tablet Pro", status=OrderStatus.shipped, amount=10.0))
    env.db.add(Order(user_id=env.user.id, product_name="Premium Font Pack", status=OrderStatus.processing, amount=5.0))
    env.db.commit()
    ws, stt = FakeWS(), FakeSTT(["mera order kahan hai"])
    session = make_session(env, ws, stt, FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert sorted(stt.vocabularies[0]) == ["Graphics Tablet Pro", "Premium Font Pack"]


def test_only_this_users_products_are_used_as_vocabulary(env):
    from app.models import User

    other = User(email="other@example.com", hashed_password="x", full_name="Other")
    env.db.add(other)
    env.db.commit()
    env.db.add(Order(user_id=other.id, product_name="Someone Elses Secret Product", status=OrderStatus.shipped, amount=1.0))
    env.db.commit()
    ws, stt = FakeWS(), FakeSTT(["hello"])
    session = make_session(env, ws, stt, FakeTTS())

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: "done" in ws.types())

    run(drive(session, ws, scenario))
    assert stt.vocabularies[0] == []


def test_unclear_audio_while_the_agent_talks_tells_the_client_to_carry_on(env):
    """The client pauses the agent's audio when it hears speech; if the server
    then can't make sense of it, the client must be told to resume."""
    ws = FakeWS()
    stt = FakeSTT(["mera order kahan hai", ""])
    session = make_session(env, ws, stt, FakeTTS(), [token("Aapke teen orders hain. "), ("sleep", 30)])

    async def scenario():
        ws.push_audio()
        await wait_for(lambda: ws.audio)
        ws.push_audio()
        await wait_for(lambda: any(m.get("reason") == "unclear" for m in ws.json))
        assert "notice" not in ws.types()

    run(drive(session, ws, scenario))
