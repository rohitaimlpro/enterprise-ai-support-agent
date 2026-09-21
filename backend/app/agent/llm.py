"""Gemini chat model wrapper, shared by the agent graph."""

from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings


def get_llm(tools: list[BaseTool] | None = None) -> ChatGoogleGenerativeAI:
    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        # Set with LLM_MODEL. Default is gemini-2.5-flash. "gemini-2.5-flash-lite" is a
        # little cheaper but measured much worse at the one thing this agent must do
        # reliably: calling its tools when told to, once it is also asked to reply in
        # Hinglish (0-6 of 15 order questions looked the order up, vs 15 of 15 on Flash
        # at the same ~1.2 s per decision). Pin a dated 2.5 release, never a "-latest"
        # alias -- an alias can shift models under us without warning.
        model=settings.llm_model,
        google_api_key=settings.google_api_key,
        temperature=0.2,
        # Gemini 2.5 "thinks" before answering by default, which measured at
        # ~4.4 s per call vs ~1.35 s with it off -- and a tool question makes
        # two calls. Latency matters most on the voice channel.
        thinking_budget=0,
    )
    if tools:
        llm = llm.bind_tools(tools)
    return llm
