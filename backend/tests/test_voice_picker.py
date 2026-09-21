"""Voice picker: the allowlist, the preview endpoint, and how a picked voice
interacts with a configured custom voice."""

import pytest

import app.routers.voice_router as voice_router
from app.config import get_settings
from app.voice.tts import AzureTTS, TTSError, pick_voice, voice_catalog


def _register(client, email="picker@example.com"):
    return client.post(
        "/auth/register", json={"email": email, "password": "pw123456", "full_name": "Picker"}
    ).json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def reset_cooldown():
    voice_router._last_sample_at.clear()
    yield
    voice_router._last_sample_at.clear()


class FakeTTS:
    configured = True
    fail = False
    calls = []

    def __init__(self, voice=None):
        self.voice = voice

    async def synthesize(self, text):
        FakeTTS.calls.append((self.voice, text))
        if FakeTTS.fail:
            raise TTSError("boom")
        return bytes(3200)


@pytest.fixture()
def fake_tts(monkeypatch):
    FakeTTS.calls, FakeTTS.fail, FakeTTS.configured = [], False, True
    monkeypatch.setattr(voice_router, "AzureTTS", FakeTTS)
    return FakeTTS


# ------------------------------------------------------------------ catalog / allowlist


def test_catalog_lists_the_default_first_without_duplicates(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "azure_tts_voice", "hi-IN-SwaraNeural")
    ids = [v["id"] for v in voice_catalog()]
    assert ids[0] == "hi-IN-SwaraNeural" and len(ids) == len(set(ids)) and len(ids) > 5


def test_only_catalog_voices_are_accepted():
    assert pick_voice("hi-IN-KavyaNeural") == "hi-IN-KavyaNeural"
    assert pick_voice("en-US-JennyNeural") is None  # a real Azure voice, but not offered
    assert pick_voice("'; drop table") is None
    assert pick_voice(None) is None and pick_voice("") is None


def test_configured_custom_voice_is_offered_and_labelled(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "azure_tts_voice", "AcmeBrandVoiceNeural")
    monkeypatch.setattr(s, "azure_tts_endpoint_id", "endpoint-123")
    first = voice_catalog()[0]
    assert first["id"] == "AcmeBrandVoiceNeural" and first["custom"] is True and "Custom voice" in first["label"]
    assert pick_voice("AcmeBrandVoiceNeural") == "AcmeBrandVoiceNeural"


# ------------------------------------------------------------------ picked voice vs custom endpoint


def test_a_picked_prebuilt_voice_never_uses_the_custom_endpoint(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "azure_tts_voice", "AcmeBrandVoiceNeural")
    monkeypatch.setattr(s, "azure_tts_endpoint_id", "endpoint-123")
    default = AzureTTS()
    picked = AzureTTS(voice="hi-IN-KavyaNeural")
    assert default.uses_custom_voice and default._voice_for("custom") == "AcmeBrandVoiceNeural"
    assert not picked.uses_custom_voice and picked._voice_for("prebuilt") == "hi-IN-KavyaNeural"
    assert picked._modes() == ["prebuilt", "prebuilt"]


def test_choosing_the_default_voice_explicitly_keeps_the_custom_endpoint(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "azure_tts_voice", "AcmeBrandVoiceNeural")
    monkeypatch.setattr(s, "azure_tts_endpoint_id", "endpoint-123")
    assert AzureTTS(voice="AcmeBrandVoiceNeural").uses_custom_voice


# ------------------------------------------------------------------ HTTP endpoints


def test_voice_endpoints_require_login(client):
    assert client.get("/voice/voices").status_code in (401, 403)
    assert client.get("/voice/sample?voice=hi-IN-SwaraNeural").status_code in (401, 403)


def test_list_voices(client):
    token = _register(client)
    body = client.get("/voice/voices", headers=_auth(token)).json()
    assert body["default"] and any(v["id"] == body["default"] for v in body["voices"])


def test_sample_returns_wav_for_the_chosen_voice(client, fake_tts):
    token = _register(client, "s1@example.com")
    resp = client.get("/voice/sample?voice=hi-IN-KavyaNeural", headers=_auth(token))
    assert resp.status_code == 200 and resp.headers["content-type"] == "audio/wav"
    assert resp.content[:4] == b"RIFF"
    assert fake_tts.calls[0][0] == "hi-IN-KavyaNeural"


def test_sample_rejects_unknown_voice_without_calling_azure(client, fake_tts):
    token = _register(client, "s2@example.com")
    resp = client.get("/voice/sample?voice=evil-Voice", headers=_auth(token))
    assert resp.status_code == 400 and fake_tts.calls == []


def test_sample_is_rate_limited_per_user(client, fake_tts):
    token = _register(client, "s3@example.com")
    assert client.get("/voice/sample?voice=hi-IN-SwaraNeural", headers=_auth(token)).status_code == 200
    assert client.get("/voice/sample?voice=hi-IN-SwaraNeural", headers=_auth(token)).status_code == 429
    assert len(fake_tts.calls) == 1


def test_sample_reports_clear_errors(client, fake_tts):
    token = _register(client, "s4@example.com")
    fake_tts.configured = False
    assert client.get("/voice/sample?voice=hi-IN-SwaraNeural", headers=_auth(token)).status_code == 503
    voice_router._last_sample_at.clear()
    fake_tts.configured, fake_tts.fail = True, True
    assert client.get("/voice/sample?voice=hi-IN-SwaraNeural", headers=_auth(token)).status_code == 502
