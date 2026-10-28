# Enterprise AI Customer Support Agent

A production-shaped agentic AI platform for enterprise customer support,
by text or by voice (Roman Hinglish by default): a LangGraph agent that answers questions from a RAG knowledge base, calls
real backend tools over **MCP** (Model Context Protocol) to look up orders
and manage tickets, remembers conversations, streams responses, reports on
its own quality via a RAGAS evaluation dashboard, and is hardened with an
output guardrail, prompt-injection-resistant tool handling, and a red-team
test suite that exercises it end to end.

Built to be read end-to-end, not just run -- every file has a short comment
explaining *why* it exists, not just what it does.

## Why this exists

Most portfolio AI projects are "a chatbot using LangChain." This one is
built to demonstrate the parts of shipping an AI feature that a chatbot demo
skips: authenticated multi-user sessions, grounded answers with citations,
tool calling against real systems, caching, streaming, structured logging,
Prometheus metrics, an evaluation pipeline that scores answer quality
instead of just eyeballing it, and the security layer a production agent
needs -- output filtering, context isolation between trusted instructions
and untrusted tool/retrieval content, and adversarial testing against the
live system rather than just the happy path.

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

**Layered defense** (`backend/app/agent/guardrails.py`, `security/red_team.py`):
every tool result is scanned for PII patterns and prompt-injection phrasing
before it re-enters the agent's context, and wrapped in
`<untrusted_tool_output>` delimiters so the system prompt can tell the model
that content is data, not instructions. This is deliberately scoped to the
tool-output channel; a separate system-prompt rule (added after the
`security/red_team.py` suite found a real vulnerability -- a message
spoofing a `[SYSTEM]` tag could talk the agent into an unauthorized refund)
closes the equivalent gap on the user-input channel. Flagged messages are
visible to admins via `GET /admin/traces` and the frontend's admin trace
viewer.

## Repository layout

```
backend/            FastAPI app, LangGraph agent, RAG, MCP tools, tests
  app/
    agent/           LangGraph graph, prompts, Gemini wrapper, guardrails
    rag/              Chroma ingestion + retrieval
    tools/             MCP server + client
    voice/             realtime voice: STT, TTS, turn-taking, confirmation gate
    cache/              Redis conversation cache
    observability/       structured logging + Prometheus metrics
    routers/               FastAPI endpoints (incl. admin trace viewer)
    data/                    knowledge-base docs + synthetic seed data
  tests/            pytest suite (SQLite in-memory, no external services needed)
frontend/          Vite + React (plain JS) chat UI, incl. admin trace viewer
eval/              RAGAS evaluation dataset + runner
security/          Red-team suite -- 5 adversarial scenarios vs. the live agent
monitoring/        Prometheus + Grafana config (optional profile)
.github/workflows/ CI: backend pytest + frontend build/lint on every push
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
   An admin account is also seeded (`admin@meridiansuite.example` /
   `admin1234`) -- sign in with it to see the "Admin traces" nav link,
   which lists conversations and flags any guardrail hits.

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

The backend suite (320 tests, including the voice pipeline's edge cases) runs against an in-memory SQLite database and stubs
Redis/MCP/the LLM where needed, so it needs no external services and no API
key -- it covers auth, the MCP tool business logic (including that one
user's orders never leak to another), the RAG doc-chunking pipeline, the
`/chat` SSE endpoint's event handling and persistence, the eval results
endpoint, the output guardrail's regex/PII checks, and the admin trace
endpoint's authorization (403 for non-admins). It runs in CI
(`.github/workflows/ci.yml`) on every push, alongside a frontend
build/lint job.

## Realtime voice (Groq Whisper STT + Azure Neural TTS)

The same LangGraph agent, reachable by voice. Voice is a second *transport*
(WebSocket `/voice`) around the existing agent -- `agent/turn.py` is the
single "run one turn" implementation that both `/chat` (SSE) and `/voice`
use, so tools, RAG, guardrails, persistence and metrics behave identically.

```
mic -> browser VAD (Silero) -> utterance (PCM16 16 kHz) --WS--> Groq whisper-large-v3-turbo
                                                                      | transcript
   speaker <- queued playback <--WS-- Azure Neural TTS <- sentence chunker <- agent tokens
