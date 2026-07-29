"""
Loads every markdown file in app/data/docs, splits it into chunks, and
writes the chunks into Chroma. Run once after the containers are up:

    docker compose exec backend python -m app.rag.ingest

Re-running is a no-op unless you pass --force (or ingest(force=True)),
since embedding calls cost time/money and the docs rarely change.
"""

import argparse
import os

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "docs")


def _title_from_markdown(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def load_documents(docs_dir: str = DOCS_DIR) -> list[Document]:
    """Reads every *.md file in docs_dir and splits it into ~800-char chunks."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    documents: list[Document] = []

    for filename in sorted(os.listdir(docs_dir)):
        if not filename.endswith(".md"):
            continue
        path = os.path.join(docs_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

        title = _title_from_markdown(text, fallback=filename)
        for chunk in splitter.split_text(text):
            documents.append(
                Document(
                    page_content=chunk,
                    metadata={"title": title, "source_file": filename},
                )
            )

    return documents


def ingest(force: bool = False) -> int:
    # Imported lazily: this pulls in chromadb + the Gemini embeddings client,
    # which aren't needed just to parse/split the markdown docs (and aren't
    # installed in lightweight test environments).
    from app.rag.retriever import get_vectorstore

    vectorstore = get_vectorstore()
    existing_ids = vectorstore.get()["ids"]

    if existing_ids and not force:
        print(
            f"Chroma already has {len(existing_ids)} chunks -- skipping "
            "(pass --force to re-ingest)."
        )
        return len(existing_ids)

    if existing_ids and force:
        vectorstore.delete(ids=existing_ids)

    docs = load_documents()
    vectorstore.add_documents(docs)
    print(f"Ingested {len(docs)} chunks from {DOCS_DIR}")
    return len(docs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-ingest even if already populated")
    args = parser.parse_args()
    ingest(force=args.force)
