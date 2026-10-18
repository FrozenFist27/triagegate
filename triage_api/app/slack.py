"""Slack: signing-secret verification, Block Kit builder, interactivity payload parse."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import parse_qsl

from .config import get_settings
from .schemas import TriageResponse, TriageResult

MAX_AGE_SECONDS = 300  # Slack rejects signatures older than 5 minutes
ACTION_APPROVE = "triage_approve"
ACTION_REJECT = "triage_reject"

_URGENCY_LABEL = {"high": "[HIGH]", "medium": "[MED]", "low": "[LOW]"}
_CATEGORY_LABEL = {
    "sales_lead": "Sales lead",
    "support_request": "Support request",
    "invoice": "Invoice",
    "spam": "Spam",
}


def verify_slack_signature(
    *,
    raw_body: str,
    signature: str,
    timestamp: str,
    signing_secret: Optional[str] = None,
    now: Optional[float] = None,
) -> tuple[bool, str]:
    """Slack v0 signature check. Returns (ok, reason)."""
    secret = signing_secret if signing_secret is not None else get_settings().slack_signing_secret
    if not secret:
        return False, "no signing secret configured"
    if not signature or not timestamp:
        return False, "missing signature or timestamp header"

    try:
        ts = int(float(timestamp))
    except (TypeError, ValueError):
        return False, "timestamp is not a number"

    current = now if now is not None else time.time()
    if abs(current - ts) > MAX_AGE_SECONDS:
        return False, f"timestamp outside {MAX_AGE_SECONDS}s window"

    basestring = f"v0:{timestamp}:{raw_body}".encode("utf-8")
    expected = "v0=" + hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False, "signature mismatch"
    return True, "ok"


def verify_forward_secret(provided: str, *, expected: Optional[str] = None,
                          mode: Optional[str] = None) -> tuple[bool, str]:
    """Socket Mode: check the internal shared secret set by the slack-socket listener."""
    st = get_settings()
    mode = mode if mode is not None else st.slack_mode
    expected = expected if expected is not None else st.internal_forward_secret
    if mode != "socket":
        return False, "internal forwarding disabled (SLACK_MODE is not socket)"
    if not expected:
        return False, "no internal forward secret configured"
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        return False, "internal forward secret mismatch"
    return True, "ok"


def parse_slack_payload(raw_body: str) -> dict[str, Any]:
    """Slack interactivity sends application/x-www-form-urlencoded."""
    pairs = parse_qsl(raw_body, keep_blank_values=True)
    data: dict[str, Any] = dict(pairs)
    import json

    if "payload" in data:
        try:
            data = json.loads(data["payload"])
        except json.JSONDecodeError as e:
            raise ValueError(f"unparseable Slack payload: {e}") from e
    return data


def extract_action(payload: dict[str, Any]) -> tuple[Optional[str], Optional[str], dict[str, Any]]:
    """Return (action_id, approval_id, user_info) from an interactivity payload."""
    actions = payload.get("actions") or []
    if not actions:
        return None, None, {}
    action = actions[0]
    user = payload.get("user") or {}
    return (
        action.get("action_id"),
        action.get("value"),
        {"id": user.get("id"), "username": user.get("username")},
    )


def build_slack_blocks(triage: TriageResult, approval_id: str, run_id: str, expires_at: str) -> list[dict[str, Any]]:
    cat = _CATEGORY_LABEL.get(triage.category.value, triage.category.value)
    urg = _URGENCY_LABEL.get(triage.urgency.value, "")
    header = f"{urg} {cat}".strip()

    fields = [
        {"type": "mrkdwn", "text": f"*Company*\n{triage.company or 'n/a'}"},
        {"type": "mrkdwn", "text": f"*Contact*\n{triage.contact_name or 'n/a'}"},
        {"type": "mrkdwn", "text": f"*Email*\n{triage.contact_email or 'n/a'}"},
        {"type": "mrkdwn", "text": f"*Urgency*\n{triage.urgency.value} ({round(triage.confidence * 100)}% conf.)"},
    ]

    return [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150], "emoji": False}},
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Request*\n{triage.request_summary}"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"run `{run_id[:8]}` · expires {expires_at[:19]} UTC · approval `{approval_id[:8]}`"},
            ],
        },
        {
            "type": "actions",
            "block_id": f"triage_actions_{approval_id}",
            "elements": [
                {
                    "type": "button",
                    "action_id": ACTION_APPROVE,
                    "text": {"type": "plain_text", "text": "Approve", "emoji": False},
                    "style": "primary",
                    "value": approval_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Write to HubSpot?", "emoji": False},
                        "text": {"type": "mrkdwn", "text": f"Creates the {cat.lower()} record in HubSpot."},
                        "confirm": {"type": "plain_text", "text": "Approve", "emoji": False},
                        "deny": {"type": "plain_text", "text": "Cancel", "emoji": False},
                    },
                },
                {
                    "type": "button",
                    "action_id": ACTION_REJECT,
                    "text": {"type": "plain_text", "text": "Reject", "emoji": False},
                    "style": "danger",
                    "value": approval_id,
                },
            ],
        },
    ]


def slack_fallback_text(triage: TriageResult) -> str:
    cat = _CATEGORY_LABEL.get(triage.category.value, triage.category.value)
    return f"{_URGENCY_LABEL.get(triage.urgency.value, '')} {cat}: {triage.request_summary}".strip()


def build_response_blocks(outcome: str, detail: str) -> list[dict[str, Any]]:
    """Replacement blocks for chat.update after a decision."""
    text = {
        "write_crm": f"Approved - written to HubSpot. {detail}",
        "rejected": f"Rejected. No CRM record created. {detail}",
        "expired": "Expired - no action taken.",
        "duplicate": "Already processed - duplicate click ignored.",
        "invalid_signature": "",
    }.get(outcome, outcome)
    if not text:
        return []
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: {text}" if outcome == "write_crm" else f"{text}"}},
    ]


def make_approval_message(
    triage_resp: TriageResponse,
    approval_id: str,
    expires_at: str,
) -> tuple[list[dict[str, Any]], str]:
    assert triage_resp.triage is not None
    blocks = build_slack_blocks(
        triage_resp.triage, approval_id, triage_resp.run_id, expires_at
    )
    return blocks, slack_fallback_text(triage_resp.triage)


def build_decision_blocks(headline: str, summary: str = "") -> list[dict[str, Any]]:
    """Blocks that replace the approval card (buttons removed) after a decision."""
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*"}}
    ]
    if summary:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": summary[:300]}]})
    return blocks
