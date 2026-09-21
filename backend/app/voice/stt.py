"""
Groq Whisper speech-to-text.

Groq's transcription endpoint is OpenAI-compatible but *not* streaming: it
transcribes a finished clip. The browser's VAD decides where an utterance
ends and sends it whole, so this module's job is: wrap the PCM as WAV, call
Groq (with one retry), and -- most importantly -- refuse to pass Whisper's
well-known silence hallucinations on to the agent.
"""

import asyncio
import io
import wave
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.observability.logging_config import get_logger
from app.voice.phrases import phrase
from app.voice.text_utils import HALLUCINATION_PHRASES, is_looping_garbage

logger = get_logger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
SAMPLE_RATE = 16000


class STTError(Exception):
    """Transcription failed after retry (network, 429/5xx, bad key)."""


@dataclass
class Transcript:
    text: str  # empty string means "nothing usable was said"
    language: str | None = None
    dropped_reason: str | None = None  # why an otherwise non-empty result was discarded
    # Mean Whisper log-probability across segments (0 = certain, more negative = guessing).
    # Typical clear speech is around -0.2 to -0.5; below about -1 is mostly guesswork.
    confidence: float | None = None


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def split_pcm(pcm: bytes, max_seconds: float) -> list[bytes]:
    """Long monologues are transcribed in pieces (cut on sample boundaries)."""
    step = int(max_seconds * SAMPLE_RATE) * 2
    return [pcm[i : i + step] for i in range(0, len(pcm), step)] or [b""]


def pcm_rms(pcm: bytes) -> float:
    """Root-mean-square amplitude in 0..1 -- cheap 'was that actually loud
    enough to be speech' check before spending a network call."""
    if len(pcm) < 2:
        return 0.0
    import array

    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    return (sum(x * x for x in samples) / len(samples)) ** 0.5 / 32768.0


def judge_result(data: dict) -> Transcript:
    """Apply the anti-hallucination rules to a Groq verbose_json response."""
    text = (data.get("text") or "").strip()
    language = data.get("language")
    if not text:
        return Transcript("", language, "empty")

    segments = data.get("segments") or []
    no_speech = max((s.get("no_speech_prob", 0.0) for s in segments), default=0.0)
    logprob = min((s.get("avg_logprob", 0.0) for s in segments), default=0.0)

    # Whisper's own reference heuristic for "this segment is silence".
    if no_speech > 0.6 and logprob < -1.0:
        return Transcript("", language, "no_speech")
    normalized = "".join(c for c in text.lower() if c.isalnum() or c == " ").strip()
    if normalized in HALLUCINATION_PHRASES and (no_speech > 0.4 or logprob < -1.0):
        return Transcript("", language, "hallucination")
    if is_looping_garbage(text):
        return Transcript("", language, "looping")
    scores = [s["avg_logprob"] for s in segments if "avg_logprob" in s]
    confidence = sum(scores) / len(scores) if scores else None
    return Transcript(text, language, None, confidence)


class GroqTranscriber:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._settings = get_settings()
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

    @property
    def configured(self) -> bool:
        return bool(self._settings.groq_api_key)

    async def close(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self, pcm: bytes, language: str | None = None, vocabulary: list[str] | None = None
    ) -> Transcript:
        if not self.configured:
            raise STTError("GROQ_API_KEY is not set")
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return await self._call(pcm, language, vocabulary)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status < 500 and status != 429:
                    break  # 4xx other than 429 won't get better on retry (bad key, bad audio)
                logger.warning("groq_stt_retry attempt=%s error=%s", attempt, exc)
                await asyncio.sleep(0.4)
        raise STTError(str(last_error))

    async def _call(self, pcm: bytes, language: str | None, vocabulary: list[str] | None = None) -> Transcript:
        data = {
            "model": self._settings.groq_stt_model,
            "response_format": "verbose_json",
            "temperature": "0",
            # Vocabulary + script hint. Written in Roman Hinglish so Whisper
            # tends to answer in Roman script too (the agent understands either).
            "prompt": phrase("stt_hint") + (" " + ", ".join(vocabulary) + "." if vocabulary else ""),
        }
        language = language or self._settings.groq_stt_language
        if language:
            data["language"] = language
        resp = await self._client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self._settings.groq_api_key}"},
            files={"file": ("utterance.wav", pcm_to_wav(pcm), "audio/wav")},
            data=data,
        )
        resp.raise_for_status()
        return judge_result(resp.json())
