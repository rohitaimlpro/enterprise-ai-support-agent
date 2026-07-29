"""
Scores the RAG pipeline against eval/eval_dataset.json using RAGAS:
Faithfulness, Answer Relevancy, Context Precision, Context Recall.

This evaluates retrieval + grounded answering directly (not the full
tool-using agent -- ticket/order tools have nothing to do with RAG
quality), so each example: retrieves context with the same retriever the
agent uses, generates an answer grounded in that context, then RAGAS
judges the (question, answer, context, reference-answer) tuple.

Needs a real GOOGLE_API_KEY (used both to generate answers and, via
RAGAS's LLM-as-judge metrics, to score them). Run from the backend venv:

    cd backend && python ../eval/run_eval.py
    # or inside the backend container:
    docker compose exec backend python /app/../eval/run_eval.py

Writes eval/results/latest.json, which the /admin/eval endpoint and the
frontend's Eval Dashboard page read.
"""

import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

# ragas.llms.base unconditionally imports ChatVertexAI from
# langchain_community, but the langchain-community release compatible with
# this project's langchain-core version dropped that shim (Vertex AI moved
# to the standalone langchain-google-vertexai package). This project never
# uses Vertex AI -- inject a harmless stub so the import succeeds instead
# of pulling in an unrelated Google Cloud SDK dependency.
_vertexai_stub = types.ModuleType("langchain_community.chat_models.vertexai")


class _StubChatVertexAI:  # pragma: no cover - never instantiated
    pass


_vertexai_stub.ChatVertexAI = _StubChatVertexAI
sys.modules.setdefault("langchain_community.chat_models.vertexai", _vertexai_stub)

eval_dir = Path(__file__).resolve().parent
# Locally, eval/ and backend/ are siblings under the repo root. Inside the
# Docker image, this script runs mounted at /app/eval and the backend
# package lives directly at /app (backend/'s contents, not backend/
# itself) -- BACKEND_DIR is set there so both layouts resolve correctly.
BACKEND_DIR = Path(os.environ.get("BACKEND_DIR", str(eval_dir.parent / "backend")))
sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from ragas import EvaluationDataset, evaluate  # noqa: E402
from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: E402
from ragas.llms import LangchainLLMWrapper  # noqa: E402
from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness  # noqa: E402

from app.agent.llm import get_llm  # noqa: E402
from app.rag.retriever import get_embeddings, retrieve  # noqa: E402

EVAL_DATASET_PATH = eval_dir / "eval_dataset.json"
# Written inside the backend package root (not eval/) so it lands in the
# same place -- backend/eval_results/ locally, /app/eval_results/ in
# Docker -- that app/routers/eval_router.py reads from. See that file.
RESULTS_DIR = BACKEND_DIR / "eval_results"

ANSWER_PROMPT = """Answer the user's question using ONLY the context below. \
Be concise. If the context doesn't contain the answer, say you don't know.

Context:
{context}

Question: {question}
"""


def generate_answer(question: str, contexts: list[str]) -> str:
    llm = get_llm()
    prompt = ANSWER_PROMPT.format(context="\n\n".join(contexts), question=question)
    response = llm.invoke(
        [SystemMessage(content="You are a helpful support assistant."), HumanMessage(content=prompt)]
    )
    return response.content


def build_dataset(examples: list[dict]) -> EvaluationDataset:
    rows = []
    for example in examples:
        retrieved = retrieve(example["question"], k=4)
        contexts = [r["snippet"] for r in retrieved]
        answer = generate_answer(example["question"], contexts)
        rows.append(
            {
                "user_input": example["question"],
                "response": answer,
                "retrieved_contexts": contexts,
                "reference": example["ground_truth"],
            }
        )
    return EvaluationDataset.from_list(rows)


def run() -> dict:
    with open(EVAL_DATASET_PATH, encoding="utf-8") as f:
        examples = json.load(f)

    dataset = build_dataset(examples)

    judge_llm = LangchainLLMWrapper(get_llm())
    judge_embeddings = LangchainEmbeddingsWrapper(get_embeddings())

    result = evaluate(
        dataset,
        metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()],
        llm=judge_llm,
        embeddings=judge_embeddings,
    )

    scores_df = result.to_pandas()
    metrics = {
        "faithfulness": float(scores_df["faithfulness"].mean()),
        "answer_relevancy": float(scores_df["answer_relevancy"].mean()),
        "context_precision": float(scores_df["context_precision"].mean()),
        "context_recall": float(scores_df["context_recall"].mean()),
    }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "num_examples": len(scores_df),
        "metrics": metrics,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    with open(RESULTS_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(json.dumps(payload, indent=2))
    return payload


if __name__ == "__main__":
    run()
