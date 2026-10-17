"""Slack signing-secret verification."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from app.schemas import TriageResult
from app.slack import (
    build_slack_blocks,
    extract_action,
    parse_slack_payload,
    verify_slack_signature,
)

SECRET = "test_signing_secret"


def sign(raw_body: str, ts: str, secret: str = SECRET) -> str:
    basestring = f"v0:{ts}:{raw_body}".encode()
    return "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    body = "payload=%7B%22a%22%3A1%7D"
    ts = str(int(time.time()))
    ok, reason = verify_slack_signature(
        raw_body=body, signature=sign(body, ts), timestamp=ts, signing_secret=SECRET
    )
    assert ok, reason


def test_tampered_body_fails():
    ts = str(int(time.time()))
    sig = sign("original body", ts)
    ok, reason = verify_slack_signature(
        raw_body="tampered body", signature=sig, timestamp=ts, signing_secret=SECRET
    )
    assert not ok and reason == "signature mismatch"


def test_old_timestamp_fails():
    body = "payload=x"
    old_ts = str(int(time.time()) - 4000)
    ok, reason = verify_slack_signature(
        raw_body=body, signature=sign(body, old_ts), timestamp=old_ts, signing_secret=SECRET
    )
    assert not ok and "window" in reason


def test_missing_header_fails():
    ok, reason = verify_slack_signature(
        raw_body="x", signature="", timestamp="", signing_secret=SECRET
    )
    assert not ok and "missing" in reason


def test_wrong_secret_fails():
    ts = str(int(time.time()))
    body = "payload=x"
    ok, _ = verify_slack_signature(
        raw_body=body, signature=sign(body, ts, secret="other"), timestamp=ts,
        signing_secret=SECRET,
    )
    assert not ok


def test_parse_form_encoded_payload():
    payload = {
        "actions": [{"action_id": "triage_approve", "value": "appr-1"}],
        "user": {"id": "U1", "username": "sambudh"},
    }
    raw = "payload=" + json.dumps(payload)
    data = parse_slack_payload(raw)
    action_id, value, user = extract_action(data)
    assert action_id == "triage_approve"
    assert value == "appr-1"
    assert user["username"] == "sambudh"


def test_blocks_contain_buttons_and_no_pii_in_value():
    t = TriageResult.model_validate({
        "category": "sales_lead", "confidence": 0.9, "company": "Acme",
        "contact_name": "Jane Doe", "contact_email": "jane@acme.example.com",
        "request_summary": "Wants a demo.", "urgency": "high",
        "urgency_reason": "Deadline tomorrow.",
    })
    blocks = build_slack_blocks(t, "approval-uuid-1234", "run-uuid", "2026-10-18T10:00:00+00:00")
    action_block = next(b for b in blocks if b.get("type") == "actions")
    buttons = action_block["elements"]
    assert [b["action_id"] for b in buttons] == ["triage_approve", "triage_reject"]
    assert all(b["value"] == "approval-uuid-1234" for b in buttons)
    # no PII inside button values
    assert "jane@" not in json.dumps(buttons)
