"""Gemini chat model wrapper, shared by the agent graph."""

from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings

# "Flash" models are fast and cheap enough for a support chatbot's latency
# budget. Pinned to the dated 2.5 release rather than "gemini-flash-latest"
# -- the latest alias can shift models under us without warning, and
# newer models add "thinking" output that complicates tool-call parsing.
CHAT_MODEL = "gemini-2.5-flash"


def get_llm(tools: list[BaseTool] | None = None) -> ChatGoogleGenerativeAI:
    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=CHAT_MODEL,
        google_api_key=settings.google_api_key,
        temperature=0.2,
    )
    if tools:
        llm = llm.bind_tools(tools)
    return llm
