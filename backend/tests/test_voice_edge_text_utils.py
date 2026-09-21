"""Text helpers behind the one-to-one conversation edge cases."""

import pytest

from app.voice.text_utils import is_backchannel, is_goodbye, looks_like_echo, normalize_spoken

SAID = ["Aapke orders mein Graphics Tablet Pro shipped ho chuka hai.", "Meridian Suite Team Plan deliver ho gaya hai."]


class TestEcho:
    def test_exact_and_slightly_garbled_echo(self):
        assert looks_like_echo("Aapke orders mein Graphics Tablet Pro shipped ho chuka hai", SAID)
        assert looks_like_echo("aapke order mein graphics tablet pro shipped ho chuka hai", SAID)

    def test_echo_spanning_two_sentences(self):
        assert looks_like_echo("shipped ho chuka hai Meridian Suite Team Plan deliver ho gaya hai", SAID)

    def test_a_fragment_inside_what_was_said(self):
        assert looks_like_echo("Graphics Tablet Pro shipped ho chuka", SAID)

    def test_real_user_speech_is_not_echo(self):
        assert not looks_like_echo("mujhe refund chahiye order ke liye", SAID)

    def test_very_short_utterances_are_never_called_echo(self):
        assert not looks_like_echo("haan", SAID)
        assert not looks_like_echo("ho gaya", SAID)

    def test_nothing_said_yet_means_nothing_to_echo(self):
        assert not looks_like_echo("aapke orders mein graphics tablet", [])


class TestBackchannel:
    @pytest.mark.parametrize("t", ["hmm", "Hmm.", "haan", "haan ji", "okay", "achha", "जी हाँ", "अच्छा"])
    def test_acknowledgements(self, t):
        assert is_backchannel(t)

    @pytest.mark.parametrize("t", ["haan kar do", "nahi", "ruko", "mera order kahan hai", "haan ji theek hai"])
    def test_real_speech_is_not_backchannel(self, t):
        assert not is_backchannel(t)

    def test_empty(self):
        assert not is_backchannel("") and not is_backchannel("...")


class TestGoodbye:
    @pytest.mark.parametrize(
        "t", ["bye", "ok bye", "goodbye", "theek hai bas itna hi", "bas itna hi dhanyavad", "अलविदा", "बस इतना ही", "chalta hoon"]
    )
    def test_farewells(self, t):
        assert is_goodbye(t)

    @pytest.mark.parametrize(
        "t",
        [
            "that's all I paid for my order last week please check it",  # long sentence containing a farewell phrase
            "bye wait one more thing",  # hedged
            "goodbye lekin ek sawaal hai",
            "mera order kahan hai",
        ],
    )
    def test_not_farewells(self, t):
        assert not is_goodbye(t)


class TestHindiNumbers:
    def test_phone_number_spoken_in_hindi(self):
        assert normalize_spoken("mera number nau aath saat chhe paanch hai") == "mera number 98765 hai"

    def test_devanagari_number_words(self):
        assert normalize_spoken("नौ आठ सात छह") == "9876"

    @pytest.mark.parametrize("t", ["please do it now", "ek minute ruko", "do din baad", "ek do din"])
    def test_ordinary_words_are_left_alone(self, t):
        assert normalize_spoken(t) == t

    def test_english_digit_words_still_work(self):
        assert normalize_spoken("order one two three dash four five six") == "order 123-456"


AGENT_SAID = [
    "Ek second, aapka order dekhte hain.",
    "Aapke orders ki details yeh hain.",
    "Premium Font & Template Pack abhi processing mein hai.",
    "Graphics Tablet Pro ship ho chuka hai aur Meridian Suite Team Plan deliver ho gaya hai.",
]


class TestEchoAcrossScripts:
    """Whisper writes Hinglish speech in Devanagari even when the agent's text
    was Roman -- the case that defeated a plain word comparison in live testing."""

    def test_the_exact_transcript_whisper_produced_for_the_agents_own_voice(self):
        whisper = (
            "Eek second, आपका order देखते हैं. आपके orders की details ये है. Premium font template pack "
            "अभी processing में है. Graphics tablet pro ship हो चुका है और Meridian's with team plan deliver हो गया है."
        )
        assert looks_like_echo(whisper, AGENT_SAID)

    def test_a_devanagari_fragment_of_what_was_said(self):
        assert looks_like_echo("आपके orders की details ये है Premium font template pack", AGENT_SAID)

    @pytest.mark.parametrize(
        "user_said",
        [
            "मुझे refund चाहिए order के लिए",
            "mujhe refund chahiye order ke liye",
            "मेरा order कब तक आएगा",
            "मुझे अपना invoice भेज दीजिए",
            "क्या आप मुझे callback कर सकते हैं",
        ],
    )
    def test_real_user_requests_in_either_script_are_not_echo(self, user_said):
        assert not looks_like_echo(user_said, AGENT_SAID)

    def test_sound_outline_matches_across_scripts(self):
        from app.voice.text_utils import _skeleton

        assert _skeleton("dekhte") == _skeleton("देखते") == "dkt"
        assert _skeleton("aapka") == _skeleton("आपका") == "pk"
        assert _skeleton("shipped") == _skeleton("शिप्ड") == "spd"


SAID_WITH_ORDERS = [
    "Ek second, aapka order dekhte hain.",
    "Aapke orders ki details yeh hain.",
    "Premium Font & Template Pack abhi processing mein hai.",
    "Graphics Tablet Pro ship ho chuka hai aur Meridian Suite Team Plan deliver ho gaya hai.",
]


class TestEchoDoesNotSwallowRealAnswers:
    """Found in live testing: the agent lists the user's orders, the user answers
    with a product name or repeats part of it, and that was dropped as 'echo'."""

    @pytest.mark.parametrize(
        "user_said",
        [
            "Graphics Tablet Pro",  # the natural answer to "which order?"
            "Graphics Tablet Pro ka status batao",  # shares a phrase with the agent's list, adds its own words
            "mujhe Graphics Tablet Pro ka refund chahiye",
            "Meridian Suite Team Plan ka invoice chahiye",
            "Premium Font pack cancel karna hai",
            "mera order kahan hai",
        ],
    )
    def test_answers_that_reuse_the_agents_words_are_not_echo(self, user_said):
        assert not looks_like_echo(user_said, SAID_WITH_ORDERS)

    @pytest.mark.parametrize(
        "echo",
        [
            "Graphics Tablet Pro ship ho chuka hai aur Meridian Suite Team Plan deliver ho gaya hai",
            "graphics tablet pro ship ho chuka hai",
            "आपके orders की details ये है Premium font template pack अभी processing में है",
        ],
    )
    def test_real_echo_is_still_caught(self, echo):
        assert looks_like_echo(echo, SAID_WITH_ORDERS)

    def test_tiny_sound_outlines_do_not_count_as_shared_words(self):
        from app.voice.text_utils import _word_coverage

        # "ka" (k) and "status" (sts) only 'match' Ek (k) and Suite (st) by accident
        assert _word_coverage("ka status batao", SAID_WITH_ORDERS) < 0.75

    def test_echo_window_is_short_so_a_normal_reply_is_never_second_guessed(self):
        from app.voice.session import _ECHO_WINDOW_SECONDS

        assert _ECHO_WINDOW_SECONDS <= 2.0
