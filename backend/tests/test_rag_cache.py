"""Knowledge-base search caching and warm-up (rag/retriever.py)."""

from types import SimpleNamespace

import pytest

import app.rag.retriever as retriever


class FakeStore:
    def __init__(self, fail_first=False):
        self.calls = 0
        self.fail_first = fail_first

    def similarity_search(self, query, k=4):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("stale index")
        return [
            SimpleNamespace(
                metadata={"title": "Refund Policy", "source_file": "refund_policy.md"},
                page_content=f" answer to {query} ",
            )
        ]


@pytest.fixture(autouse=True)
def clean_cache():
    retriever.clear_cache()
    yield
    retriever.clear_cache()


@pytest.fixture()
def store(monkeypatch):
    s = FakeStore()
    builds = []
    monkeypatch.setattr(retriever, "get_vectorstore", lambda: builds.append(1) or s)
    s.builds = builds
    return s


def test_repeat_query_is_served_from_cache(store):
    first = retriever.retrieve("What is your refund policy?")
    second = retriever.retrieve("what is your REFUND policy")  # same after normalizing
    assert first == second and store.calls == 1


def test_different_query_or_k_is_a_separate_entry(store):
    retriever.retrieve("refund policy")
    retriever.retrieve("shipping times")
    retriever.retrieve("refund policy", k=2)
    assert store.calls == 3


def test_vector_store_client_is_built_once_and_shared(store):
    retriever.retrieve("one")
    retriever.retrieve("two")
    retriever.retrieve("three")
    assert len(store.builds) == 1


def test_caller_mutating_results_cannot_poison_the_cache(store):
    r = retriever.retrieve("refund policy")
    r[0]["snippet"] = "TAMPERED"
    assert retriever.retrieve("refund policy")[0]["snippet"] == "answer to refund policy"


def test_cache_and_client_expire_after_ttl(store, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(retriever.time, "monotonic", lambda: now[0])
    retriever.retrieve("refund policy")
    now[0] += retriever.CACHE_TTL_SECONDS + 1  # someone may have re-ingested meanwhile
    retriever.retrieve("refund policy")
    assert store.calls == 2 and len(store.builds) == 2


def test_empty_results_are_not_cached(monkeypatch):
    calls = []

    class Empty:
        def similarity_search(self, query, k=4):
            calls.append(1)
            return []

    monkeypatch.setattr(retriever, "get_vectorstore", lambda: Empty())
    assert retriever.retrieve("anything") == []
    retriever.retrieve("anything")
    assert len(calls) == 2  # a transient failure must not stick


def test_broken_cached_client_is_rebuilt_and_retried(monkeypatch):
    stores = [FakeStore(fail_first=True), FakeStore()]
    monkeypatch.setattr(retriever, "get_vectorstore", lambda: stores.pop(0))
    assert retriever.retrieve("refund policy")[0]["title"] == "Refund Policy"


def test_cache_size_is_bounded(store, monkeypatch):
    monkeypatch.setattr(retriever, "RESULT_CACHE_MAX", 3)
    for i in range(10):
        retriever.retrieve(f"question {i}")
    assert len(retriever._results) == 3


def test_warm_up_fills_cache_and_never_raises(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(retriever, "get_vectorstore", lambda: s)
    assert retriever.warm_up(("a", "b", "c")) == 3
    retriever.retrieve("a")
    assert s.calls == 3  # served from the warmed cache

    def boom(*a, **k):
        raise RuntimeError("no api key")

    monkeypatch.setattr(retriever, "retrieve", boom)
    assert retriever.warm_up(("x", "y")) == 0  # logged, not raised


def test_search_tool_output_shape_unchanged(store):
    import json

    out = json.loads(retriever.search_knowledge_base.invoke({"query": "refund policy"}))
    assert out["sources"][0]["title"] == "Refund Policy"
    assert out["context"].startswith("[1] (Refund Policy, refund_policy.md):")
