"""
MCP tool server exposing the "enterprise systems" a support agent needs:
orders, tickets, refunds, invoices, callbacks, and escalation.

This is a real MCP server (using the official `mcp` SDK's FastMCP helper),
not a fake function-calling shim -- it can be launched standalone and
talked to by any MCP client, not just this project's agent:

    python -m app.tools.mcp_server

Normally though, app/tools/mcp_client.py spawns it as a subprocess and the
LangGraph agent calls its tools over stdio via langchain-mcp-adapters.

Every tool is scoped to one user via the SUPPORT_AGENT_USER_ID environment
variable (set by mcp_client.py when it spawns this process), NOT via an
argument the LLM fills in -- an LLM should never be trusted to supply
(or be tempted to guess) another user's id.
"""

import os

from mcp.server.fastmcp import FastMCP

from app.database import SessionLocal
from app.models import Order, OrderStatus, Ticket, TicketPriority, TicketStatus

mcp = FastMCP("enterprise-support-tools")


def _current_user_id() -> str:
    user_id = os.environ.get("SUPPORT_AGENT_USER_ID")
    if not user_id:
        raise RuntimeError("SUPPORT_AGENT_USER_ID env var not set for MCP server")
    return user_id


def _priority_from_string(value: str) -> TicketPriority:
    try:
        return TicketPriority(value.lower())
    except ValueError:
        return TicketPriority.medium


@mcp.tool()
def lookup_order(order_id: str = "") -> str:
    """Look up the current user's orders. Pass order_id to look up one
    specific order, or leave it blank to list recent orders."""
    db = SessionLocal()
    try:
        query = db.query(Order).filter(Order.user_id == _current_user_id())
        if order_id:
            query = query.filter(Order.id == order_id)
        orders = query.order_by(Order.order_date.desc()).limit(10).all()

        if not orders:
            return "No matching orders found."

        return "\n".join(
            f"- {o.product_name}: status={o.status.value}, amount=${o.amount:.2f}, "
            f"ordered={o.order_date.date()}, order_id={o.id}"
            for o in orders
        )
    finally:
        db.close()


@mcp.tool()
def cancel_order(order_id: str) -> str:
    """Cancel an order that is still in 'processing' status."""
    db = SessionLocal()
    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id, Order.user_id == _current_user_id())
            .first()
        )
        if not order:
            return "Order not found."
        if order.status != OrderStatus.processing:
            return f"Cannot cancel: order is already '{order.status.value}'."

        order.status = OrderStatus.cancelled
        db.commit()
        return f"Order {order_id} cancelled."
    finally:
        db.close()


@mcp.tool()
def refund_request(order_id: str, reason: str) -> str:
    """File a refund request for one of the user's orders and open a
    tracking ticket for it."""
    db = SessionLocal()
    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id, Order.user_id == _current_user_id())
            .first()
        )
        if not order:
            return "Order not found."

        order.status = OrderStatus.refunded
        ticket = Ticket(
            user_id=_current_user_id(),
            subject=f"Refund request for order {order_id}",
            description=reason,
            status=TicketStatus.open,
            priority=TicketPriority.medium,
        )
        db.add(ticket)
        db.commit()
        return f"Refund requested for order {order_id}; ticket {ticket.id} opened to process it."
    finally:
        db.close()


@mcp.tool()
def get_invoice(order_id: str) -> str:
    """Get a plain-text invoice summary for one order."""
    db = SessionLocal()
    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id, Order.user_id == _current_user_id())
            .first()
        )
        if not order:
            return "Order not found."

        return (
            f"Invoice for order {order.id}\n"
            f"Product: {order.product_name}\n"
            f"Amount: ${order.amount:.2f}\n"
            f"Date: {order.order_date.date()}\n"
            f"Status: {order.status.value}"
        )
    finally:
        db.close()


@mcp.tool()
def create_ticket(subject: str, description: str, priority: str = "medium") -> str:
    """Create a support ticket for an issue that needs human follow-up."""
    db = SessionLocal()
    try:
        ticket = Ticket(
            user_id=_current_user_id(),
            subject=subject,
            description=description,
            status=TicketStatus.open,
            priority=_priority_from_string(priority),
        )
        db.add(ticket)
        db.commit()
        return f"Ticket {ticket.id} created: '{subject}' (priority={ticket.priority.value})."
    finally:
        db.close()


@mcp.tool()
def escalate_issue(reason: str) -> str:
    """Escalate the conversation to a human agent. Use this when you can't
    confidently answer the user's question after searching the knowledge
    base, or when they explicitly ask for a human."""
    db = SessionLocal()
    try:
        ticket = Ticket(
            user_id=_current_user_id(),
            subject="Escalated from AI assistant",
            description=reason,
            status=TicketStatus.open,
            priority=TicketPriority.high,
        )
        db.add(ticket)
        db.commit()
        return (
            f"Escalated to a human agent. Ticket {ticket.id} opened "
            "(priority=high) -- someone will follow up shortly."
        )
    finally:
        db.close()


@mcp.tool()
def schedule_callback(preferred_time: str, phone_number: str) -> str:
    """Schedule a phone callback from support at the user's preferred time."""
    db = SessionLocal()
    try:
        ticket = Ticket(
            user_id=_current_user_id(),
            subject="Callback requested",
            description=f"Preferred time: {preferred_time}. Phone: {phone_number}.",
            status=TicketStatus.open,
            priority=TicketPriority.medium,
        )
        db.add(ticket)
        db.commit()
        return f"Callback requested for {preferred_time}. Ticket {ticket.id} created."
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run(transport="stdio")
