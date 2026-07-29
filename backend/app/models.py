"""
SQLAlchemy models -- the source of truth for everything the agent's tools
read and write (users, orders, tickets) and everything the chat UI shows
(conversations, messages).
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.db_types import GUID, JSONType


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(GUID(), primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)

    conversations = relationship("Conversation", back_populates="user")
    orders = relationship("Order", back_populates="user")
    tickets = relationship("Ticket", back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(GUID(), primary_key=True, default=_uuid)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    title = Column(String, default="New conversation")
    created_at = Column(DateTime(timezone=True), default=_now)

    user = relationship("User", back_populates="conversations")
    messages = relationship(
        "Message", back_populates="conversation", order_by="Message.created_at"
    )


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


class Message(Base):
    __tablename__ = "messages"

    id = Column(GUID(), primary_key=True, default=_uuid)
    conversation_id = Column(
        GUID(), ForeignKey("conversations.id"), nullable=False
    )
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)
    # List[{"title": str, "source_file": str, "snippet": str}] -- RAG citations.
    sources = Column(JSONType(), default=list)
    # List[{"name": str, "args": dict}] -- which tools the agent called for this turn.
    tool_calls = Column(JSONType(), default=list)
    created_at = Column(DateTime(timezone=True), default=_now)

    conversation = relationship("Conversation", back_populates="messages")


class OrderStatus(str, enum.Enum):
    processing = "processing"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"
    refunded = "refunded"


class Order(Base):
    __tablename__ = "orders"

    id = Column(GUID(), primary_key=True, default=_uuid)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    product_name = Column(String, nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.processing)
    amount = Column(Float, nullable=False)
    order_date = Column(DateTime(timezone=True), default=_now)

    user = relationship("User", back_populates="orders")


class TicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"


class TicketPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(GUID(), primary_key=True, default=_uuid)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    conversation_id = Column(
        GUID(), ForeignKey("conversations.id"), nullable=True
    )
    subject = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(Enum(TicketStatus), default=TicketStatus.open)
    priority = Column(Enum(TicketPriority), default=TicketPriority.medium)
    created_at = Column(DateTime(timezone=True), default=_now)

    user = relationship("User", back_populates="tickets")
