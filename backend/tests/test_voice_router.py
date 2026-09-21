"""WebSocket /voice: pre-session behavior (auth, capacity, single call per
user) and the Groq client's retry rules."""

import asyncio

import httpx
import pytest

import app.routers.voice_router as voice_router
from app.config import get_settings
from app.voice.stt import GroqTranscriber, STTError


def _register(client, email="voice@example.com"):
    return client.post(
        "/auth/register", json={"email": email, "password": "pw123456", "full_name": "Voice User"}
    ).json()["access_token"]


def test_rejects_connection_with_bad_token(client):
    with client.websocket_connect("/voice") as ws:
        ws.send_json({"type": "auth", "token": "not-a-jwt"})
        assert ws.receive_json()["code"] == "unauthorized"


def test_rejects_first_message_that_is_not_auth(client):
    with client.websocket_connect("/voice") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["code"] == "unauthorized"


def test_without_groq_key_reports_stt_unavailable(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "groq_api_key", "")
    token = _register(client)
    with client.websocket_connect("/voice") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["code"] == "stt_unavailable"


def test_capacity_limit_rejects_new_user(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "voice_max_concurrent_sessions", 0)
    token = _register(client, "cap@example.com")
    with client.websocket_connect("/voice") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["code"] == "busy"


def test_ready_message_when_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "groq_api_key", "test")
    monkeypatch.setattr(get_settings(), "azure_speech_key", "")
    token = _register(client, "ready@example.com")
    with client.websocket_connect("/voice") as ws:
        ws.send_json({"type": "auth", "token": token})
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["stt"] is True and ready["tts"] is False
        assert ws.receive_json()["type"] == "tts_unavailable"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    assert not voice_router._active_sessions  # cleaned up on disconnect


# ------------------------------------------------------------------ Groq client


def _transcriber(handler, monkeypatch):
    monkeypatch.setattr(get_settings(), "groq_api_key", "test")
    return GroqTranscriber(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_groq_retries_once_on_429_then_succeeds(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"text": "hello", "segments": [{"no_speech_prob": 0.0, "avg_logprob": -0.1}]})

    result = asyncio.run(_transcriber(handler, monkeypatch).transcribe(bytes(32000)))
    assert result.text == "hello" and len(calls) == 2


def test_groq_does_not_retry_bad_key(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    with pytest.raises(STTError):
        asyncio.run(_transcriber(handler, monkeypatch).transcribe(bytes(32000)))
    assert len(calls) == 1


def test_groq_gives_up_after_second_failure(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(STTError):
        asyncio.run(_transcriber(handler, monkeypatch).transcribe(bytes(32000)))
    assert len(calls) == 2
