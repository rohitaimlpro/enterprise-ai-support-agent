"""
Tests for the admin trace endpoint -- both that it's genuinely
authorization-gated (a regular authenticated user is not enough) and
that it correctly shows data across users, which chat_router's own
GET /chat/conversations deliberately never does.
"""

from app.models import Conversation, Message, MessageRole, User


def _register(client, email, full_name="Test User"):
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "pw123456", "full_name": full_name},
    )
    return resp.json()["access_token"]


def _make_admin(db_session, email):
    user = db_session.query(User).filter(User.email == email).first()
    user.is_admin = True
    db_session.commit()


def test_regular_user_forbidden(client, db_session):
    token = _register(client, "regular@example.com")
    resp = client.get("/admin/traces", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_admin_user_allowed(client, db_session):
    token = _register(client, "admin@example.com")
    _make_admin(db_session, "admin@example.com")

    resp = client.get("/admin/traces", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_unauthenticated_rejected(client):
    resp = client.get("/admin/traces")
    assert resp.status_code in (401, 403)


def test_admin_sees_conversations_across_all_users(client, db_session):
    admin_token = _register(client, "admin2@example.com")
    _make_admin(db_session, "admin2@example.com")
    _register(client, "customerA@example.com", "Customer A")
    _register(client, "customerB@example.com", "Customer B")

    user_a = db_session.query(User).filter(User.email == "customerA@example.com").first()
    user_b = db_session.query(User).filter(User.email == "customerB@example.com").first()

    conv_a = Conversation(user_id=user_a.id, title="A's question")
    conv_b = Conversation(user_id=user_b.id, title="B's question")
    db_session.add_all([conv_a, conv_b])
    db_session.commit()

    resp = client.get("/admin/traces", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    titles = {c["title"] for c in resp.json()}
    emails = {c["user_email"] for c in resp.json()}
    assert {"A's question", "B's question"} <= titles
    assert {"customerA@example.com", "customerB@example.com"} <= emails


def test_flagged_only_filters_to_flagged_conversations(client, db_session):
    admin_token = _register(client, "admin3@example.com")
    _make_admin(db_session, "admin3@example.com")
    _register(client, "customerC@example.com", "Customer C")
    user_c = db_session.query(User).filter(User.email == "customerC@example.com").first()

    clean_conv = Conversation(user_id=user_c.id, title="Clean conversation")
    flagged_conv = Conversation(user_id=user_c.id, title="Flagged conversation")
    db_session.add_all([clean_conv, flagged_conv])
    db_session.flush()

    db_session.add(
        Message(conversation_id=clean_conv.id, role=MessageRole.assistant, content="fine", guardrail_flags=[])
    )
    db_session.add(
        Message(
            conversation_id=flagged_conv.id,
            role=MessageRole.assistant,
            content="filtered",
            guardrail_flags=["prompt_injection_phrasing"],
        )
    )
    db_session.commit()

    resp = client.get(
        "/admin/traces", params={"flagged_only": True}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    titles = [c["title"] for c in resp.json()]
    assert "Flagged conversation" in titles
    assert "Clean conversation" not in titles
