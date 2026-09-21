"""Realtime voice support: Groq Whisper STT, Azure Neural TTS, and the
session logic (turn-taking, barge-in, confirmations) that ties them to the
existing agent. See routers/voice_router.py for the WebSocket entrypoint."""
