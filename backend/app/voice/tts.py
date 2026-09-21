"""
Azure Neural text-to-speech, including Custom Neural Voice.

Each sentence is synthesized as soon as the chunker releases it (see
text_utils.SentenceChunker), so playback starts after the first sentence
rather than after the whole answer. The Azure SDK is blocking, so calls run
in a worker thread; the SDK import is optional so the app (and the test
suite) still starts on machines without it.

Custom Neural Voice: set AZURE_TTS_ENDPOINT_ID to the deployment's endpoint
id and AZURE_TTS_VOICE to the custom voice's name. Custom deployments can
fail in ways prebuilt voices don't (suspended, quota, wrong id), so on any
failure we fall back to AZURE_TTS_FALLBACK_VOICE and stop retrying the custom
endpoint for a few minutes rather than paying the failure on every sentence.
"""

import asyncio
import re
import time
from xml.sax.saxutils import escape

from app.config import get_settings
from app.observability.logging_config import get_logger

logger = get_logger(__name__)

# 16 kHz, 16-bit mono PCM: trivial for the browser to play, no decoder needed.
SAMPLE_RATE = 16000
_CUSTOM_RETRY_AFTER_SECONDS = 300
_LOCALE_IN_NAME = re.compile(r"^([a-z]{2,3}-[A-Za-z]{2,4})-")


VOICE_LABELS = {
    "hi-IN-SwaraNeural": "Swara - Hindi, female",
    "hi-IN-KavyaNeural": "Kavya - Hindi, female",
    "hi-IN-AartiNeural": "Aarti - Hindi, female",
    "hi-IN-AnanyaNeural": "Ananya - Hindi, female",
    "hi-IN-MadhurNeural": "Madhur - Hindi, male",
    "hi-IN-AaravNeural": "Aarav - Hindi, male",
    "hi-IN-KunalNeural": "Kunal - Hindi, male",
    "en-IN-NeerjaIndicNeural": "Neerja - Hindi-English mix, female",
    "en-IN-AartiIndicNeural": "Aarti - Hindi-English mix, female",
    "en-IN-PrabhatIndicNeural": "Prabhat - Hindi-English mix, male",
    "en-IN-ArjunIndicNeural": "Arjun - Hindi-English mix, male",
}


def voice_catalog() -> list[dict]:
    """Voices the UI may offer. The configured voice comes first (and is the
    custom voice when AZURE_TTS_ENDPOINT_ID is set)."""
    s = get_settings()
    names = [s.azure_tts_voice] + [v.strip() for v in s.azure_tts_voices.split(",") if v.strip()]
    seen: set[str] = set()
    out = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        custom = bool(s.azure_tts_endpoint_id) and name == s.azure_tts_voice
        label = f"Custom voice - {name}" if custom else VOICE_LABELS.get(name, name)
        out.append({"id": name, "label": label, "custom": custom})
    return out


def pick_voice(requested: str | None) -> str | None:
    """A client-requested voice, if (and only if) it is in the catalog."""
    if requested and any(v["id"] == requested for v in voice_catalog()):
        return requested
    return None


class TTSError(Exception):
    """Synthesis failed after retry; the session falls back to captions."""


def resolve_locale(voice: str, override: str = "") -> str:
    """SSML needs a language tag. Prebuilt voice names carry one
    ('hi-IN-SwaraNeural'); a custom voice name usually doesn't, so use the
    configured locale, defaulting to hi-IN (this assistant speaks Hinglish)."""
    if override:
        return override
    m = _LOCALE_IN_NAME.match(voice)
    return m.group(1) if m else "hi-IN"


def build_ssml(text: str, voice: str, locale: str = "") -> str:
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{resolve_locale(voice, locale)}">'
        f'<voice name="{voice}">{escape(text)}</voice></speak>'
    )


class AzureTTS:
    def __init__(self, voice: str | None = None) -> None:
        """`voice`: a user-picked voice from the catalog (already validated by
        the caller). None = the configured default, which may be a custom voice."""
        self._settings = get_settings()
        self._voice = voice if voice and voice != self._settings.azure_tts_voice else None
        self._synths: dict[str, object] = {}  # one warm synthesizer per mode
        self._custom_broken_until = 0.0

    @property
    def configured(self) -> bool:
        s = self._settings
        if not (s.azure_speech_key and s.azure_speech_region):
            return False
        try:
            import azure.cognitiveservices.speech  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def uses_custom_voice(self) -> bool:
        # The custom endpoint only ever serves the configured voice; a voice the
        # user picked from the list is a prebuilt one and never uses it.
        return bool(self._settings.azure_tts_endpoint_id) and self._voice is None

    def _voice_for(self, mode: str) -> str:
        if mode == "fallback":
            return self._settings.azure_tts_fallback_voice
        return self._voice or self._settings.azure_tts_voice

    def _get_synth(self, mode: str):
        if mode not in self._synths:
            import azure.cognitiveservices.speech as speechsdk

            s = self._settings
            cfg = speechsdk.SpeechConfig(subscription=s.azure_speech_key, region=s.azure_speech_region)
            cfg.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm)
            cfg.speech_synthesis_voice_name = self._voice_for(mode)
            if mode == "custom":
                cfg.endpoint_id = s.azure_tts_endpoint_id
            # audio_config=None -> keep audio in memory instead of playing it on the server
            self._synths[mode] = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
        return self._synths[mode]

    def _synthesize_blocking(self, text: str, mode: str) -> bytes:
        import azure.cognitiveservices.speech as speechsdk

        ssml = build_ssml(text, self._voice_for(mode), self._settings.azure_tts_locale)
        result = self._get_synth(mode).speak_ssml_async(ssml).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            self._synths.pop(mode, None)  # rebuild the connection next time
            detail = getattr(getattr(result, "cancellation_details", None), "error_details", "")
            raise TTSError(f"{result.reason} {detail}".strip())
        return bytes(result.audio_data)

    def _modes(self) -> list[str]:
        if not self.uses_custom_voice:
            return ["prebuilt", "prebuilt"]  # same voice twice: one retry for transient errors
        if time.monotonic() < self._custom_broken_until:
            return ["fallback"]
        return ["custom", "fallback"]

    async def synthesize(self, text: str) -> bytes:
        """Returns raw 16 kHz mono PCM. Serialized per session by the caller."""
        if not self.configured:
            raise TTSError("Azure Speech is not configured")
        last: Exception | None = None
        for mode in self._modes():
            try:
                return await asyncio.to_thread(self._synthesize_blocking, text, mode)
            except Exception as exc:  # SDK raises assorted types; all mean "try the next option"
                last = exc
                logger.warning("azure_tts_failed mode=%s error=%s", mode, exc)
                if mode == "custom":
                    self._custom_broken_until = time.monotonic() + _CUSTOM_RETRY_AFTER_SECONDS
        raise TTSError(str(last))
