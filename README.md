# Enterprise AI Customer Support Agent

A production-shaped agentic AI platform for enterprise customer support: a
LangGraph agent that answers questions from a RAG knowledge base, calls
real backend tools over **MCP** (Model Context Protocol) to look up orders
and manage tickets, remembers conversations, streams responses, and reports
on its own quality via a RAGAS evaluation dashboard.

Built to be read end-to-end, not just run -- every file has a short comment
explaining *why* it exists, not just what it does.

## Why this exists

Most portfolio AI projects are "a chatbot using LangChain." This one is
built to demonstrate the parts of shipping an AI feature that a chatbot demo
skips: authenticated multi-user sessions, grounded answers with citations,
tool calling against real systems, caching, streaming, structured logging,
Prometheus metrics, and an evaluation pipeline that scores answer quality
instead of just eyeballing it.

## Architecture

```
React (Vite, JS) ──HTTP/SSE──> FastAPI backend
                                   │
                     ┌─────────────┼──────────────┐
                     │             │              │
               JWT auth       LangGraph agent   Prometheus /metrics
                     │             │
                Postgres      ┌────┴─────┐
                (users,       │          │
                 orders,   RAG (Chroma  MCP tool server (subprocess,
                 tickets,   embedded,    stdio transport) exposing:
                 messages)  Gemini       create_ticket, lookup_order,
                     │      embeddings)  cancel_order, refund_request,
                Redis                   get_invoice, schedule_callback,
                (short-term              escalate_issue
                 conv. cache)
```

**Agent loop** (`backend/app/agent/graph.py`): a LangGraph `StateGraph` with
two nodes -- `agent` (asks Gemini, with tools bound, what to do next) and
`tools` (executes whatever it asked for) -- looping until the model replies
with plain text instead of a tool call.

**RAG** (`backend/app/rag/`): markdown docs in `backend/app/data/docs/` are
chunked and embedded into a local, embedded Chroma index. Retrieval is
exposed to the agent as a regular tool (`search_knowledge_base`), so from
the agent's point of view, "search the docs" and "look up an order" are the
same kind of action -- it decides when to use each.

