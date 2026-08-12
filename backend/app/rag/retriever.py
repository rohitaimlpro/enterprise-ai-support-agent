"""
Retrieval over the knowledge-base docs stored in Chroma.

Also exposes `search_knowledge_base` as a LangChain tool, so the agent
graph can call it exactly like the MCP-backed tools (create_ticket,
lookup_order, ...) -- from the agent's point of view, RAG is just another
tool it can decide to use.
"""

import json

from langchain_chroma import Chroma
from langchain_core.tools import tool
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.config import get_settings

EMBEDDING_MODEL = "models/gemini-embedding-001"
COLLECTION_NAME = "support_docs"


def get_embeddings() -> GoogleGenerativeAIEmbeddings:
    settings = get_settings()
    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL, google_api_key=settings.google_api_key
    )


def get_vectorstore() -> Chroma:
    settings = get_settings()
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=settings.chroma_persist_dir,
    )


def retrieve(query: str, k: int = 4) -> list[dict]:
    """Returns up to k chunks as {title, source_file, snippet} dicts."""
    vectorstore = get_vectorstore()
    docs = vectorstore.similarity_search(query, k=k)
    return [
        {
            "title": doc.metadata.get("title", "Untitled"),
            "source_file": doc.metadata.get("source_file", "unknown"),
            "snippet": doc.page_content.strip(),
        }
        for doc in docs
    ]


@tool
def search_knowledge_base(query: str) -> str:
    """Search Meridian Suite's help-center articles, policies, and pricing
    guide. Use this for any question about how the product works, pricing,
    refunds, billing, or troubleshooting. Always prefer this over answering
    from memory so the answer can be grounded and cited.
    """
    results = retrieve(query)
    if not results:
        return json.dumps({"sources": [], "context": "No relevant documents found."})

    context = "\n\n".join(
        f"[{i + 1}] ({r['title']}, {r['source_file']}): {r['snippet']}"
        for i, r in enumerate(results)
    )
    return json.dumps({"sources": results, "context": context})
