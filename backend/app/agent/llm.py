"""Gemini chat model wrapper, shared by the agent graph."""

from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings

# "Flash" models are fast and cheap enough for a support chatbot's latency
# budget; swap for "gemini-2.0-pro" if you want stronger reasoning at the
# cost of latency.
CHAT_MODEL = "gemini-2.0-flash"


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
