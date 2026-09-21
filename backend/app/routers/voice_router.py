"""
WebSocket entrypoint for realtime voice. All the behavior lives in
voice/session.py; this file only does what has to happen *before* a session
exists: authenticate, enforce capacity limits, and make sure a user has at
most one live call.
"""

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import decode_access_token, token_expiry
from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.observability.logging_config import get_logger
from app.observability.metrics import voice_events_total
from app.voice.session import VoiceSession
from app.voice.phrases import phrase
from app.voice.stt import GroqTranscriber, pcm_to_wav
from app.voice.tts import AzureTTS, TTSError, pick_voice, voice_catalog

router = APIRouter(tags=["voice"])
logger = get_logger(__name__)

_AUTH_TIMEOUT_SECONDS = 10
_active_sessions: dict[str, VoiceSession] = {}
_last_sample_at: dict[str, float] = {}
_SAMPLE_COOLDOWN_SECONDS = 2.0  # each preview costs a TTS call; don't let a button be hammered


@router.get("/voice/voices")
def list_voices(current_user: User = Depends(get_current_user)):
    """Voices the UI may offer, and which one is the default."""
    return {"default": get_settings().azure_tts_voice, "voices": voice_catalog()}


@router.get("/voice/sample")
async def voice_sample(voice: str, current_user: User = Depends(get_current_user)):
    """A short spoken sample so a voice can be auditioned without starting a call."""
    if pick_voice(voice) is None:
        raise HTTPException(status_code=400, detail="Unknown voice")
    now = time.monotonic()
    if now - _last_sample_at.get(current_user.id, 0.0) < _SAMPLE_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail="Please wait a moment between previews")
    _last_sample_at[current_user.id] = now
    tts = AzureTTS(voice=voice)
    if not tts.configured:
        raise HTTPException(status_code=503, detail="Text-to-speech is not configured")
    try:
        pcm = await tts.synthesize(phrase("voice_sample"))
    except TTSError as exc:
        logger.warning("voice_sample_failed voice=%s error=%s", voice, exc)
        raise HTTPException(status_code=502, detail="Could not synthesize that voice") from exc
    return Response(content=pcm_to_wav(pcm), media_type="audio/wav")


@router.websocket("/voice")
async def voice_socket(ws: WebSocket, db: Session = Depends(get_db)):
    await ws.accept()

    # Browsers can't set an Authorization header on a WebSocket, and putting
    # the JWT in the URL would leak it into logs -- so the token is the
    # first message instead.
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=_AUTH_TIMEOUT_SECONDS)
    except Exception:
        await ws.close(code=4401, reason="auth required")
        return
    token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
    user_id = decode_access_token(token) if token else None
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    if user is None:
        await ws.send_json({"type": "error", "code": "unauthorized", "message": "Invalid or expired token", "recoverable": False})
        await ws.close(code=4401, reason="unauthorized")
        return

    if len(_active_sessions) >= get_settings().voice_max_concurrent_sessions and user.id not in _active_sessions:
        voice_events_total.labels(event="capacity_rejected").inc()
        await ws.send_json({"type": "error", "code": "busy", "message": "Voice is busy right now. Try again shortly or use text chat.", "recoverable": False})
        await ws.close(code=4429, reason="capacity")
        return

    # One live call per user: a second tab or a reconnect replaces the first.
    previous = _active_sessions.get(user.id)
    if previous is not None:
        await previous.send({"type": "error", "code": "replaced", "message": "Call moved to another window.", "recoverable": False})
        await previous.close(4409, "replaced")

    session = VoiceSession(
        ws,
        db,
        user,
        token_expires_at=token_expiry(token),
        conversation_id=first.get("conversation_id"),
        stt=GroqTranscriber(),
        tts=AzureTTS(voice=pick_voice(first.get("voice"))),  # only voices from the catalog are honoured
    )
    _active_sessions[user.id] = session
    try:
        await session.run()
    finally:
        if _active_sessions.get(user.id) is session:
            del _active_sessions[user.id]
        await session.cleanup()
