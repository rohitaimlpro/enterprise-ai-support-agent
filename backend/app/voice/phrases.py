"""
Every line the *system* (not the LLM) says to the user, in one place:
"checking..." lines when a tool starts, reprompts, warnings.

Two reasons these are canned instead of model-generated: they must appear
instantly (the whole point of a "checking" line is to fill the wait for the
model/tool), and they must be predictable. They're phrased gender-neutrally
("check karte hain") because the configured TTS voice may be female or male.
"""

from app.config import get_settings

_HINGLISH = {
    "tool:search_knowledge_base": "Ek minute, knowledge base mein check karte hain.",
    "tool:lookup_order": "Ek second, aapka order dekhte hain.",
    "tool:get_invoice": "Ek second, invoice nikaalte hain.",
    "tool:create_ticket": "Ek minute, aapka ticket bana rahe hain.",
    "tool:escalate_issue": "Theek hai, isse human team tak pahunchate hain.",
    "tool:schedule_callback": "Ek second, callback note kar lete hain.",
    "tool:default": "Ek minute, check karte hain.",
    "reprompt": "Sorry, mujhe theek se sunai nahi diya. Kya aap dobara bol sakte hain?",
    "give_up": "Mujhe aapki awaaz clear nahi aa rahi. Chaliye, chat box mein type kar dijiye.",
    "card_warning": "Aapki security ke liye, please card number bolkar mat bataiye. Maine woh hissa ignore kar diya hai.",
    "idle": "Kya aap abhi bhi wahan hain?",
    "apology": "Sorry, kuch gadbad ho gayi. Please dobara try kijiye.",
    "timeout": "Isme thoda zyada time lag raha hai. Please thodi der baad dobara try kijiye.",
    "tools_down": "Abhi order system tak pahunch nahi pa rahe, isliye sirf general sawaalon ke jawab de sakte hain.",
    "more_in_chat": "Baaki details chat mein likhi hui hain.",
    "voice_sample": "Namaste! Main aapki madad ke liye yahaan hoon. Aap apna order ya refund ke baare mein pooch sakte hain.",
    "stt_hint": (
        "Meridian Suite support call, Hinglish. Refund, order, invoice, subscription, ticket, callback. "
        "Mera order kahan hai? Mujhe refund chahiye. Account mein login nahi ho raha."
    ),
}

_ENGLISH = {
    "tool:search_knowledge_base": "One moment, let me check the knowledge base.",
    "tool:lookup_order": "One second, looking up your order.",
    "tool:get_invoice": "One second, pulling up the invoice.",
    "tool:create_ticket": "One moment, creating your ticket.",
    "tool:escalate_issue": "Okay, passing this to our human team.",
    "tool:schedule_callback": "One second, noting down the callback.",
    "tool:default": "One moment, let me check that for you.",
    "reprompt": "Sorry, I didn't catch that. Could you say it again?",
    "give_up": "I'm having trouble hearing you. Let's switch to typing, you can use the chat box.",
    "card_warning": "For your security, please don't say card numbers out loud. I've ignored that part.",
    "idle": "Are you still there?",
    "apology": "Sorry, something went wrong on my side. Please try again.",
    "timeout": "That's taking longer than expected. Please try again in a moment.",
    "tools_down": "I can't reach our order system right now, so I can only answer general questions.",
    "more_in_chat": "The rest of the details are in the chat.",
    "voice_sample": "Hello! I'm here to help. You can ask me about your order or a refund.",
    "stt_hint": "Meridian Suite support call: refund, invoice, subscription, order, ticket, callback.",
}


def _table() -> dict[str, str]:
    return _HINGLISH if get_settings().assistant_language.lower() == "hinglish" else _ENGLISH


def phrase(key: str) -> str:
    return _table()[key]


def tool_status(tool_name: str) -> str:
    """The 'I'm working on it' line shown/spoken when a tool starts."""
    table = _table()
    return table.get(f"tool:{tool_name}", table["tool:default"])
