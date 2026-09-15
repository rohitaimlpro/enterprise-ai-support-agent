"""
Whole-agent behavioral evaluation: unlike eval/run_eval.py (RAG answer
quality only) and security/red_team.py (misuse resistance only), this
scores task correctness of the full tool-using agent -- did it pick the
right tool, target the right order, and actually produce the right
database side effect (not just say the right words).

Runs the real agent graph (same code path as a live chat turn) against
each example in eval/agent_eval_dataset.json, for the seeded demo user.

Needs a real GOOGLE_API_KEY, and Postgres/Redis reachable (same
requirements as security/red_team.py) -- easiest via:

    docker compose up -d postgres redis backend
    docker compose exec backend python -m app.data.seed
    docker compose exec backend python /app/../eval/run_agent_eval.py

Mutates the demo user's orders/tickets (cancelling, refunding, creating
tickets) as part of what it's testing -- so it resets those rows to a
known seed state before every run, making it safe to re-run repeatedly
rather than only working once against a freshly-seeded database.

Writes eval/agent_eval_results/latest.json.
"""

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

eval_dir = Path(__file__).resolve().parent
# Same BACKEND_DIR resolution as run_eval.py / red_team.py -- local dev
# finds backend/ as a sibling directory; inside Docker, BACKEND_DIR=/app.
BACKEND_DIR = Path(os.environ.get("BACKEND_DIR", str(eval_dir.parent / "backend")))
sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.messages import HumanMessage  # noqa: E402

from app.agent.graph import build_agent_graph  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Order, OrderStatus, Ticket, TicketPriority, TicketStatus, User  # noqa: E402
from app.routers.chat_router import _extract_text  # noqa: E402
from app.tools.mcp_client import mcp_tools_session  # noqa: E402

DATASET_PATH = eval_dir / "agent_eval_dataset.json"
RESULTS_DIR = BACKEND_DIR / "agent_eval_results"
DEMO_EMAIL = "demo@meridiansuite.example"

# Mirrors app/data/seed.py's demo orders exactly -- reset to this fixed
# state before every run so db-effect checks are reproducible instead of
# depending on whatever a previous run left behind.
SEED_ORDERS = [
    {"product_name": "Meridian Suite Team Plan (annual)", "status": OrderStatus.delivered, "amount": 280.0, "age_days": 40},
    {"product_name": "Graphics Tablet Pro", "status": OrderStatus.shipped, "amount": 249.99, "age_days": 2},
    {"product_name": "Premium Font & Template Pack", "status": OrderStatus.processing, "amount": 4.99, "age_days": 0},
]


def get_demo_user_id() -> str:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if not user:
            raise RuntimeError("Seed the demo user first: python -m app.data.seed")
        return user.id
    finally:
        db.close()


def reset_demo_data(user_id: str) -> None:
    """Deletes and recreates the demo user's orders/tickets so each run
    starts from the same known state, regardless of what a previous
    (destructive) run left behind."""
    db = SessionLocal()
    try:
        db.query(Order).filter(Order.user_id == user_id).delete()
        db.query(Ticket).filter(Ticket.user_id == user_id).delete()
        now = datetime.now(timezone.utc)
        for o in SEED_ORDERS:
            db.add(
                Order(
                    user_id=user_id,
                    product_name=o["product_name"],
                    status=o["status"],
                    amount=o["amount"],
                    order_date=now - timedelta(days=o["age_days"]),
                )
            )
        db.add(
            Ticket(
                user_id=user_id,
                subject="Sync not working across devices",
                description="Files stopped syncing between my laptop and desktop yesterday.",
                status=TicketStatus.open,
                priority=TicketPriority.medium,
                created_at=now - timedelta(days=1),
            )
        )
        db.commit()
    finally:
        db.close()


async def run_agent_turn(user_id: str, message: str) -> dict:
    """One full turn through the real agent graph -- same shape as
    chat_router.py's event_generator and red_team.py's helper, collected
    instead of streamed."""
    async with mcp_tools_session(user_id) as mcp_tools:
        graph = build_agent_graph(mcp_tools)
        state = {"messages": [HumanMessage(content=message)], "sources": [], "guardrail_flags": []}
        result = await graph.ainvoke(state)

    final_message = result["messages"][-1]
    tool_calls = []
    for m in result["messages"]:
        if getattr(m, "tool_calls", None):
            tool_calls.extend({"name": c["name"], "args": c["args"]} for c in m.tool_calls)

    return {
        "answer": _extract_text(final_message.content),
        "tool_calls": tool_calls,
        "sources": result.get("sources", []),
        "guardrail_flags": result.get("guardrail_flags", []),
    }


# ---------- db-effect check helpers ----------
# Each takes (user_id, arg) and returns (passed: bool, detail: str).


def _order_by_product(db, user_id: str, product_name: str) -> Order | None:
    return (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.product_name == product_name)
        .first()
    )


