def test_register_then_me(client):
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "hunter2", "full_name": "Alice"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_register_duplicate_email_rejected(client):
    payload = {"email": "bob@example.com", "password": "pw", "full_name": "Bob"}
    first = client.post("/auth/register", json=payload)
    assert first.status_code == 200

    second = client.post("/auth/register", json=payload)
    assert second.status_code == 400


def test_login_wrong_password_rejected(client):
    client.post(
        "/auth/register",
        json={"email": "carol@example.com", "password": "correct", "full_name": "Carol"},
    )
    resp = client.post(
        "/auth/login", json={"email": "carol@example.com", "password": "wrong"}
    )
    assert resp.status_code == 401


def test_me_without_token_rejected(client):
    resp = client.get("/auth/me")
    assert resp.status_code in (401, 403)
