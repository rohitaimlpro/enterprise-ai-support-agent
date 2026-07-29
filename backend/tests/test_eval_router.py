import json

import app.routers.eval_router as eval_router_module


def _auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "eval@example.com", "password": "pw123456", "full_name": "Eval User"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_returns_404_when_no_results_yet(client, tmp_path, monkeypatch):
    monkeypatch.setattr(eval_router_module, "RESULTS_PATH", tmp_path / "missing.json")
    resp = client.get("/admin/eval", headers=_auth_headers(client))
    assert resp.status_code == 404


def test_returns_latest_results_when_present(client, tmp_path, monkeypatch):
    results_file = tmp_path / "latest.json"
    payload = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "num_examples": 3,
        "metrics": {"faithfulness": 0.9, "answer_relevancy": 0.8, "context_precision": 0.7, "context_recall": 0.95},
    }
    results_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(eval_router_module, "RESULTS_PATH", results_file)

    resp = client.get("/admin/eval", headers=_auth_headers(client))
    assert resp.status_code == 200
    assert resp.json() == payload