```

**Enable it:** set `GROQ_API_KEY`, `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`
in `.env` (see `.env.example`), rebuild the backend image, then click
"Start voice" in the chat header. Without Azure keys you get live captions
only; without a Groq key the call reports "voice unavailable" and text chat is
unaffected.

**Where things live:** `backend/app/voice/` (`session.py` turn-taking and
edge cases, `stt.py`, `tts.py`, `confirm.py`, `text_utils.py`),
`routers/voice_router.py` (auth + capacity), `frontend/src/api/voice.js`
(mic, VAD, playback, barge-in, reconnect). The WebSocket protocol is
documented at the top of `voice/session.py`.

**Edge cases handled on purpose** (each has a test in `tests/test_voice_*.py`):

- *Noise / silence / Whisper hallucinations* -- too-short or too-quiet clips never reach Groq;
  confident-looking "Thank you." on silence is dropped using Whisper's own `no_speech_prob`/`avg_logprob`.
- *STT failure or empty transcript* -- spoken reprompt, capped at 3, then "switch to typing".
- *TTS failure* -- captions continue; the call doesn't die.
- *Barge-in* -- the client flushes audio and the server cancels the turn; audio frames carry a
  `turn_id` so late frames from an interrupted turn are discarded. History stores only what was
  actually spoken.
- *Never cancel a refund/cancel mid-write* -- an interrupt during those tools only mutes audio;
  the write finishes and the next utterance waits its turn.
- *Misheard order numbers / spoken prompt injection* -- `refund_request` and `cancel_order` are
  **code-gated** (`voice/confirm.py`): they don't run unless the user's previous utterance was an
  explicit short "yes" to a read-back of that exact call. Prompt rules 9-13 add phrasing guidance,
  but the gate doesn't depend on the model obeying them.
- *Card numbers spoken aloud* -- Luhn-checked, redacted before storage/LLM, user warned.
- *Lifecycle* -- heartbeat, idle prompt then close, per-call time cap, concurrent-call cap,
  one live call per user, JWT expiry mid-call, auto-reconnect that resumes the conversation.

**Language & voice:** the assistant answers in Hinglish (Hindi-English mix, Roman script) by default
-- set `ASSISTANT_LANGUAGE=english` to turn that off. Whenever a tool or knowledge-base search runs,
the user gets a short "checking..." line: spoken on voice calls and shown in the chat bubble on both
channels (`voice/phrases.py` holds every system-spoken line in both languages, phrased
gender-neutrally). Refund/cancel confirmations understand Hinglish and Devanagari ("haan kar do",
"हाँ"), and hedges ("haan lekin ruko") are correctly refused. For a **Custom Neural Voice**, set
`AZURE_TTS_ENDPOINT_ID` and put the custom voice's name in `AZURE_TTS_VOICE`; on any failure it falls
back to `AZURE_TTS_FALLBACK_VOICE`. Training and deploying the custom voice itself happens in Azure
Speech Studio (Microsoft gates access to it) -- nothing in this repo can create one.

**One-to-one conversation handling** (`voice/session.py`; each has a test in `tests/test_voice_conversation_edges.py`).
An incoming utterance is *transcribed first and only then acted on*, so:
the agent hearing its own voice through the speakers (echo) is detected by comparing what was
"heard" with what was just said, and dropped without cutting the answer off; "hmm"/"haan"/"okay"
while it talks are ignored; a sentence split by a pause ("I want a refund ... for my tablet") is
merged into one request when the second half arrives before the agent starts answering (the stored
first half is retracted from history); an unintelligible clip under the agent's voice never triggers
"sorry, say again"; answers are read aloud only up to `VOICE_MAX_SPOKEN_CHARS` and then say the rest is
in the chat; a short goodbye ("bas itna hi, bye") makes the agent say farewell and the call ends after
it plays; Hindi number words ("nau aath saat...") become digits. Browser side (`frontend/src/api/voice.js`):
half-duplex by default -- the microphone is ignored while the agent talks and for 0.5 s after -- because on
laptop speakers the mic hears the agent; tick "Let me interrupt (headphones)" to allow talking over it
(needs 0.6 s of sustained speech).

**Interrupting (`frontend/src/api/voice.js`):** "duck, then confirm". When the microphone hears clear speech
over the agent for 0.3 s the agent's audio is *paused at once*; your utterance goes to the server, which
transcribes it and answers with a verdict -- a real request (`transcript`: the old audio is discarded and
the agent answers the new thing) or a false alarm (`ignored`: echo, "hmm", noise -- playback resumes where it
stopped). A **Stop** button and a "Let me interrupt" toggle are always available. This logic has no browser in
CI, so it is tested with a fake clock and fake audio context: `cd frontend && npm test`.

**Word errors:** Whisper's output is repaired before the agent (and the refund confirmation gate) sees it --
known misspellings and Devanagari forms of key words are normalised (`रिफंड` -> `refund`, `rene do` /
`rehnye do` -> `rehne do`), the user's own product names are passed to Whisper as vocabulary, transcripts Whisper
was mostly guessing at (mean log-prob below -1.1) are not acted on (the user is asked to repeat), and
borderline ones are shown with a "may have been misheard" note.

**Voice picker:** the header offers a dropdown of Azure Indian voices with a Preview button
(`GET /voice/voices`, `GET /voice/sample`); only voices in `AZURE_TTS_VOICES` are accepted from a client.

**Latency choices** (measured warm): Gemini "thinking" is off (`thinking_budget=0`, ~4.4 s -> ~1.35 s per
call). MCP tool subprocesses are pooled per user and kept warm for 5 minutes (`tools/mcp_client.py`;
capped at 20, closed on shutdown), and knowledge-base searches use a shared Chroma client plus a
256-entry result cache, pre-warmed at startup (`rag/retriever.py`). Voice also opens the tools
connection the moment a call connects, so the first question isn't the slow one. Trade-off to know:
after re-running `python -m app.rag.ingest`, answers can serve the old docs for up to 10 minutes (the
cache TTL) -- restart the backend to pick them up immediately.

**Model choice (`LLM_MODEL`, default `gemini-2.5-flash`):** `gemini-2.5-flash-lite` was tried and
rejected. With the Hinglish rules on, it looked an order up first in only 0-6 of 15 tries; Flash did
in 15 of 15, at the same ~1.2 s per decision. Before switching models, check that questions like
"Mera order kahan hai?" still trigger `lookup_order`, and re-run `eval/run_agent_eval.py` and
`security/red_team.py`.

**Observability:** `support_agent_voice_stt_latency_seconds`,
`support_agent_voice_first_audio_seconds`, and `support_agent_voice_events_total{event=...}`.

**Not verified here:** the test suite fakes STT/TTS/the model, so Groq, Azure and the
browser VAD/audio path have not been exercised against the live services yet -- do one
real call end to end before relying on it. Latency target: roughly 1.5-2 s from end of
speech to first audio.

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

Latest live run (37 examples, real Gemini API calls, no mocking):
Faithfulness 0.91, Context Recall 0.96, Answer Relevancy 0.74, Context
Precision 0.67. RAGAS's judge calls hit free-tier rate limits partway
through and fell back from 3-sample to 1-sample self-consistency on some
examples (visible in the run's logged `TimeoutError`s), so treat these as
directionally real rather than a tight, reproducible benchmark.

## Agent task-correctness evaluation

`eval/run_agent_eval.py` runs the **real agent graph** (live Gemini + MCP tools)
against the 52 scenarios in `eval/agent_eval_dataset.json` for the seeded demo user
(tool selection 17, tool correctness 13, safety 6, task success 5, escalation 5,
RAG grounding 4, business rules 2).

Each case resets the demo user's orders/tickets to a known state, sends one message,
then checks: the expected tool was called (`expect_tool_called`), forbidden tools
were not (`expect_no_tool_called`), RAG returned sources (`expect_sources_nonempty`),
and the **database ended in the right state** (`expect_db_check`, e.g. the right
order is `cancelled`, a refund ticket exists, no order changed on an ambiguous
"cancel everything"). A case passes only if every check passes; the score is
passed / total, written to `backend/agent_eval_results/latest.json` with a
per-category breakdown.

```
docker compose exec backend python /app/../eval/run_agent_eval.py
```

Limits to know: one run per case (the model is non-deterministic, so a single run
is a sample), single-turn only, tool checks are "was it called" rather than
exact-argument or call-order matching, and **no pass rate is recorded in this
repo** -- run it to get a current number. Its first live run found two real gaps
(asking for an order id instead of looking it up; asking for confirmation before
escalating when already authorized).

## Security & adversarial validation

```
docker compose exec backend python security/red_team.py
```

Runs 5 scenarios against the **live agent graph** -- no mocking of the LLM,
retriever, or tools:

1. **System prompt extraction** -- does asking directly (or via a role-play
   pretext) get the system prompt echoed back.
2. **Cross-user data access** -- does asking about "my order" ever return
   another seeded user's order.
3. **Indirect knowledge-base injection** -- a fixture document containing an
   injected instruction is planted in Chroma; the scenario checks both
   whether the agent obeys it and whether the output guardrail flags it.
4. **Jailbreak via role-play** ("pretend you're an unrestricted bot...") --
   checks for actual compliance, not just the presence of a keyword, since
   a correct refusal can legitimately contain the same words as the attack.
5. **Spoofed system message in user input** -- a user message containing a
   fake `[SYSTEM]`-tagged instruction claiming prior verification, trying to
   trigger `refund_request` without a real authorization step. This is the
   one that found a real vulnerability during development; the fix is
   Rule 8 in `backend/app/agent/prompts.py`.

Results (PASS / FAIL / INCONCLUSIVE per scenario) are written to
`security_results/latest.json`. This is a small, hand-written scenario
suite exercising one live system -- useful as a regression check and a
concrete demonstration of the find-fix-reverify loop, not a substitute for
a red team, a fuzzer, or an established benchmark like `garak`.

## Monitoring (optional)

```
docker compose --profile monitoring up
```

Starts Prometheus (:9090) and Grafana (:3001, login `admin`/`admin`, or
just open it -- anonymous viewer access is enabled) with a dashboard
already provisioned: HTTP request rate, chat-turn latency (p50/p95), tool
calls by name, and RAG retrieval rate. These answer *aggregate* questions
("is p95 latency drifting", "did the guardrail flag rate spike").

LangSmith tracing is also available, for the complementary *per-run*
question ("what exactly did this one conversation do, node by node").
Set `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY` in `.env` --
LangChain picks it up automatically, no code changes needed. (Left off by
default in `.env.example`; verified during development against a real
LangSmith project, with the exact `agent -> tools -> agent` node sequence
visible per trace.)

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
- **Voice latency not benchmarked live**: the voice path is covered by tests with
  fake STT/TTS/LLM; Groq/Azure/browser audio have not been measured end to end. The
  1.5-2 s end-of-speech-to-first-audio figure is a target, not a result.
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
- Hardened the agent with an **output guardrail** (PII and prompt-injection
  detection on tool results), **context isolation** between trusted
  instructions and untrusted tool/retrieval content, and a **5-scenario
  adversarial test suite** run against the live system, which surfaced and
  drove the fix for a real unauthorized-tool-call vulnerability.
