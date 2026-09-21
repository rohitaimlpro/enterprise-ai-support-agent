"""
Pure text helpers for the voice pipeline (no network, no audio), kept
separate so the fiddly edge cases -- sentence splitting, what's speakable,
what's a Whisper hallucination -- are unit-testable in isolation.
"""

import difflib
import re

# Devanagari block, built from code points so this file stays readable.
_DEVANAGARI = chr(0x0900) + "-" + chr(0x097F)
# Python's \b treats Devanagari vowel signs (matras) as non-word characters, so
# it would split words like "हाँ" mid-way. Explicit lookarounds avoid that.
_L = rf"(?<![\w{_DEVANAGARI}])"
_R = rf"(?![\w{_DEVANAGARI}])"

# ---------------------------------------------------------------------------
# Sentence chunking: LLM tokens in, speakable sentences out
# ---------------------------------------------------------------------------

_ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "no", "inc", "ltd",
}
_MIN_SENTENCE_CHARS = 12  # don't send "Sure." on its own -- merge into the next sentence
_MAX_BUFFER_CHARS = 220  # a run-on with no punctuation still has to start playing


class SentenceChunker:
    """Buffers streamed LLM text and releases whole sentences.

    Sending each sentence to TTS as soon as it completes is what keeps
    time-to-first-audio low: we don't wait for the full answer.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        while True:
            cut = self._find_boundary()
            if cut is None:
                break
            sentence, self._buf = self._buf[:cut].strip(), self._buf[cut:].lstrip()
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []

    def _find_boundary(self) -> int | None:
        buf = self._buf
        for i, ch in enumerate(buf):
            if ch == "\n" and i >= _MIN_SENTENCE_CHARS:
                return i + 1
            if ch in ".!?" and i + 1 < len(buf) and buf[i + 1].isspace():
                if i + 1 < _MIN_SENTENCE_CHARS:
                    continue
                word = re.findall(r"[A-Za-z.]+$", buf[:i])
                if ch == "." and word and word[0].lower().rstrip(".") in _ABBREVIATIONS:
                    continue
                return i + 1
        if len(buf) > _MAX_BUFFER_CHARS:
            cut = max(buf.rfind(", ", 0, _MAX_BUFFER_CHARS), buf.rfind(" ", 0, _MAX_BUFFER_CHARS))
            return cut + 1 if cut > 0 else _MAX_BUFFER_CHARS
        return None


# ---------------------------------------------------------------------------
# Speakable text: what's fine on screen sounds terrible aloud
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-([0-9a-fA-F]{12})\b")
_URL_RE = re.compile(r"https?://\S+")
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def speakable(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [label](url) -> label
    text = _URL_RE.sub("the link in the chat", text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"(\*\*|__|\*|_)(\S.*?\S|\S)\1", r"\2", text)  # bold/italic
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)  # headings
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.M)  # bullets
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.M)  # numbered lists
    # A 36-char UUID read aloud is unbearable; say the tail, spaced out.
    text = _UUID_RE.sub(lambda m: "ending in " + " ".join(m.group(1)[-4:]), text)
    text = _EMOJI_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Transcript cleanup
# ---------------------------------------------------------------------------

# Whisper is known to invent these on silence/noise. Only dropped when the
# model's own confidence signals say it wasn't really speech (see stt.py) --
# a real "thank you" at the end of a call must still get through.
HALLUCINATION_PHRASES = {
    "thank you", "thanks", "thanks for watching", "thank you for watching", "thank you so much",
    "please subscribe", "you", "bye", "bye bye", "okay", "so", "the end",
    # Whisper's usual silence hallucinations in Hindi
    "धन्यवाद", "शुक्रिया", "dhanyavad", "shukriya", "आपका धन्यवाद",
}

_DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Roman Hindi, for phone numbers spoken as "nau aath saat ..."
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4",
    "paanch": "5", "panch": "5", "chhe": "6", "chhah": "6", "cheh": "6",
    "saat": "7", "aath": "8", "nau": "9",
}
_DIGIT_ALT = "|".join(sorted(_DIGIT_WORDS, key=len, reverse=True))  # longest first: "chaar" before "char"
# A run needs 3+ digit words in a row, so ordinary uses of "do" ("do it") or
# "ek" ("ek minute") are left alone.
_DIGIT_RUN_RE = re.compile(r"\b(?:(?:%s)(?:\s+|-|,\s*)?){3,}" % _DIGIT_ALT, re.I)
_DEVA_DIGIT_WORDS = {
    "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5",
    "छह": "6", "छः": "6", "सात": "7", "आठ": "8", "नौ": "9",
}
_DEVA_RUN_RE = re.compile(
    _L + r"(?:(?:%s)\s*){3,}" % "|".join(sorted(_DEVA_DIGIT_WORDS, key=len, reverse=True)) + _R
)
_EMAIL_RE = re.compile(r"\b([\w.+-]+)\s+at\s+([\w-]+)\s+dot\s+(com|org|net|io|in|co|edu|gov)\b", re.I)


def normalize_spoken(text: str) -> str:
    """'order one two three dash four five six' -> 'order 123-456'; and
    'john at gmail dot com' -> 'john@gmail.com'. Conservative on purpose:
    only digit-word runs of 3+ and unambiguous email shapes are touched."""

    def digits(m: re.Match) -> str:
        words = [w for w in re.split(r"[\s,-]+", m.group(0).strip().lower()) if w]
        return "".join(_DIGIT_WORDS[w] for w in words) + " "

    def deva_digits(m: re.Match) -> str:
        return "".join(_DEVA_DIGIT_WORDS[w] for w in re.findall("|".join(_DEVA_DIGIT_WORDS), m.group(0))) + " "

    text = _DIGIT_RUN_RE.sub(digits, text)
    text = _DEVA_RUN_RE.sub(deva_digits, text)
    text = re.sub(r"(\d)\s+dash\s+(\d)", r"\1-\2", text, flags=re.I)
    text = _EMAIL_RE.sub(lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}".lower(), text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Speech-recognition word errors
# ---------------------------------------------------------------------------

# The words this assistant's requests hinge on, and the ways Whisper commonly
# writes them instead: mangled Roman spellings and Devanagari transliterations
# ("रिफंड" for "refund"). Fixing these before the agent (and the refund
# confirmation gate) sees the text means a misheard word can't change what
# the user appears to have asked for.
_TERM_VARIANTS = {
    "refund": ["rifund", "refand", "refnd", "rifand", "reefund", "riphund", "रिफंड", "रीफंड", "रिफण्ड", "रिफन्ड"],
    "refunds": ["रिफंड्स", "रिफंडस"],
    "order": ["ordar", "ordor", "aardar", "ऑर्डर", "आर्डर", "ओर्डर", "ऑडर", "आर्डेर"],
    "orders": ["ऑर्डर्स", "आर्डर्स", "ऑर्डरस"],
    "cancel": ["cansal", "kansal", "cancle", "कैंसल", "कैन्सल", "कैंसिल", "कैन्सिल", "कैंसल्"],
    "invoice": ["invoyce", "inwoice", "invois", "इनवॉइस", "इनवॉयस", "इन्वॉइस", "इनवाइस"],
    "ticket": ["tikat", "tikit", "टिकट", "टिकेट"],
    "callback": ["कॉलबैक", "कॉल-बैक"],
    "subscription": ["subscripsan", "subscripton", "सब्सक्रिप्शन", "सब्स्क्रिप्शन"],
    "delivery": ["डिलीवरी", "डिलिवरी", "delivri"],
    "payment": ["पेमेंट", "peyment"],
    "password": ["पासवर्ड", "pasword", "paswad"],
    "account": ["अकाउंट", "acount"],
    "plan": ["प्लान"],
    "shipping": ["शिपिंग"],
    "tracking": ["ट्रैकिंग", "ट्रेकिंग"],
}
_VARIANT_TO_TERM = {v: term for term, variants in _TERM_VARIANTS.items() for v in variants}

# Whisper's habitual Roman-Hindi spellings -> the ones this app expects.
_SPELLING_FIXES = {"kaa": "ka", "kee": "ki", "nahin": "nahi", "nhi": "nahi"}
_PHRASE_FIXES = [
    ("call back", "callback"),
    ("rene do", "rehne do"),
    ("rahne do", "rehne do"),
    ("rehne dijiye", "rehne do"),
]
# Only these are fuzzy-matched. Kept short and specific: every entry is a word the
# assistant would badly misunderstand if it were garbled, and a rare-in-English word
# so a near-match is almost certainly the intended one.
_FUZZY_TERMS = ["refund", "invoice", "subscription", "callback", "cancel"]
# Whisper spells "rehne do" (leave it) differently every time: rene, rehnye, rahne, renay...
_REHNE_DO_RE = re.compile(r"\br[ae]h?n[aeiy]{0,3}\s+do\b", re.IGNORECASE)
_WORD_RE = re.compile(rf"[\w{_DEVANAGARI}]+")


def correct_transcript(text: str) -> str:
    """Repair common speech-recognition word errors in support vocabulary.
    Conservative: exact known variants first, then fuzzy matching for a few key
    terms only, never touching a word that merely *starts with* a term
    ('cancelled', 'refunded', 'delivered') or is short."""
    lowered = _REHNE_DO_RE.sub("rehne do", text)
    for wrong, right in _PHRASE_FIXES:
        lowered = re.sub(re.escape(wrong), right, lowered, flags=re.IGNORECASE)

    def fix(match: re.Match) -> str:
        word = match.group(0)
        low = word.lower()
        replacement = _VARIANT_TO_TERM.get(word) or _VARIANT_TO_TERM.get(low) or _SPELLING_FIXES.get(low)
        if replacement is None and len(low) >= 6 and low.isascii():
            if not any(low.startswith(t) or t.startswith(low) for t in _FUZZY_TERMS):
                close = difflib.get_close_matches(low, _FUZZY_TERMS, n=1, cutoff=0.82)
                if close and close[0][0] == low[0]:
                    replacement = close[0]
        if replacement is None:
            return word
        return replacement.capitalize() if word[:1].isupper() else replacement

    return _WORD_RE.sub(fix, lowered)


_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0


def redact_card_numbers(text: str) -> tuple[str, bool]:
    """Card-number-shaped digit runs (Luhn-checked) never get stored or
    sent to the model. Returns (clean_text, whether anything was removed)."""
    found = False

    def sub(m: re.Match) -> str:
        nonlocal found
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found = True
            return "[card number removed]"
        return m.group(0)

    return _CARD_RE.sub(sub, text), found


def is_looping_garbage(text: str) -> bool:
    """Whisper sometimes degenerates into 'the the the the ...' loops."""
    words = re.findall(r"\w+", text.lower())
    run = 1
    for a, b in zip(words, words[1:]):
        run = run + 1 if a == b else 1
        if run >= 6:
            return True
    return False


_REPEAT_RE = re.compile(
    r"^\s*(?:sorry[, ]*)?(?:can you |could you |please )?"
    r"(?:say that again|repeat that|repeat (?:it|yourself)|come again|what was that|i didn'?t (?:catch|hear) that"
    # Hinglish
    r"|(?:ek baar )?(?:phir se|dobara|fir se) (?:boliye|bolo|bataiye|batao|bolna)(?: please)?"
    r"|kya kaha|kya bola|repeat karo|repeat kijiye|samajh nahi aaya"
    # Devanagari
    r"|(?:फिर से|दोबारा) (?:बोलिए|बोलो|बताइए|बताओ)|क्या कहा|क्या बोला)\W*$",
    re.I,
)


def is_repeat_request(text: str) -> bool:
    return bool(_REPEAT_RE.match(text))


# ---------------------------------------------------------------------------
# Confirmation phrases (used by confirm.py)
# ---------------------------------------------------------------------------


_AFFIRM_RE = re.compile(
    _L
    + r"(?:yes|yeah|yep|yup|sure|correct|confirm|confirmed|affirmative|go ahead|do it|please do"
    r"|that'?s right|that is right|that'?s correct"
    # Hinglish (Roman)
    r"|haan|haa|han|haanji|haan ji|ji haan|bilkul|theek hai|thik hai|sahi hai|kar do|kardo|kar dijiye|kar dijiye"
    # Devanagari
    r"|हाँ|हां|जी हाँ|जी हां|बिल्कुल|ठीक है|सही है|कर दो|कर दीजिए|कर दीजिये|करदो)"
    + _R,
    re.I,
)
_NEGATE_RE = re.compile(
    _L
    + r"(?:no|nope|not|don'?t|do not|wait|stop|cancel that|never ?mind|wrong|actually|but|hold on"
    # Hinglish (Roman)
    r"|nahi|nahin|nhi|mat|ruko|ruk|rukiye|rukna|galat|lekin|magar|abhi nahi|ek minute|ek second"
    # Devanagari
    r"|नहीं|नही|मत|रुको|रुकिए|रुकें|गलत|लेकिन|मगर|एक मिनट)"
    + _R,
    re.I,
)


def is_affirmative(text: str) -> bool:
    """A confirmation must be short, contain an explicit yes, and contain
    nothing that hedges it ('yes but...', 'no wait'). Long or mixed replies
    are treated as NOT a confirmation and the read-back gets asked again."""
    if len(text.split()) > 7:
        return False
    return bool(_AFFIRM_RE.search(text)) and not _NEGATE_RE.search(text)


# ---------------------------------------------------------------------------
# One-to-one conversation edge cases
# ---------------------------------------------------------------------------

_BACKCHANNEL_WORDS = {
    "hmm", "hm", "mm", "mhm", "uh", "um", "ah", "aha", "ahaa", "oh", "oho", "haan", "han", "haa", "ha", "hanji", "ji",
    "achha", "accha", "acha", "okay", "ok", "yes", "yeah", "yep", "right", "sure", "theek", "thik",
    "अच्छा", "हाँ", "हां", "जी", "ठीक", "हम्म", "ओह",
}


def is_backchannel(text: str) -> bool:
    """'hmm', 'haan', 'okay' said *while the agent is talking* are the user
    saying "I'm listening", not a new request -- they must not cut the agent
    off. At most two words, all of them acknowledgements."""
    words = re.findall(rf"[\w{_DEVANAGARI}']+", text.lower())
    return 0 < len(words) <= 2 and all(w in _BACKCHANNEL_WORDS for w in words)


_WAIT_RE = re.compile(_L + r"(?:wait|but|actually|hold on|ruko|lekin|magar|ek minute|एक मिनट|रुको)" + _R, re.I)
_GOODBYE_RE = re.compile(
    _L
    + r"(?:bye|goodbye|good bye|see you|talk (?:to you )?later|that'?s all|that is all|nothing else"
    r"|end (?:the )?call|hang up"
    # Hinglish (Roman)
    r"|alvida|bas itna hi|itna hi kaafi|call (?:band|khatam) kar|phone rakh|chalta hoon|chalti hoon"
    # Devanagari
    r"|अलविदा|बस इतना ही|कॉल बंद|फोन रख)"
    + _R,
    re.I,
)


def is_goodbye(text: str) -> bool:
    """A short farewell ('ok bye', 'bas itna hi, dhanyavad'). Long sentences
    that merely contain 'that's all' ('that's all I paid for my order') are
    not farewells, and a hedge ('bye... wait') cancels it."""
    return len(text.split()) <= 7 and bool(_GOODBYE_RE.search(text)) and not _WAIT_RE.search(text)


# Devanagari consonant -> the Roman consonant it sounds like. Vowels, vowel signs,
# the virama and nasal marks are simply dropped (see _skeleton).
_DEVA_CONSONANTS = {
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n",
    "च": "k", "छ": "k", "ज": "j", "झ": "j", "ञ": "n",
    "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n",
    "त": "t", "थ": "t", "द": "d", "ध": "d", "न": "n",
    "प": "p", "फ": "p", "ब": "b", "भ": "b", "म": "m",
    "र": "r", "ल": "l", "ळ": "l", "व": "v", "श": "s", "ष": "s", "स": "s",
}
_ROMAN_CONSONANT_MERGES = {"c": "k", "q": "k", "x": "k", "z": "j", "w": "v"}


def _skeleton(text: str) -> str:
    """A script-independent 'sound outline': just the consonants, with
    look-alikes merged. 'dekhte' and 'देखते' both become 'dkt'; 'aapka' and 'आपका'
    both become 'pk'. Only meant for comparing two texts to each other."""
    out: list[str] = []
    for ch in text.lower():
        if ch in _DEVA_CONSONANTS:
            letter = _DEVA_CONSONANTS[ch]
        elif "a" <= ch <= "z":
            letter = _ROMAN_CONSONANT_MERGES.get(ch, ch)
            if letter in "aeiouyh":  # vowels; 'h' only ever adds aspiration (kh, dh, sh...)
                continue
        else:
            continue
        if not out or out[-1] != letter:  # 'shipped' and 'शिप्ड' both -> 'spd'
            out.append(letter)
    return "".join(out)


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


_ECHO_MIN_WORD_COVERAGE = 0.75


def _word_coverage(text: str, agent_sentences: list[str]) -> float:
    """Fraction of the words in `text` that (by sound, so the script doesn't matter)
    appear somewhere in what the agent said. Near-matches count, because Whisper
    garbles some words of an echo."""
    # Outlines shorter than 3 consonants ("k" from "ka", "st" from "Suite") match by
    # accident and prove nothing, so only distinctive words count.
    said = {w for w in (_skeleton(t) for t in " ".join(agent_sentences).split()) if len(w) >= 3}
    heard = [w for w in (_skeleton(t) for t in text.split()) if len(w) >= 3]
    if not heard or not said:
        return 0.0
    said_list = sorted(said)
    hits = 0
    for word in heard:
        if word in said or difflib.get_close_matches(word, said_list, n=1, cutoff=0.8):
            hits += 1
    return hits / len(heard)


def looks_like_echo(text: str, recent_agent_sentences: list[str], threshold: float = 0.72) -> bool:
    """True if `text` is (nearly) something the agent just said. When the
    agent's voice leaks from the speakers into the microphone, it gets
    transcribed like a user utterance -- answering it would make the agent
    talk to itself. Compared against runs of 1-3 consecutive sentences,
    because an echo can span sentence boundaries."""
    # Fewer than 4 words is never treated as echo: "Graphics Tablet Pro" is a perfectly
    # good answer to "which order?" right after the agent listed its orders, and echo
    # leaking back is almost always a whole clause, not a name.
    if len(text.split()) < 4 or not recent_agent_sentences:
        return False

    # 1. Same script (Roman vs Roman): compare the words.
    # An echo is *all* agent speech. A reply that merely shares a phrase with it
    # ("Graphics Tablet Pro ka status batao" after the agent said "Graphics Tablet Pro
    # shipped ho chuka hai") has words of its own, so most of its words are NOT in
    # what the agent said. Require that before any similarity score can call it echo.
    if _word_coverage(text, recent_agent_sentences) < _ECHO_MIN_WORD_COVERAGE:
        return False

    heard = _plain(text)
    plain = [_plain(s) for s in recent_agent_sentences]
    if heard in " ".join(plain):
        return True
    for size in (1, 2, 3):
        for i in range(0, max(1, len(plain) - size + 1)):
            candidate = " ".join(plain[i : i + size])
            if candidate and difflib.SequenceMatcher(None, heard, candidate).ratio() >= threshold:
                return True

    # 2. Different scripts: Whisper often writes Hinglish speech in Devanagari
    #    ("आपका order देखते हैं") while the agent's text was Roman ("aapka order
    #    dekhte hain"). Compare how the words *sound* instead of how they're spelled.
    heard_sk = _skeleton(text)
    if len(heard_sk) < 8:
        return False
    said_sk = [_skeleton(s) for s in recent_agent_sentences]
    said_all = "".join(said_sk)
    if heard_sk in said_all:
        return True
    match = difflib.SequenceMatcher(None, heard_sk, said_all, autojunk=False).find_longest_match(
        0, len(heard_sk), 0, len(said_all)
    )
    if match.size >= 10 and match.size >= 0.75 * len(heard_sk):  # nearly all of it is one contiguous chunk of what was said
        return True
    for size in (1, 2, 3, 4):
        for i in range(0, max(1, len(said_sk) - size + 1)):
            candidate = "".join(said_sk[i : i + size])
            if candidate and difflib.SequenceMatcher(None, heard_sk, candidate).ratio() >= 0.75:
                return True
    return False
