"""
One realtime voice connection: turn-taking, cancellation, and the
degrade-gracefully rules.

Wire protocol (WebSocket /voice)
--------------------------------
client -> server
    JSON   {"type":"auth","token":"<jwt>","conversation_id":"<optional>","voice":"<optional>"}  (first message)
    JSON   {"type":"interrupt"}     the Stop button: silence the agent now, no questions asked
    JSON   {"type":"ping"}
    BINARY one complete utterance: 16 kHz mono signed-16-bit little-endian PCM
           (the browser's VAD decides where an utterance starts and ends). Speaking over the
           agent works as "duck, then confirm": the browser PAUSES the agent's audio when it hears
           you and sends the utterance; this file transcribes it and answers with a verdict --
             transcript  -> a real request: the browser discards the agent's old audio
             ignored     -> echo / "hmm" / noise / unclear: the browser resumes the agent

server -> client
    JSON   ready{conversation_id,sample_rate,stt,tts}
           transcript{text,turn_id,merged,low_confidence}
           turn_start{turn_id} | token{content,turn_id} | tool_call{name,args,turn_id}
           status{text,turn_id}   ("checking the knowledge base...")
           speaking_end{turn_id} | done{...} | interrupted{turn_id,cancelled} | notice{text}
           ignored{reason: echo|backchannel|unclear|queued|too_short_or_quiet}
           hangup{}               (the user said goodbye; end the call after the farewell plays)
           error{code,message,recoverable} | tts_unavailable | pong
    BINARY audio frame: 4-byte big-endian turn_id, 4-byte seq, then PCM16 @16 kHz.
           The client drops frames whose turn_id isn't its current turn, which
           is what makes late audio after an interrupt harmless.

Design rules this file enforces (each one is an edge case from the design):
  * exactly one active agent turn per connection; a new utterance cancels the old one
  * a turn that has reached a refund/cancel tool is never cancelled mid-write:
    an interrupt only mutes audio, and the next utterance waits its turn
  * STT failure -> spoken reprompt (capped) -> then "switch to text";
    TTS failure -> captions only, the conversation continues
  * interrupted answers are persisted as only what was actually spoken
"""

import asyncio
import json
import struct
import time
from collections import deque
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage
from sqlalchemy.orm import Session

from app.agent.graph import build_agent_graph
from app.agent.prompts import get_system_prompt
from app.agent.turn import stream_turn
from app.cache.redis_client import append_message, pop_last_message
from app.config import get_settings
from app.models import Conversation, Message, MessageRole, Order, User
from app.observability.logging_config import get_logger, log_event
from app.observability.metrics import (
    chat_turn_latency_seconds,
    rag_retrievals_total,
    tool_calls_total,
    voice_events_total,
    voice_first_audio_seconds,
    voice_stt_latency_seconds,
)
from app.routers.chat_router import _load_history_as_messages
from app.tools.mcp_client import invalidate_shared_session, shared_mcp_tools_session
from app.voice.confirm import WRITE_TOOLS, ConfirmationGate
from app.voice.phrases import phrase, tool_status
from app.voice.stt import SAMPLE_RATE, STTError, pcm_rms, split_pcm
from app.voice.text_utils import (
    SentenceChunker,
    is_backchannel,
    is_goodbye,
    correct_transcript,
    is_repeat_request,
    looks_like_echo,
    normalize_spoken,
    redact_card_numbers,
    speakable,
)
from app.voice.tts import TTSError

logger = get_logger(__name__)

_MIN_RMS = 0.003  # below this an "utterance" is room noise, not speech
_AUDIO_FRAME_BYTES = 16000  # 0.5 s of audio per WebSocket frame
_TURN_TIMEOUT_SECONDS = 60
_HEARTBEAT_TIMEOUT_SECONDS = 45  # client pings every ~20 s; silence this long = dead link
_RECV_POLL_SECONDS = 15
_IDLE_CLOSE_GRACE_SECONDS = 60
_LOW_CONFIDENCE = -1.1  # below this Whisper is mostly guessing: ask again instead of acting on it
_UNCERTAIN_CONFIDENCE = -0.75  # between the two: act on it, but tell the UI it may be misheard
_MERGE_WINDOW_SECONDS = 6.0  # a follow-up this soon, before the agent starts answering, joins the request
_AGENT_TAIL_SECONDS = 0.8  # room echo/reverb lingers a little after our audio ends
_ECHO_WINDOW_SECONDS = 1.5  # room echo only lingers about a second: after that, similar words are the user's own answer
_ECHO_MEMORY_SECONDS = 30.0