def _check_order_status(user_id: str, spec: str) -> tuple[bool, str]:
    product_name, expected_status = spec.rsplit(":", 1)
    db = SessionLocal()
    try:
        order = _order_by_product(db, user_id, product_name)
        if not order:
            return False, f"Order '{product_name}' not found."
        actual = order.status.value
        passed = actual == expected_status
        return passed, f"Expected status '{expected_status}' for '{product_name}', got '{actual}'."
    finally:
        db.close()


def _check_order_status_unchanged(user_id: str, spec: str) -> tuple[bool, str]:
    # Same check as _check_order_status -- named separately in the dataset
    # for readability (asserting a non-event reads oddly as "order_status").
    return _check_order_status(user_id, spec)


def _check_no_order_status_changed(user_id: str, _: str | None) -> tuple[bool, str]:
    db = SessionLocal()
    try:
        orders = db.query(Order).filter(Order.user_id == user_id).all()
        seed_by_name = {o["product_name"]: o["status"] for o in SEED_ORDERS}
        changed = [o.product_name for o in orders if o.status != seed_by_name.get(o.product_name)]
        return not changed, (
            f"Order(s) changed unexpectedly: {changed}" if changed else "No order changed status."
        )
    finally:
        db.close()


def _check_ticket_created_containing(user_id: str, keyword: str) -> tuple[bool, str]:
    db = SessionLocal()
    try:
        tickets = db.query(Ticket).filter(Ticket.user_id == user_id).all()
        # 1 pre-existing seed ticket ("Sync not working...") -- look for an
        # additional one matching the keyword, not just any ticket existing.
        matches = [
            t for t in tickets
            if keyword.lower() in (t.subject + " " + t.description).lower()
        ]
        return bool(matches), (
            f"Found matching ticket(s): {[t.subject for t in matches]}" if matches
            else f"No ticket found containing '{keyword}'."
        )
    finally:
        db.close()


DB_CHECKS = {
    "order_status": _check_order_status,
    "order_status_unchanged": _check_order_status_unchanged,
    "no_order_status_changed": _check_no_order_status_changed,
    "ticket_created_containing": _check_ticket_created_containing,
}


def run_db_check(user_id: str, check_spec: str) -> tuple[bool, str]:
    kind, _, rest = check_spec.partition(":")
    fn = DB_CHECKS.get(kind)
    if not fn:
        return False, f"Unknown db check kind: '{kind}'"
    return fn(user_id, rest or None)


# ---------- per-example evaluation ----------


async def evaluate_example(user_id: str, example: dict) -> dict:
    reset_demo_data(user_id)
    result = await run_agent_turn(user_id, example["message"])
    called_names = [c["name"] for c in result["tool_calls"]]

    checks: list[tuple[str, bool, str]] = []

    expect_tool = example.get("expect_tool_called")
    if expect_tool:
        passed = expect_tool in called_names
        checks.append((f"called {expect_tool}", passed, f"tool_calls={called_names}"))

    for forbidden in example.get("expect_no_tool_called", []):
        passed = forbidden not in called_names
        checks.append((f"did not call {forbidden}", passed, f"tool_calls={called_names}"))

    if example.get("expect_sources_nonempty"):
        passed = len(result["sources"]) > 0
        checks.append(("sources non-empty", passed, f"sources={len(result['sources'])}"))

    if "expect_db_check" in example:
        passed, detail = run_db_check(user_id, example["expect_db_check"])
        checks.append((example["expect_db_check"], passed, detail))

    overall_passed = all(c[1] for c in checks) if checks else None

    return {
        "id": example["id"],
        "category": example.get("category"),
        "message": example["message"],
        "passed": overall_passed,
        "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in checks],
        "tool_calls": result["tool_calls"],
        "answer": result["answer"],
    }


async def run() -> dict:
    user_id = get_demo_user_id()
    with open(DATASET_PATH, encoding="utf-8") as f:
        examples = json.load(f)

    results = []
    for example in examples:
        print(f"Running: {example['id']}...")
        try:
            outcome = await evaluate_example(user_id, example)
        except Exception as exc:  # noqa: BLE001 -- an example erroring is itself a finding
            traceback.print_exc()
            outcome = {
                "id": example["id"],
                "category": example.get("category"),
                "message": example["message"],
                "passed": False,
                "checks": [{"name": "ran without error", "passed": False, "detail": str(exc)}],
                "tool_calls": [],
                "answer": "",
            }
        results.append(outcome)
        status = "PASS" if outcome["passed"] else "FAIL"
        print(f"  {status}")
        for c in outcome["checks"]:
            mark = "OK" if c["passed"] else "XX"
            print(f"    [{mark}] {c['name']} -- {c['detail']}")

    # Leave the demo data in the reset (known) state rather than whatever
    # the final example mutated it into.
    reset_demo_data(user_id)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"] is True),
        "failed": sum(1 for r in results if r["passed"] is False),
        "results": results,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    with open(RESULTS_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print()
    print(f"{payload['passed']}/{payload['total']} passed, {payload['failed']} failed.")
    return payload


if __name__ == "__main__":
    asyncio.run(run())
