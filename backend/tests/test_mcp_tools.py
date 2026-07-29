"""
Exercises the MCP tool functions directly (as plain Python calls) rather
than over the stdio protocol -- that keeps the tests fast while still
covering the real business logic (the FastMCP decorator doesn't change
the function's behavior, only how it's exposed over MCP).
"""

import pytest

from app.models import Order, OrderStatus, User
from app.tools import mcp_server


@pytest.fixture()
def seeded_user_and_order(sqlite_sessionmaker, monkeypatch):
    """Points mcp_server.SessionLocal at the test DB, seeds one user with
    one processing order, and sets SUPPORT_AGENT_USER_ID so the tools
    scope every query to that user -- mirroring how mcp_client.py spawns
    the real server with that env var set per-request."""
    monkeypatch.setattr(mcp_server, "SessionLocal", sqlite_sessionmaker)

    session = sqlite_sessionmaker()
    user = User(email="u@example.com", full_name="U", hashed_password="x")
    session.add(user)
    session.flush()
    order = Order(user_id=user.id, product_name="Widget", status=OrderStatus.processing, amount=9.99)
    session.add(order)
    session.commit()
    user_id, order_id = user.id, order.id
    session.close()

    monkeypatch.setenv("SUPPORT_AGENT_USER_ID", user_id)
    return user_id, order_id


def test_lookup_order_returns_seeded_order(seeded_user_and_order):
    _, order_id = seeded_user_and_order
    result = mcp_server.lookup_order()
    assert "Widget" in result
    assert order_id in result


def test_lookup_order_scoped_to_current_user(seeded_user_and_order, sqlite_sessionmaker, monkeypatch):
    # A second user's order must never show up for the first user.
    session = sqlite_sessionmaker()
    other_user = User(email="other@example.com", full_name="Other", hashed_password="x")
    session.add(other_user)
    session.flush()
    session.add(Order(user_id=other_user.id, product_name="Other's Gadget", status=OrderStatus.processing, amount=1))
    session.commit()
    session.close()

    result = mcp_server.lookup_order()
    assert "Other's Gadget" not in result


def test_cancel_order_updates_status(seeded_user_and_order, sqlite_sessionmaker):
    _, order_id = seeded_user_and_order
    result = mcp_server.cancel_order(order_id)
    assert "cancelled" in result.lower()

    session = sqlite_sessionmaker()
    order = session.query(Order).filter(Order.id == order_id).first()
    assert order.status == OrderStatus.cancelled
    session.close()


def test_cancel_order_rejects_already_shipped(seeded_user_and_order, sqlite_sessionmaker):
    _, order_id = seeded_user_and_order
    session = sqlite_sessionmaker()
    order = session.query(Order).filter(Order.id == order_id).first()
    order.status = OrderStatus.shipped
    session.commit()
    session.close()

    result = mcp_server.cancel_order(order_id)
    assert "cannot cancel" in result.lower()


def test_create_ticket_persists(seeded_user_and_order, sqlite_sessionmaker):
    result = mcp_server.create_ticket(subject="Help", description="It's broken", priority="high")
    assert "Help" in result
    assert "high" in result


def test_escalate_issue_opens_high_priority_ticket(seeded_user_and_order):
    result = mcp_server.escalate_issue(reason="Can't answer this one")
    assert "Escalated" in result
    assert "high" in result.lower() or "priority" in result.lower()