class _Status(str):
    """A system line (status/notice), not part of the agent's answer: it is
    spoken but never stored as something the agent said."""


@dataclass
class _TurnState:
    turn_id: int
    muted: bool = False  # audio suppressed (interrupt during a protected write); captions continue
    spoken: list[str] = field(default_factory=list)  # sentences whose audio was fully sent
    status_said: set[str] = field(default_factory=set)  # 'checking...' lines already shown this turn


class VoiceSession:
    def __init__(
        self,
        ws: WebSocket,
        db: Session,
        user: User,
        token_expires_at: datetime | None,
        conversation_id: str | None,
        stt,
        tts,
        *,
        tools_session=shared_mcp_tools_session,
        graph_builder=build_agent_graph,
    ) -> None:
        self.ws, self.db, self.user = ws, db, user
        self.token_expires_at = token_expires_at
        self.stt, self.tts = stt, tts
        self._tools_session = tools_session
        self._graph_builder = graph_builder
        self.settings = get_settings()

        self.conversation: Conversation | None = None
        if conversation_id:
            self.conversation = (
                db.query(Conversation)
                .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
                .first()
            )  # unknown/foreign id -> silently start a fresh conversation

        self.gate = ConfirmationGate()
        self.closing = False
        self.tts_ok = tts.configured
        self.turn_id = 0
        self.task: asyncio.Task | None = None
        self._ts = _TurnState(0)
        self._write_window = False
        self._queued_text: str | None = None  # what the user said while a refund/cancel was running
        self._queued_at = 0.0
        self._vocab: list[str] | None = None  # this user's product names, to help speech recognition
        self._turn_uncertain = False
        self._intake_tail: asyncio.Task | None = None
        self._speaking_until = 0.0  # monotonic time our audio should finish playing (an estimate)
        self._recent_spoken: deque[tuple[float, str]] = deque(maxlen=12)
        self._last_user_msg: Message | None = None  # the current request, until the agent starts answering
        self._turn_user_text = ""
        self._turn_started = 0.0
        self._answer_started = False
        self._stt_failures = 0
        self._last_spoken = ""
        self._tools = None
        self._tools_lock = asyncio.Lock()
        self._warm_task: asyncio.Task | None = None
        self._stack = AsyncExitStack()
        self._started = time.monotonic()
        self._last_activity = time.monotonic()
        self._idle_prompted = False

    # ------------------------------------------------------------------ io

    async def send(self, payload: dict) -> bool:
        if self.closing:
            return False
        try:
            await self.ws.send_json(payload)
            return True
        except (RuntimeError, WebSocketDisconnect):
            self.closing = True
            return False

    async def _send_audio(self, turn_id: int, seq: int, pcm: bytes) -> bool:
        if self.closing:
            return False
        try:
            await self.ws.send_bytes(struct.pack(">II", turn_id, seq) + pcm)
            return True
        except (RuntimeError, WebSocketDisconnect):
            self.closing = True
            return False

    async def close(self, code: int, reason: str) -> None:
        if self.closing:
            return
        self.closing = True
        try:
            await self.ws.close(code=code, reason=reason)
        except Exception:  # already closed by the peer
            pass

    async def _error(self, code: str, message: str, recoverable: bool = True) -> None:
        await self.send({"type": "error", "code": code, "message": message, "recoverable": recoverable})

    # ---------------------------------------------------------- main loop

    async def run(self) -> None:
        if not self.stt.configured:
            await self._error("stt_unavailable", "Voice input isn't configured on this server. Use text chat.", False)
            await self.close(4503, "stt unavailable")
            return
        await self.send(
            {
                "type": "ready",
                "conversation_id": self.conversation.id if self.conversation else None,
                "sample_rate": SAMPLE_RATE,
                "stt": True,
                "tts": self.tts_ok,
            }
        )
        if not self.tts_ok:
            await self.send({"type": "tts_unavailable", "message": "Spoken replies are off; showing captions only."})
        self._warm_task = asyncio.create_task(self._warm())

        last_message = time.monotonic()
        while not self.closing:
            try:
                msg = await asyncio.wait_for(self.ws.receive(), timeout=_RECV_POLL_SECONDS)
                last_message = time.monotonic()
            except asyncio.TimeoutError:
                if time.monotonic() - last_message > _HEARTBEAT_TIMEOUT_SECONDS:
                    voice_events_total.labels(event="heartbeat_timeout").inc()
                    await self.close(1001, "heartbeat timeout")
                    break
                await self._check_timers()
                continue
            except (RuntimeError, WebSocketDisconnect):
                break

            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await self._on_utterance(msg["bytes"])
            elif msg.get("text") is not None:
                await self._on_control(msg["text"])
            await self._check_timers()

    async def _on_control(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            return
        kind = data.get("type")
        if kind == "ping":
            await self.send({"type": "pong"})
        elif kind == "interrupt":
            await self._on_interrupt()

    async def _check_timers(self) -> None:
        now = time.monotonic()
        if now - self._started > self.settings.voice_max_session_seconds:
            await self._error("session_limit", "Maximum call length reached.", False)
            await self.close(4408, "session limit")
        elif self.task is None or self.task.done():
            idle = now - self._last_activity
            if idle > self.settings.voice_idle_seconds + _IDLE_CLOSE_GRACE_SECONDS:
                await self.close(4408, "idle timeout")
            elif idle > self.settings.voice_idle_seconds and not self._idle_prompted:
                self._idle_prompted = True
                self._launch(self._say_task(phrase("idle")))

    # -------------------------------------------------------- turn control

    def _token_expired(self) -> bool:
        return self.token_expires_at is not None and datetime.now(timezone.utc) >= self.token_expires_at

    async def _on_utterance(self, pcm: bytes) -> None:
        self._last_activity = time.monotonic()
        self._idle_prompted = False
        if self._token_expired():
            await self._error("token_expired", "Your session expired. Please sign in again.", False)
            await self.close(4401, "token expired")
            return
        # Never block the receive loop on transcription: an interrupt sent a
        # moment later must be handled right away. Utterances are still
        # processed strictly in the order they arrived.
        previous = self._intake_tail
        self._intake_tail = asyncio.create_task(self._intake(pcm, time.monotonic(), previous))

    async def _intake(self, pcm: bytes, received_at: float, previous: asyncio.Task | None) -> None:
        """Transcribe first, decide second.

        The running turn is left alone until we know what was actually said, so
        the agent's own voice leaking back through the microphone, a cough or a
        "hmm" can no longer cut off its answer.
        """
        if previous is not None:
            await asyncio.wait({previous})  # keep arrival order; doesn't raise previous's errors
        try:
            heard = await self._transcribe(pcm)
            if heard is None:
                return
            text, warning, uncertain = heard
            if self._agent_busy() and is_backchannel(text):
                voice_events_total.labels(event="backchannel_ignored").inc()
                await self.send({"type": "ignored", "reason": "backchannel"})
                return
            if self._is_echo(text):
                voice_events_total.labels(event="echo_ignored").inc()
                await self.send({"type": "ignored", "reason": "echo"})
                return
            await self._start_turn(text, received_at, warning, uncertain)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("voice_intake_failed")
            await self._error("turn_failed", "Something went wrong with that request.")

    def _vocabulary(self) -> list[str]:
        """This user's product names. Whisper mishears unfamiliar names ("Graphics
        Tablet Pro"); handing it the words it should expect helps a lot."""
        if self._vocab is None:
            try:
                rows = self.db.query(Order.product_name).filter(Order.user_id == self.user.id).distinct().limit(8).all()
                self._vocab = [name for (name,) in rows if name]
            except Exception:
                self.db.rollback()
                self._vocab = []
        return self._vocab

    def _agent_audible(self) -> bool:
        """Audio we sent may still be coming out of the user's speakers."""
        return time.monotonic() < self._speaking_until + _AGENT_TAIL_SECONDS

    def _agent_busy(self) -> bool:
        return self._agent_audible() or bool(self.task and not self.task.done())

    def _is_echo(self, text: str) -> bool:
        now = time.monotonic()
        if now > self._speaking_until + _ECHO_WINDOW_SECONDS:
            return False
        recent = [sentence for when, sentence in self._recent_spoken if now - when < _ECHO_MEMORY_SECONDS]
        return looks_like_echo(text, recent)

    def _note_audio_sent(self, nbytes: int) -> None:
        self._speaking_until = max(self._speaking_until, time.monotonic()) + nbytes / (2 * SAMPLE_RATE)

    async def _start_turn(self, text: str, received_at: float, warning: str | None, uncertain: bool = False) -> None:
        active = bool(self.task and not self.task.done())
        if active and self._write_window:
            # A refund/cancel is in progress: never cancel it half-way. Keep
            # what the user said (joined, if they said several things) and
            # run it as soon as the write has finished.
            self._queued_text = f"{self._queued_text} {text}".strip() if self._queued_text else text
            self._queued_at = received_at
            self._ts.muted = True
            voice_events_total.labels(event="utterance_queued_during_write").inc()
            # The client may have paused the agent's audio when it heard you: tell it there's
            # nothing to wait for (the agent is muted until the write finishes anyway).
            await self.send({"type": "ignored", "reason": "queued"})
            return

        merged = False
        if active and self._can_merge(received_at):
            # The user paused mid-sentence ("I want a refund ... for my tablet")
            # and the pause was read as the end of the utterance. Answer the
            # whole thing instead of the two halves separately.
            first_half = self._last_user_msg  # captured now: cancelling the turn clears self._last_user_msg
            text = f"{self._turn_user_text} {text}".strip()
            uncertain = uncertain or self._turn_uncertain
            merged = True
            voice_events_total.labels(event="utterance_merged").inc()
            await self._cancel_task()
            self._retract_message(first_half)
        elif active:
            await self._cancel_task()
        self._launch(self._turn_task(text, received_at, warning, merged, uncertain))

    def _can_merge(self, received_at: float) -> bool:
        return (
            self._last_user_msg is not None
            and not self._answer_started
            and received_at - self._turn_started < _MERGE_WINDOW_SECONDS
        )

    def _retract_message(self, msg: Message | None) -> None:
        """Undo the stored first half of a request that is being merged."""
        if msg is None:
            return
        try:
            self.db.delete(msg)
            self.db.commit()
            pop_last_message(msg.conversation_id, "user", msg.content)
        except Exception:
            self.db.rollback()
            logger.warning("voice_retract_failed", exc_info=True)

    async def _on_interrupt(self) -> None:
        voice_events_total.labels(event="barge_in").inc()
        self._speaking_until = 0.0  # the client flushed its playback buffer
        if not (self.task and not self.task.done()):
            return
        cancelled = not self._write_window
        if cancelled:
            await self._cancel_task()
        else:
            self._ts.muted = True  # stop talking, but let the write finish
        # `cancelled` tells the UI whether a `done` will still follow.
        await self.send({"type": "interrupted", "turn_id": self._ts.turn_id, "cancelled": cancelled})

    async def _cancel_task(self) -> None:
        task = self.task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def _launch(self, coro) -> None:
        self.task = asyncio.create_task(coro)

    async def _turn_task(
        self, text: str, received_at: float, warning: str | None, merged: bool, uncertain: bool = False
    ) -> None:
        self.turn_id += 1
        self._ts = _TurnState(self.turn_id)
        self._answer_started = False
        self._last_user_msg = None
        try:
            if warning:
                await self._say(warning, self._ts)
            await self._agent_turn(text, self._ts, received_at, merged, uncertain)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("voice_turn_failed")
            voice_events_total.labels(event="turn_error").inc()
            await self._reset_tools()
            await self._error("turn_failed", "Something went wrong with that request.")
            await self._say(phrase("apology"), self._ts)
        finally:
            self._write_window = False
            self._last_user_msg = None
        queued, self._queued_text = self._queued_text, None
        if queued and not self.closing:
            self._launch(self._turn_task(queued, self._queued_at, None, False))

    async def _say_task(self, text: str) -> None:
        self.turn_id += 1
        self._ts = _TurnState(self.turn_id)
        await self._say(text, self._ts)

    # ----------------------------------------------------- one utterance

    async def _transcribe(self, pcm: bytes) -> tuple[str, str | None, bool] | None:
        """Speech -> cleaned text, plus an optional spoken warning. None when
        there is nothing to act on (noise, failure, empty)."""
        s = self.settings
        duration_ms = len(pcm) / 2 / SAMPLE_RATE * 1000
        if duration_ms < s.voice_min_utterance_ms or pcm_rms(pcm) < _MIN_RMS:
            voice_events_total.labels(event="dropped_noise").inc()
            await self.send({"type": "ignored", "reason": "too_short_or_quiet"})
            return None

        started = time.monotonic()
        try:
            parts = []
            confidences = []
            vocabulary = self._vocabulary()
            for chunk in split_pcm(pcm, s.voice_max_utterance_seconds):
                result = await self.stt.transcribe(chunk, vocabulary=vocabulary)
                if result.text:
                    parts.append(result.text)
                    if result.confidence is not None:
                        confidences.append(result.confidence)
            text = " ".join(parts).strip()
        except STTError as exc:
            logger.warning("stt_failed error=%s", exc)
            voice_events_total.labels(event="stt_failure").inc()
            await self._stt_gave_nothing()
            return None
        voice_stt_latency_seconds.observe(time.monotonic() - started)

        if not text:
            voice_events_total.labels(event="stt_empty").inc()
            await self._stt_gave_nothing()
            return None

        confidence = min(confidences) if confidences else None
        if confidence is not None and confidence < _LOW_CONFIDENCE:
            # Whisper was mostly guessing. Acting on a guess ("refund" vs "rewind") is worse
            # than asking the user to repeat.
            voice_events_total.labels(event="stt_low_confidence").inc()
            await self._stt_gave_nothing()
            return None
        uncertain = confidence is not None and confidence < _UNCERTAIN_CONFIDENCE
        self._stt_failures = 0
        text = correct_transcript(normalize_spoken(text))
        text, had_card = redact_card_numbers(text)
        warning = None
        if had_card:
            voice_events_total.labels(event="card_number_redacted").inc()
            warning = phrase("card_warning")
            if text.replace("[card number removed]", "").strip(" .,") == "":
                if not self._agent_busy():
                    self._launch(self._say_task(warning))
                return None
        return text, warning, uncertain

    async def _stt_gave_nothing(self) -> None:
        if self._agent_busy():
            # Almost certainly room noise or echo under the agent's own voice:
            # stay quiet rather than talk over it with "sorry, say again" -- but tell the
            # client, which may have paused the agent's audio, that it can carry on.
            await self.send({"type": "ignored", "reason": "unclear"})
            return
        self._stt_failures += 1
        if self._stt_failures >= self.settings.voice_max_stt_failures:
            self._stt_failures = 0
            await self._error("stt_failed", "Voice input keeps failing. Please type instead.", False)
            self._launch(self._say_task(phrase("give_up")))
        else:
            self._launch(self._say_task(phrase("reprompt")))

    async def _agent_turn(
        self, text: str, ts: _TurnState, t_end: float, merged: bool = False, uncertain: bool = False
    ) -> None:
        self._turn_uncertain = uncertain
        await self.send(
            {"type": "transcript", "text": text, "turn_id": ts.turn_id, "merged": merged, "low_confidence": uncertain}
        )
        self.gate.on_user_utterance(text)

        if is_repeat_request(text) and self._last_spoken:
            await self._say(self._last_spoken, ts)
            return

        conversation = self._ensure_conversation(text)
        history = _load_history_as_messages(self.db, conversation)
        user_message = Message(conversation_id=conversation.id, role=MessageRole.user, content=text)
        self.db.add(user_message)
        self.db.commit()
        append_message(conversation.id, "user", text)
        # Remembered so a follow-up fragment that arrives before the agent starts
        # answering can be merged into this request instead of replacing it.
        self._last_user_msg, self._turn_user_text, self._turn_started = user_message, text, t_end

        try:
            await self._ensure_tools()
        except Exception:
            logger.exception("voice_tools_unavailable")
            await self._error("tools_unavailable", "I can't reach the order system right now.")
            await self._say(phrase("tools_down"), ts)
            self._tools = []

        graph = self._graph_builder(self._tools, system_prompt=get_system_prompt(voice=True), tool_gate=self.gate)
        state = {"messages": [*history, HumanMessage(content=text)], "sources": [], "guardrail_flags": []}

        await self.send({"type": "turn_start", "turn_id": ts.turn_id})
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak_loop(queue, ts, t_end))
        chunker = SentenceChunker()
        full_answer = ""
        sources: list[dict] = []
        tool_calls: list[dict] = []
        flags: list[str] = []
        cancelled = False
        turn_start = time.monotonic()
        spoken_chars = 0
        capped = False

        def enqueue(sentence: str) -> None:
            """Queue a sentence for speech, but never talk for minutes: past the
            cap the rest stays on screen and we say so once."""
            nonlocal spoken_chars, capped
            if capped:
                return
            limit = self.settings.voice_max_spoken_chars
            if limit and spoken_chars and spoken_chars + len(sentence) > limit:
                capped = True
                queue.put_nowait(_Status(phrase("more_in_chat")))
                return
            spoken_chars += len(sentence)
            queue.put_nowait(sentence)

        try:
            async with asyncio.timeout(_TURN_TIMEOUT_SECONDS):
                async for event in stream_turn(graph, state):
                    if event.kind == "token":
                        if not full_answer:
                            self._answer_started = True
                            self._last_user_msg = None  # too late to merge a follow-up into this request
                        full_answer += event.text
                        await self.send({"type": "token", "content": event.text, "turn_id": ts.turn_id})
                        for sentence in chunker.feed(event.text):
                            enqueue(sentence)
                    elif event.kind == "tool_start":
                        tool_calls.append(event.tool)
                        tool_calls_total.labels(tool_name=event.tool["name"]).inc()
                        if event.tool["name"] == "search_knowledge_base":
                            rag_retrievals_total.inc()
                        if event.tool["name"] in WRITE_TOOLS:
                            self._write_window = True
                        status = tool_status(event.tool["name"])
                        if status not in ts.status_said and len(ts.status_said) < 2:
                            ts.status_said.add(status)
                            # Shown on screen in every case; spoken only if the
                            # model hasn't started talking, so we never talk over it.
                            await self.send({"type": "status", "text": status, "turn_id": ts.turn_id})
                            if not full_answer:
                                queue.put_nowait(_Status(status))
                        await self.send({"type": "tool_call", **event.tool, "turn_id": ts.turn_id})
                    elif event.kind == "tool_output":
                        self._write_window = False
                        sources.extend(event.sources)
                        flags.extend(event.flags)
            for sentence in chunker.flush():
                enqueue(sentence)
            queue.put_nowait(None)
            await speaker
        except asyncio.CancelledError:
            cancelled = True
            speaker.cancel()
            raise
        except TimeoutError:
            speaker.cancel()
            voice_events_total.labels(event="turn_timeout").inc()
            await self._say(phrase("timeout"), ts)
        finally:
            spoken_text = " ".join(ts.spoken)
            # If the user cut us off, history should record what they actually
            # heard, not the rest of a sentence they never listened to. With
            # TTS off there is no "heard" -- they read the captions instead.
            content = spoken_text if (cancelled and self.tts_ok) else full_answer
            if content.strip():
                self._persist_assistant(conversation, content, sources, tool_calls, flags)
                self._last_spoken = content
            if not cancelled:
                elapsed = time.monotonic() - turn_start
                chat_turn_latency_seconds.observe(elapsed)
                log_event(
                    logger,
                    "chat_turn",
                    channel="voice",
                    conversation_id=conversation.id,
                    latency_ms=int(elapsed * 1000),
                    tool_calls=[t["name"] for t in tool_calls],
                    guardrail_flags=flags,
                    answer_length=len(content),
                )

        await self.send(
            {
                "type": "done",
                "turn_id": ts.turn_id,
                "conversation_id": conversation.id,
                "sources": sources,
                "tool_calls": tool_calls,
                "guardrail_flags": flags,
            }
        )
        if is_goodbye(text):
            # The reply above is the farewell; the client hangs up once it has played.
            await self.send({"type": "hangup"})

    # ------------------------------------------------------------ helpers

    def _ensure_conversation(self, first_message: str) -> Conversation:
        if self.conversation is None:
            self.conversation = Conversation(user_id=self.user.id, title=first_message[:60])
            self.db.add(self.conversation)
            self.db.commit()
            self.db.refresh(self.conversation)
        return self.conversation

    async def _ensure_tools(self) -> None:
        # One MCP subprocess per connection (not per turn): spawning it costs
        # noticeable latency, and on a voice call every second is felt.
        async with self._tools_lock:
            if self._tools is None:
                self._tools = await self._stack.enter_async_context(self._tools_session(self.user.id))

    async def _warm(self) -> None:
        """Best-effort: open the tools connection while the user is still
        deciding what to say, so the first question doesn't pay for it."""
        try:
            await self._ensure_tools()
        except Exception:
            logger.warning("voice_warmup_failed", exc_info=True)  # the first turn will retry and report properly

    async def _reset_tools(self) -> None:
        """A turn failed: the pooled MCP subprocess may be wedged. Drop it so
        the next turn borrows a fresh one instead of failing the same way."""
        self._tools = None
        try:
            await invalidate_shared_session(self.user.id)
        except Exception:
            logger.exception("voice_tools_reset_failed")

    def _persist_assistant(self, conversation, content, sources, tool_calls, flags) -> None:
        self.db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.assistant,
                content=content,
                sources=sources,
                tool_calls=tool_calls,
                guardrail_flags=flags,
            )
        )
        self.db.commit()
        append_message(conversation.id, "assistant", content)

    async def _say(self, text: str, ts: _TurnState) -> None:
        """Speak canned text outside the agent (reprompts, warnings, idle)."""
        await self.send({"type": "turn_start", "turn_id": ts.turn_id})
        await self.send({"type": "notice", "text": text, "turn_id": ts.turn_id})
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        queue.put_nowait(_Status(text))
        queue.put_nowait(None)
        await self._speak_loop(queue, ts, None)

    async def _speak_loop(self, queue: asyncio.Queue, ts: _TurnState, t_end: float | None) -> None:
        """Synthesizes queued sentences in order and streams them out."""
        seq = 0
        spoke_any = False
        while True:
            sentence = await queue.get()
            if sentence is None:
                break
            if ts.muted or not self.tts_ok:
                continue
            clean = speakable(sentence)
            if not clean:
                continue
            try:
                pcm = await self.tts.synthesize(clean)
            except TTSError as exc:
                logger.warning("tts_failed error=%s", exc)
                voice_events_total.labels(event="tts_failure").inc()
                self.tts_ok = False
                await self.send({"type": "tts_unavailable", "message": "Spoken replies failed; showing captions only."})
                continue
            if ts.muted:
                continue  # interrupted while synthesizing: don't play it
            self._recent_spoken.append((time.monotonic(), clean))
            for i in range(0, len(pcm), _AUDIO_FRAME_BYTES):
                frame = pcm[i : i + _AUDIO_FRAME_BYTES]
                if ts.muted or not await self._send_audio(ts.turn_id, seq, frame):
                    break
                seq += 1
                self._note_audio_sent(len(frame))
                if t_end is not None and not spoke_any:
                    voice_first_audio_seconds.observe(time.monotonic() - t_end)
                spoke_any = True
            else:
                if not isinstance(sentence, _Status):
                    ts.spoken.append(sentence)  # only the agent's own answer counts as "what they heard"
        if spoke_any:
            await self.send({"type": "speaking_end", "turn_id": ts.turn_id})

    # ------------------------------------------------------------ cleanup

    async def cleanup(self) -> None:
        self.closing = True
        if self._warm_task and not self._warm_task.done():
            self._warm_task.cancel()
        if self._intake_tail and not self._intake_tail.done():
            self._intake_tail.cancel()
        task = self.task
        if task and not task.done():
            if self._write_window:
                # Disconnected mid refund/cancel: let the write finish.
                await asyncio.wait({task}, timeout=15)
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        try:
            await self._stack.aclose()
        except Exception:
            logger.exception("voice_mcp_close_failed")
        await self.stt.close()
