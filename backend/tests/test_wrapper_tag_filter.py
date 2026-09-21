"""The model sometimes echoes the guardrail's <untrusted_tool_output> wrapper
into its reply. That's internal plumbing (and would be read aloud on a voice
call), so it's stripped from the stream -- including when a tag is split
across streamed chunks."""

import asyncio
from types import SimpleNamespace

from app.agent.prompts import get_system_prompt
from app.agent.turn import WrapperTagFilter, stream_turn


def feed_all(chunks):
    f = WrapperTagFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_removes_a_whole_tag():
    assert feed_all(["</untrusted_tool_output>\nAccording to policy"]) == "\nAccording to policy"


def test_removes_opening_tag_with_attributes():
    assert feed_all(['<untrusted_tool_output source="search_knowledge_base">Refunds in 14 days']) == "Refunds in 14 days"


def test_removes_a_tag_split_across_chunks_even_char_by_char():
    text = "Hello </untrusted_tool_output> world"
    assert feed_all(list(text)) == "Hello  world"
    assert feed_all(["Hello </untr", "usted_tool_", "output> world"]) == "Hello  world"


def test_case_insensitive():
    assert feed_all(["<UNTRUSTED_TOOL_OUTPUT>hi"]) == "hi"


def test_ordinary_angle_brackets_pass_through_untouched():
    assert feed_all(["I <3 this, and a < b > c"]) == "I <3 this, and a < b > c"
    assert feed_all(list("x <b>bold</b> y")) == "x <b>bold</b> y"


def test_held_back_text_is_not_lost_at_end_of_stream():
    assert feed_all(["Price <"]) == "Price <"
    assert feed_all(["cut off <untrusted_tool"]) == "cut off <untrusted_tool"


def test_stream_turn_strips_the_tag_from_token_events():
    def token(t):
        return {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content=t)}}

    class Graph:
        async def astream_events(self, state, version="v2"):
            for t in ["</untr", "usted_tool_output>", "\nAccording to our ", "Refund Policy, 14 din."]:
                yield token(t)

    async def collect():
        return "".join([e.text async for e in stream_turn(Graph(), {}) if e.kind == "token"])

    out = asyncio.run(collect())
    assert "untrusted" not in out and out.strip() == "According to our Refund Policy, 14 din."


def test_both_channels_tell_the_agent_to_look_orders_up_and_not_to_narrate_tools():
    for voice in (False, True):
        text = " ".join(get_system_prompt(voice=voice).split())
        assert "FIRST action must be to call lookup_order" in text and "Never ask for an order id" in text
        assert "never say tool or function names" in text
