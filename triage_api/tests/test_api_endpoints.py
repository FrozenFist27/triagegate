"""End-to-end API behaviour through the FastAPI TestClient."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from app.llm.fake_client import FakeLLMClient

SECRET = "test_signing_secret"


def sign(raw: str, ts: str) -> str:
    return "v0=" + hmac.new(
        SECRET.encode(), f"v0:{ts}:{raw}".encode(), hashlib.sha256
    ).hexdigest()


def test_health_no_auth_required(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["db"] is True


def test_auth_token_required(client):
    r = client.post("/triage", json={"message_id": "x", "source": "webhook"})
    assert r.status_code == 401
    r = client.get("/runs")
    assert r.status_code == 401
    r = client.post("/triage",
                    json={"message_id": "x", "source": "webhook"},
                    headers={"X-Internal-Token": "wrong"})
    assert r.status_code == 401


def test_triage_endpoint_returns_approval_flow(client, auth, db_path):
    from app import triage as triage_mod

    original = triage_mod.get_client
    triage_mod.get_client = lambda *a, **k: FakeLLMClient()
    try:
        body = {
            "message_id": "api-1",
            "source": "webhook",
            "from_email": "jane@acme-widgets.example.com",
            "from_name": "Jane Doe",
            "subject": "Demo please",
            "body_text": "Can we book a demo next week?",
            "received_at": "2026-10-17T10:00:00+00:00",
        }
        r = client.post("/triage", json=body, headers=auth)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "ok"
        assert data["route"] == "approval"
        assert data["approval_id"]
        assert data["slack_blocks"], "Block Kit payload should be returned to n8n"
        assert data["slack_text"]
    finally:
        triage_mod.get_client = original


def test_duplicate_message_id_returns_duplicate(client, auth, db_path):
    from app import triage as triage_mod

    original = triage_mod.get_client
    triage_mod.get_client = lambda *a, **k: FakeLLMClient()
    try:
        body = {"message_id": "dup-api", "source": "webhook",
                "from_email": "a@b.example.com", "subject": "s", "body_text": "b"}
        first = client.post("/triage", json=body, headers=auth).json()
        second = client.post("/triage", json=body, headers=auth).json()
        assert first["status"] == "ok"
        assert second["status"] == "duplicate_message"
        assert second["run_id"] == first["run_id"]
    finally:
        triage_mod.get_client = original


def test_full_decision_flow_happy_path(client, auth, db_path):
    from app import triage as triage_mod

    original = triage_mod.get_client
    triage_mod.get_client = lambda *a, **k: FakeLLMClient()
    try:
        body = {"message_id": "flow-1", "source": "webhook",
                "from_email": "jane@acme-widgets.example.com", "from_name": "Jane",
                "subject": "Demo", "body_text": "Demo next week please."}
        triage = client.post("/triage", json=body, headers=auth).json()
        approval_id = triage["approval_id"]

        # n8n posts the Slack message and saves the ref
        r = client.post(
            f"/approvals/{approval_id}/slack-ref",
            json={"channel": "C123", "ts": "1760000000.000100"},
            headers=auth,
        )
        assert r.status_code == 200

        # signed Slack approve click
        payload = {
            "actions": [{"action_id": "triage_approve", "value": approval_id}],
            "user": {"id": "U1", "username": "sambudh"},
        }
        raw = "payload=" + json.dumps(payload)
        ts = str(int(time.time()))
        r = client.post(
            "/approvals/decide",
            json={"raw_body": raw, "signature": sign(raw, ts), "timestamp": ts},
            headers=auth,
        )
        assert r.status_code == 200
        out = r.json()
        assert out["outcome"] == "write_crm"
        assert out["crm_payload"]["contact"]["email"] == "jane@acme-widgets.example.com"

        # n8n reports the HubSpot result
        r = client.post(
            f"/approvals/{approval_id}/crm-result",
            json={"status": "success", "hubspot_contact_id": "9001"},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["inserted"] is True

        # double click -> duplicate
        r = client.post(
            "/approvals/decide",
            json={"raw_body": raw, "signature": sign(raw, ts), "timestamp": ts},
            headers=auth,
        )
        assert r.json()["outcome"] == "duplicate"

        # run log shows input, model output, decision, result
        runs = client.get("/runs", headers=auth).json()
        run = next(x for x in runs if x["run_id"] == triage["run_id"])
        assert json.loads(run["input_json"])["subject"] == "Demo"
        attempts = json.loads(run["attempts_json"])
        assert attempts and attempts[0]["raw_output"]
        assert run["decision"] == "approved"
        assert run["result"] == "crm_written:9001"
    finally:
        triage_mod.get_client = original


def test_bad_signature_rejected(client, auth):
    raw = "payload=" + json.dumps({"actions": [{"action_id": "triage_approve", "value": "x"}]})
    ts = str(int(time.time()))
    r = client.post(
        "/approvals/decide",
        json={"raw_body": raw, "signature": "v0=deadbeef", "timestamp": ts},
        headers=auth,
    )
    assert r.json()["outcome"] == "invalid_signature"


def test_expire_endpoint(client, auth, db_path):
    r = client.post("/approvals/expire", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"expired": [], "count": 0}


def test_export_jsonl(client, auth, db_path):
    from app import triage as triage_mod

    original = triage_mod.get_client
    triage_mod.get_client = lambda *a, **k: FakeLLMClient()
    try:
        client.post("/triage",
                    json={"message_id": "exp-1", "source": "webhook",
                          "from_email": "a@b.example.com", "subject": "s",
                          "body_text": "hello"},
                    headers=auth)
        r = client.get("/runs/export.jsonl", headers=auth)
        assert r.status_code == 200
        lines = [x for x in r.text.splitlines() if x.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["message_id"] == "exp-1"
    finally:
        triage_mod.get_client = original


def test_events_logged(client, auth, db_path):
    from app import triage as triage_mod

    original = triage_mod.get_client
    triage_mod.get_client = lambda *a, **k: FakeLLMClient()
    try:
        client.post("/triage",
                    json={"message_id": "ev-1", "source": "webhook",
                          "from_email": "a@b.example.com", "subject": "s",
                          "body_text": "hello"},
                    headers=auth)
        events = client.get("/events", headers=auth).json()
        names = {e["event"] for e in events}
        assert "triage_completed" in names
        assert "approval_created" in names
    finally:
        triage_mod.get_client = original


def _make_approval(client, auth, monkeypatch, msg_id="api-card-1"):
    from app import triage as triage_mod

    monkeypatch.setattr(triage_mod, "get_client", lambda *a, **k: FakeLLMClient())
    body = {
        "message_id": msg_id, "source": "webhook",
        "from_email": "jane@acme-widgets.example.com", "from_name": "Jane Doe",
        "subject": "Demo please", "body_text": "Can we book a demo next week?",
        "received_at": "2026-10-17T10:00:00+00:00",
    }
    return client.post("/triage", json=body, headers=auth).json()


def _click(client, auth, approval_id, action):
    payload = {"actions": [{"action_id": action, "value": approval_id}],
               "user": {"id": "U1", "username": "tester"}}
    raw = "payload=" + json.dumps(payload)
    ts = str(int(time.time()))
    return client.post("/approvals/decide", headers=auth,
                       json={"raw_body": raw, "signature": sign(raw, ts), "timestamp": ts}).json()


def test_decide_returns_slack_card_and_ticket_subject_has_marker(client, auth, monkeypatch):
    data = _make_approval(client, auth, monkeypatch)
    out = _click(client, auth, data["approval_id"], "triage_approve")
    assert out["outcome"] == "write_crm"
    assert "Approved by tester" in out["slack_text"]
    assert out["slack_blocks"] and out["slack_blocks"][0]["type"] == "section"
    assert out["crm_payload"]["contact"]["lastname"].endswith("[TriageGate test]")
    assert out["crm_payload"]["run_id"] == data["run_id"]


def test_reject_returns_card_and_second_click_is_duplicate(client, auth, monkeypatch):
    data = _make_approval(client, auth, monkeypatch, "api-card-2")
    out = _click(client, auth, data["approval_id"], "triage_reject")
    assert out["outcome"] == "rejected" and out["crm_payload"] is None
    assert "Rejected by tester" in out["slack_text"]
    again = _click(client, auth, data["approval_id"], "triage_approve")
    assert again["outcome"] == "duplicate" and again["crm_payload"] is None


def test_expire_returns_slack_text(client, auth, monkeypatch, db_path):
    import sqlite3

    data = _make_approval(client, auth, monkeypatch, "api-card-3")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE approvals SET expires_at='2000-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()
    r = client.post("/approvals/expire", headers=auth).json()
    assert r["count"] == 1 and "Expired" in r["expired"][0]["slack_text"]
    out = _click(client, auth, data["approval_id"], "triage_approve")
    assert out["outcome"] == "expired" and out["crm_payload"] is None


def test_unpersisted_run_creates_no_approval(db_path, sample_email):
    from app.llm.fake_client import FakeLLMClient
    from app.triage import run_triage

    r = run_triage(sample_email(), client=FakeLLMClient(), persist=False)
    assert r.route.value == "approval" and r.approval_id is None
