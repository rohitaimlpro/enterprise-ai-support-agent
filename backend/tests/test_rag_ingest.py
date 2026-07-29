"""
Tests the doc-loading/splitting logic only (no Chroma, no API key needed --
that part is exercised manually via `python -m app.rag.ingest` once you
have a real GOOGLE_API_KEY).
"""

from app.rag.ingest import load_documents


def test_load_documents_finds_all_markdown_files():
    docs = load_documents()
    source_files = {d.metadata["source_file"] for d in docs}

    assert "refund_policy.md" in source_files
    assert "pricing_guide.md" in source_files
    assert "troubleshooting_login_issues.md" in source_files
    assert len(docs) > len(source_files)  # each file should split into >1 chunk on average


def test_chunks_carry_a_human_readable_title():
    docs = load_documents()
    refund_chunks = [d for d in docs if d.metadata["source_file"] == "refund_policy.md"]

    assert refund_chunks
    assert all(d.metadata["title"] == "Refund Policy" for d in refund_chunks)


def test_chunk_content_is_reasonably_sized():
    docs = load_documents()
    assert all(len(d.page_content) <= 900 for d in docs)  # 800 target + splitter overlap slack
