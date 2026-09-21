"""A refund must only run on a clear yes -- in English, Roman Hinglish or
Devanagari, however Whisper happens to write it."""

import asyncio

import pytest

from app.voice.confirm import ConfirmationGate


def _confirmed(reply: str) -> bool:
    gate = ConfirmationGate()
    asyncio.run(gate("refund_request", {"order_id": "abc"}))  # blocked, pending read-back
    gate.on_user_utterance(reply)
    return asyncio.run(gate("refund_request", {"order_id": "abc"})) is None


@pytest.mark.parametrize(
    "reply",
    ["haan", "haan kar do", "ji haan bilkul", "theek hai kar dijiye", "हाँ", "जी हां", "हाँ कर दीजिए", "बिल्कुल"],
)
def test_clear_hinglish_yes_confirms(reply):
    assert _confirmed(reply)


@pytest.mark.parametrize(
    "reply",
    ["nahi", "nahi nahi mat karo", "haan lekin ruko", "haan par ek minute", "नहीं", "मत करो", "हाँ लेकिन रुको", "ruko"],
)
def test_hinglish_no_or_hedge_does_not_confirm(reply):
    assert not _confirmed(reply)


@pytest.mark.parametrize("reply", ["mera order kahan hai", "मुझे invoice चाहिए", "refund policy kya hai"])
def test_unrelated_hinglish_does_not_confirm(reply):
    assert not _confirmed(reply)
