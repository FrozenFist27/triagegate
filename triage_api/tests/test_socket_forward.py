"""Socket Mode internal-secret path through /approvals/decide."""
from __future__ import annotations

import json
import urllib.parse

import pytest

from app.config import get_settings
from app.llm.fake_client import FakeLLMClient
from app.schemas import DecideOutcome

FWD = "forward-secret-for-tests"


@pytest.fixture()
def sock_client(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_FORWARD_SECRET", FWD)
    monkeypatch.setenv("SLACK_MODE", "socket")
    get_settings.cache_clear()
    return client


def _approval(client, auth):
    from app import triage as triage_mod

    triage_mod.get_client = lambda *a, **k: FakeLLMClient()
    body = {"message_id": "fwd-1", "source": "webhook", "from_email": "jane@acme-widgets.example.com",
            "from_name": "Jane Doe", "subject": "Demo please",
            "body_text": "Can we book a demo next week?", "received_at": "2026-10-17T10:00:00+00:00"}
    return client.post("/triage", json=body, headers=auth).json()["approval_id"]


def _raw(aid, action="triage_approve"):
    p = {"type": "block_actions", "user": {"id": "U1", "username": "rev"},
         "actions": [{"action_id": action, "value": aid}]}
    return "payload=" + urllib.parse.quote(json.dumps(p))


def _decide(client, auth, aid, secret, action="triage_approve"):
    return client.post("/approvals/decide", headers=auth,
                       json={"raw_body": _raw(aid, action), "forward_secret": secret}).json()


def test_valid_secret_approves_and_duplicate_is_idempotent(sock_client, auth):
    aid = _approval(sock_client, auth)
    assert _decide(sock_client, auth, aid, FWD)["outcome"] == DecideOutcome.write_crm.value
    assert _decide(sock_client, auth, aid, FWD)["outcome"] == DecideOutcome.duplicate.value


@pytest.mark.parametrize("secret", ["wrong-secret", "x"])
def test_bad_secret_rejected_and_approval_stays_pending(sock_client, auth, secret):
    aid = _approval(sock_client, auth)
    assert _decide(sock_client, auth, aid, secret)["outcome"] == DecideOutcome.invalid_signature.value
    assert sock_client.get("/approvals?status=pending", headers=auth).json()[0]["approval_id"] == aid


def test_missing_secret_and_signature_rejected(sock_client, auth):
    aid = _approval(sock_client, auth)
    r = sock_client.post("/approvals/decide", headers=auth, json={"raw_body": _raw(aid)}).json()
    assert r["outcome"] == DecideOutcome.invalid_signature.value


def test_secret_refused_in_http_mode(client, auth, monkeypatch):
    monkeypatch.setenv("INTERNAL_FORWARD_SECRET", FWD)
    monkeypatch.setenv("SLACK_MODE", "http")
    get_settings.cache_clear()
    aid = _approval(client, auth)
    assert _decide(client, auth, aid, FWD)["outcome"] == DecideOutcome.invalid_signature.value


def test_reject_via_secret(sock_client, auth):
    aid = _approval(sock_client, auth)
    assert _decide(sock_client, auth, aid, FWD, "triage_reject")["outcome"] == DecideOutcome.rejected.value
