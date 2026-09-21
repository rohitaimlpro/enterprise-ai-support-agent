"""Pure-text edge cases of the voice pipeline: chunking, speakable text,
transcript cleanup and confirmation phrases."""

import array

import pytest

from app.voice.stt import judge_result, pcm_rms, pcm_to_wav, split_pcm
from app.voice.text_utils import (
    SentenceChunker,
    is_affirmative,
    is_looping_garbage,
    is_repeat_request,
    normalize_spoken,
    redact_card_numbers,
    speakable,
)


def _chunk_all(text: str, step: int = 5) -> list[str]:
    """Feed in small pieces, like streamed tokens, and return all sentences."""
    c = SentenceChunker()
    out: list[str] = []
    for i in range(0, len(text), step):
        out += c.feed(text[i : i + step])
    return out + c.flush()


class TestSentenceChunker:
    def test_splits_on_sentence_boundaries(self):
        out = _chunk_all("Your refund was approved today. It will arrive in five days. Anything else?")
        assert out == ["Your refund was approved today.", "It will arrive in five days.", "Anything else?"]

    def test_does_not_split_on_abbreviations_or_decimals(self):
        out = _chunk_all("Please ask Dr. Smith about the 3.5 percent fee, e.g. next week. Thanks for waiting!")
        assert len(out) == 2
        assert out[0].startswith("Please ask Dr. Smith") and "3.5" in out[0]

    def test_short_sentence_merges_with_next(self):
        out = _chunk_all("Sure. Your order shipped on Monday and arrives Friday.")
        assert out == ["Sure. Your order shipped on Monday and arrives Friday."]

    def test_flush_returns_unpunctuated_tail(self):
        c = SentenceChunker()
        assert c.feed("Almost done with this one") == []
        assert c.flush() == ["Almost done with this one"]
        assert c.flush() == []

    def test_long_run_on_is_force_split(self):
        text = "word " * 100
        out = _chunk_all(text.strip())
        assert len(out) >= 2 and all(len(s) <= 230 for s in out)


class TestSpeakable:
    def test_strips_markdown(self):
        assert speakable("**Bold** and `code`\n- item one\n- item two") == "Bold and code item one item two"

    def test_links_and_urls(self):
        assert speakable("See [our policy](https://x.io/p) now") == "See our policy now"
        assert "the link in the chat" in speakable("Go to https://example.com/very/long")

    def test_uuid_read_as_last_four(self):
        out = speakable("Order 123e4567-e89b-12d3-a456-426614174000 shipped")
        assert "ending in 4 0 0 0" in out and "123e4567" not in out

    def test_removes_emoji(self):
        assert speakable("Done \U0001F389") == "Done"


class TestTranscriptCleanup:
    def test_digit_words_become_digits(self):
        assert normalize_spoken("order one two three dash four five six please") == "order 123-456 please"

    def test_two_digit_words_left_alone(self):
        assert normalize_spoken("I have one two questions") == "I have one two questions"

    def test_spoken_email(self):
        assert normalize_spoken("it is John at Gmail dot com") == "it is john@gmail.com"

    def test_card_number_redacted(self):
        text, found = redact_card_numbers("my card is 4111 1111 1111 1111 thanks")
        assert found and "4111" not in text and "[card number removed]" in text

    def test_non_card_digit_run_kept(self):
        text, found = redact_card_numbers("order 1234567890123 please")  # fails Luhn
        assert not found and "1234567890123" in text

    def test_looping_garbage(self):
        assert is_looping_garbage("the the the the the the the")
        assert not is_looping_garbage("I really really want a refund")

    @pytest.mark.parametrize("phrase", ["say that again", "Sorry, could you repeat that?", "what was that"])
    def test_repeat_requests(self, phrase):
        assert is_repeat_request(phrase)

    def test_normal_sentence_is_not_repeat(self):
        assert not is_repeat_request("can you repeat the refund policy for annual plans")


class TestAffirmative:
    @pytest.mark.parametrize("t", ["yes", "Yeah go ahead", "yes please do", "that's correct"])
    def test_accepts_clear_yes(self, t):
        assert is_affirmative(t)

    @pytest.mark.parametrize(
        "t",
        [
            "no",
            "yes but wait",
            "actually no",
            "I think so maybe",
            "yes and also refund all my other orders too please thanks",  # long, mixed = not a confirmation
        ],
    )
    def test_rejects_hedged_or_long(self, t):
        assert not is_affirmative(t)


def _pcm(seconds: float, amplitude: int) -> bytes:
    n = int(16000 * seconds)
    return array.array("h", [amplitude if i % 2 else -amplitude for i in range(n)]).tobytes()


class TestSttHelpers:
    def test_rms_separates_silence_from_speech(self):
        assert pcm_rms(_pcm(0.5, 0)) == 0.0
        assert pcm_rms(_pcm(0.5, 8000)) > 0.2

    def test_wav_header(self):
        assert pcm_to_wav(_pcm(0.1, 100))[:4] == b"RIFF"

    def test_long_audio_split_into_chunks(self):
        chunks = split_pcm(_pcm(65, 100), 30)
        assert len(chunks) == 3 and sum(len(c) for c in chunks) == 65 * 16000 * 2

    def test_judge_drops_silence_hallucination(self):
        r = judge_result({"text": "Thank you.", "segments": [{"no_speech_prob": 0.8, "avg_logprob": -1.4}]})
        assert r.text == "" and r.dropped_reason

    def test_judge_keeps_confident_thank_you(self):
        r = judge_result({"text": "Thank you.", "segments": [{"no_speech_prob": 0.02, "avg_logprob": -0.2}]})
        assert r.text == "Thank you."

    def test_judge_empty_and_looping(self):
        assert judge_result({"text": "  "}).text == ""
        assert judge_result({"text": "go go go go go go go", "segments": []}).text == ""

    def test_judge_passes_normal_speech_and_language(self):
        r = judge_result({"text": "Where is my order?", "language": "english", "segments": [{"no_speech_prob": 0.01, "avg_logprob": -0.3}]})
        assert r.text == "Where is my order?" and r.language == "english"
