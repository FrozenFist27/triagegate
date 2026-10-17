#!/usr/bin/env python
"""Send a correctly SIGNED synthetic Slack interactivity payload to the n8n webhook.

Used when the Slack app's Interactivity Request URL is not configured: it exercises the
exact same endpoint Slack would call, with the real signing secret (read from .env,
never printed).

  python scripts/simulate_click.py <approval_id> [approve|reject] [--url URL] [--user NAME]
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
import urllib.parse
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def signed_request(approval_id: str, action: str, secret: str, user: str = "simulated-reviewer",
                   ts: str | None = None) -> tuple[str, dict[str, str]]:
    payload = {
        "type": "block_actions",
        "user": {"id": "U_SIMULATED", "username": user},
        "actions": [{"action_id": "triage_approve" if action == "approve" else "triage_reject",
                     "value": approval_id}],
    }
    raw = "payload=" + urllib.parse.quote(json.dumps(payload, separators=(",", ":")))
    stamp = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:{raw}".encode(), hashlib.sha256).hexdigest()
    return raw, {"Content-Type": "application/x-www-form-urlencoded",
                 "X-Slack-Signature": sig, "X-Slack-Request-Timestamp": stamp}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("approval_id")
    ap.add_argument("action", nargs="?", default="approve", choices=["approve", "reject"])
    ap.add_argument("--url", default="http://localhost:5678/webhook/slack-actions")
    ap.add_argument("--user", default="simulated-reviewer")
    a = ap.parse_args()
    env = dotenv_values(ROOT / ".env")
    raw, headers = signed_request(a.approval_id, a.action, env["SLACK_SIGNING_SECRET"], a.user)
    r = httpx.post(a.url, content=raw, headers=headers, timeout=30)
    print(r.status_code, r.text[:200])


if __name__ == "__main__":
    main()
