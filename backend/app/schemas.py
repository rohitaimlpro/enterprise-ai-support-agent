"""Pydantic request/response models -- the API's public contract."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, ConfigDict


# ---------- Auth ----------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    is_admin: bool = False


# ---------- Chat ----------


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class SourceCitation(BaseModel):
    title: str
    source_file: str
    snippet: str


class ToolCallRecord(BaseModel):
    name: str
    args: dict[str, Any] = {}


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    sources: list[dict] = []
    tool_calls: list[dict] = []
    guardrail_flags: list[str] = []
    created_at: datetime


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    messages: list[MessageResponse] = []


class AdminConversationResponse(BaseModel):
    """Same shape as ConversationResponse, plus which user it belongs to
    -- only meaningful for the admin trace view, since a normal user's
    own conversation list never needs to say whose it is."""

    id: str
    title: str
    created_at: datetime
    user_email: str
    messages: list[MessageResponse] = []


# ---------- Tickets / Orders (read-only demo endpoints) ----------


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subject: str
    description: str
    status: str
    priority: str
    created_at: datetime


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_name: str
    status: str
    amount: float
    order_date: datetime