**MCP tools** (`backend/app/tools/`): `mcp_server.py` is a real MCP server
(built with the official `mcp` SDK's `FastMCP`) exposing 7 tools backed by
Postgres. `mcp_client.py` spawns it as a subprocess over stdio for each chat
turn, scoped to the current user via an environment variable (never an
LLM-supplied argument -- see the comment in `mcp_server.py` for why).

**Escalation**: instead of a numeric confidence threshold, the agent is
instructed to call `escalate_issue` when it can't confidently answer -- this
opens a high-priority ticket and tells the user a human will follow up.

## Repository layout

```
backend/            FastAPI app, LangGraph agent, RAG, MCP tools, tests
  app/
    agent/           LangGraph graph, prompts, Gemini wrapper
    rag/              Chroma ingestion + retrieval
    tools/             MCP server + client
    cache/              Redis conversation cache
    observability/       structured logging + Prometheus metrics
    routers/               FastAPI endpoints
    data/                    knowledge-base docs + synthetic seed data
  tests/            pytest suite (SQLite in-memory, no external services needed)
frontend/          Vite + React (plain JS) chat UI
eval/              RAGAS evaluation dataset + runner
monitoring/        Prometheus + Grafana config (optional profile)
```

## Running it

1. **Get a Gemini API key** (free tier is fine): https://aistudio.google.com/app/apikey
2. Copy the env file and fill in your key:
   ```
   cp .env.example .env
   # edit .env, set GOOGLE_API_KEY and JWT_SECRET_KEY
   ```
3. Start the app:
   ```
   docker compose up --build
   ```
   This starts `backend` (FastAPI, :8000), `frontend` (Vite, :5173),
   `postgres`, and `redis`. Prometheus/Grafana are **not** started by
   default -- see [Monitoring](#monitoring-optional) below.
4. Seed a demo user with sample orders/tickets, and ingest the knowledge
   base docs into Chroma:
   ```
   docker compose exec backend python -m app.data.seed
   docker compose exec backend python -m app.rag.ingest
   ```
5. Open http://localhost:5173 and sign in with the seeded demo account
   (`demo@meridiansuite.example` / `demo1234`), or register a new one.

Try asking:
- *"How do I upgrade my subscription?"* -- pulls from the knowledge base, cites sources.
- *"Where is my order?"* -- calls the `lookup_order` MCP tool.
- Something out of scope -- the agent calls `escalate_issue` and opens a ticket.

## Running the backend without Docker

```
cd backend
python -m venv .venv && .venv\Scripts\activate   # or source .venv/bin/activate
pip install -r requirements.txt
# point DATABASE_URL/REDIS_HOST at localhost in .env, then:
uvicorn app.main:app --reload
```

## Tests

```
cd backend
pytest
```

The suite (19 tests) runs against an in-memory SQLite database and stubs
Redis/MCP/the LLM where needed, so it needs no external services and no API
key -- it covers auth, the MCP tool business logic (including that one
user's orders never leak to another), the RAG doc-chunking pipeline, the
`/chat` SSE endpoint's event handling and persistence, and the eval
results endpoint.

## Evaluation dashboard

`eval/run_eval.py` scores the RAG pipeline (retrieval + grounded answering)
against `eval/eval_dataset.json` using **RAGAS**: Faithfulness, Answer
Relevancy, Context Precision, Context Recall. It needs a real
`GOOGLE_API_KEY` since RAGAS uses an LLM-as-judge.

37 of the questions are phrased after real customer-support queries from
the public [Bitext Customer Support LLM Chatbot dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset)
(CDLA-Sharing-1.0), covering all 26 of its support intents (cancel/change/
place/track order, account management, payments & invoices, refunds,
shipping, escalation, feedback...) -- each entry's `source_intent` field
names which. The ground-truth answers are our own, written against this
project's knowledge-base docs (the dataset's own answers reference a
different, generic company). The knowledge base itself was expanded to
5 new docs (`account_management.md`, `order_management.md`,
`payments_and_invoices.md`, `contact_and_escalation.md`,
`feedback_and_reviews.md`) specifically to cover that intent taxonomy.

```
docker compose exec backend python eval/run_eval.py
```

Results are written to a `latest.json` inside the backend container and
served at `GET /admin/eval`; view them at http://localhost:5173 → *Eval
dashboard*.

## Monitoring (optional)

```
docker compose --profile monitoring up
```

Starts Prometheus (:9090) and Grafana (:3001, login `admin`/`admin`, or
just open it -- anonymous viewer access is enabled) with a dashboard
already provisioned: HTTP request rate, chat-turn latency (p50/p95), tool
calls by name, and RAG retrieval rate. LangSmith tracing is also
available for free -- just set `LANGCHAIN_TRACING_V2=true` and
`LANGCHAIN_API_KEY` in `.env`; LangChain picks it up automatically, no
code changes needed.

## Dependency notes

The LangChain/LangGraph/MCP ecosystem moves fast and unpinned installs can
easily pull incompatible combinations (e.g. `langchain-mcp-adapters`
requires `mcp<2.0`, which is where the `FastMCP` helper this project's MCP
server uses still lives -- `mcp>=2.0` renamed/restructured it). Every
version in `backend/requirements.txt` was installed together and verified
against the test suite. If you bump one package, re-run `pytest` and
`pip check` before assuming the rest still lines up.

`eval/run_eval.py` also works around a real upstream issue: `ragas`
unconditionally imports a `ChatVertexAI` shim from `langchain-community`
that recent `langchain-community` releases dropped (Vertex AI moved to its
own package). The script stubs that module out at import time rather than
pinning to an old `langchain-community`, which would conflict with the
`langchain-core` 1.x this project otherwise uses -- see the comment at the
top of that file.

## Known simplifications (next steps for real production use)

- **Migrations**: tables are created with `Base.metadata.create_all()` on
  startup instead of Alembic migrations -- fine for a demo, not for a
  schema that needs to evolve safely in production.
- **Cross-turn tool memory**: conversation history replayed into the model
  is user/assistant text only; each turn's own tool-call loop stays
  in-memory for that turn rather than being replayed on subsequent turns.
- **Single eval snapshot**: 37 examples is enough to prove the pipeline
  works end-to-end and exercises real-world question phrasing across all
  26 support intents; a production eval suite would run into the hundreds
  and track score trends over time, not just a single latest snapshot.

## Resume bullet points

- Built a production-shaped enterprise AI customer support platform using
  **FastAPI, LangGraph, MCP, and the Gemini API**, with authenticated
  multi-turn conversations and streaming responses over SSE.
- Developed a **RAG pipeline** over company documentation using **Chroma**,
  delivering cited, context-aware answers and exposing retrieval as a
  tool the agent chooses when to invoke.
- Integrated **MCP-based tools** for order lookup, cancellation, refunds,
  invoices, ticket creation, callback scheduling, and human escalation,
  running as a real MCP server over stdio.
- Implemented **JWT auth, Redis-backed conversation caching, Dockerized
  deployment, and a RAGAS-based evaluation dashboard** (Faithfulness,
  Answer Relevancy, Context Precision, Context Recall) alongside
  **Prometheus/Grafana** monitoring of latency, tool usage, and retrieval
  volume.
