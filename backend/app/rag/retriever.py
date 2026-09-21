"""
Retrieval over the knowledge-base docs stored in Chroma.

Also exposes `search_knowledge_base` as a LangChain tool, so the agent
graph can call it exactly like the MCP-backed tools (create_ticket,
lookup_order, ...) -- from the agent's point of view, RAG is just another
tool it can decide to use.
"""

import json
import re
import threading
import time
from collections import OrderedDict

from langchain_chroma import Chroma
from langchain_core.tools import tool
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.config import get_settings
from app.observability.logging_config import get_logger

logger = get_logger(__name__)

EMBEDDING_MODEL = "models/gemini-embedding-001"
COLLECTION_NAME = "support_docs"

# Every search used to build a fresh Chroma client + embeddings client and
# embed the query over the network (~1.2 s). Two things now avoid that:
#   1. a shared vector store, rebuilt every TTL so that a re-ingest done by
#      another process (`python -m app.rag.ingest`) is picked up -- a
#      long-lived Chroma client can keep serving stale vectors otherwise;
#   2. a small result cache keyed by the normalized query, so repeated
#      questions ("refund policy") skip the embedding call entirely.
# Both expire after the same TTL, which is therefore the worst-case delay
# before newly ingested docs appear in answers.
CACHE_TTL_SECONDS = 600
RESULT_CACHE_MAX = 256

# Common support topics, searched once at startup so the first real
# customer question doesn't pay for cold clients.
WARM_QUERIES = (
    "refund policy",
    "pricing plans",
    "shipping and delivery",
    "login problems",
    "cancel subscription",
    "invoices and payments",
)

_lock = threading.Lock()
_store: Chroma | None = None
_store_built_at = 0.0
_results: "OrderedDict[tuple[str, int], tuple[float, list[dict]]]" = OrderedDict()


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


def _normalize_query(query: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", query.lower()).split())


def _shared_vectorstore(force_new: bool = False) -> Chroma:
    global _store, _store_built_at
    with _lock:
        if force_new or _store is None or time.monotonic() - _store_built_at > CACHE_TTL_SECONDS:
            _store = get_vectorstore()
            _store_built_at = time.monotonic()
        return _store


def clear_cache() -> None:
    """Forget the shared client and cached results (tests; after re-ingest)."""
    global _store
    with _lock:
        _store = None
        _results.clear()


def _search(query: str, k: int) -> list[dict]:
    try:
        docs = _shared_vectorstore().similarity_search(query, k=k)
    except Exception:
        # A stale or broken cached client shouldn't fail the customer's
        # question: rebuild it and try once more.
        logger.warning("kb_search_retry_with_fresh_client", exc_info=True)
        docs = _shared_vectorstore(force_new=True).similarity_search(query, k=k)
    return [
        {
            "title": doc.metadata.get("title", "Untitled"),
            "source_file": doc.metadata.get("source_file", "unknown"),
            "snippet": doc.page_content.strip(),
        }
        for doc in docs
    ]


def retrieve(query: str, k: int = 4) -> list[dict]:
    """Returns up to k chunks as {title, source_file, snippet} dicts."""
    key = (_normalize_query(query), k)
    now = time.monotonic()
    with _lock:
        hit = _results.get(key)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            _results.move_to_end(key)
            return [dict(r) for r in hit[1]]  # copies: callers must not mutate the cache

    results = _search(query, k)
    if results:  # never cache "nothing found": it may just be a transient failure
        with _lock:
            _results[key] = (time.monotonic(), results)
            _results.move_to_end(key)
            while len(_results) > RESULT_CACHE_MAX:
                _results.popitem(last=False)
    return [dict(r) for r in results]


def warm_up(queries: tuple[str, ...] = WARM_QUERIES) -> int:
    """Pre-build the clients and pre-fill the cache. Blocking; run it in a
    thread. Failures are logged, never raised: warming is best-effort."""
    done = 0
    for q in queries:
        try:
            retrieve(q)
            done += 1
        except Exception:
            logger.warning("kb_warmup_failed query=%s", q, exc_info=True)
    return done


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
