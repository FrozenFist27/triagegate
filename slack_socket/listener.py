"""Slack Socket Mode listener for TriageGate.

Slack delivers button clicks over a websocket (Socket Mode) instead of POSTing them to a public
Request URL, so no tunnel is needed. Each interaction is acknowledged immediately (Slack allows
3 seconds), then forwarded to n8n's existing `slack-actions` webhook on the compose network with an
internal shared secret header. n8n hands it to triage-api, which owns idempotency, expiry, reject,
the CRM write and the Slack card update - nothing is re-implemented here.

Env: SLACK_APP_TOKEN (app-level token, connections:write), INTERNAL_FORWARD_SECRET, SLACK_MODE (socket|http),
     FORWARD_URL (default http://n8n:5678/webhook/slack-actions). Secrets are never logged.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger("slack-socket")

DEFAULT_FORWARD_URL = "http://n8n:5678/webhook/slack-actions"
SECRET_HEADER = "X-Internal-Forward-Secret"


def build_raw_body(payload: dict[str, Any]) -> str:
    """Re-create the form-encoded body Slack HTTP interactivity would have sent."""
    return "payload=" + urllib.parse.quote(json.dumps(payload, separators=(",", ":")))


def forward_interaction(
    payload: dict[str, Any],
    *,
    url: str,
    secret: str,
    client: httpx.Client | None = None,
    retries: int = 3,
    backoff: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """POST the interaction to the approval path. Retrying is safe: approvals are idempotent."""
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        for attempt in range(1, retries + 1):
            try:
                r = client.post(
                    url,
                    content=build_raw_body(payload),
                    headers={"Content-Type": "application/x-www-form-urlencoded", SECRET_HEADER: secret},
                )
                if r.status_code < 300:
                    return True
                log.warning("forward attempt %d got HTTP %d", attempt, r.status_code)
            except httpx.HTTPError as e:
                log.warning("forward attempt %d failed: %s", attempt, type(e).__name__)
            if attempt < retries:
                sleep(backoff * attempt)
        return False
    finally:
        if own:
            client.close()


def make_handler(*, forward_url: str, secret: str, http: httpx.Client | None = None):
    """Return a SocketModeClient request listener: ack first, then forward."""
    from slack_sdk.socket_mode.response import SocketModeResponse

    def handle(client, req) -> None:
        # Ack before anything else so Slack never sees a timeout, whatever happens next.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type != "interactive":
            return
        payload = req.payload or {}
        if payload.get("type") != "block_actions":
            return
        ok = forward_interaction(payload, url=forward_url, secret=secret, client=http)
        log.info("interaction forwarded ok=%s", ok)

    return handle


def main() -> int:  # pragma: no cover - needs a live websocket
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    mode = os.environ.get("SLACK_MODE", "socket").lower()
    if mode != "socket":
        log.info("SLACK_MODE=%s: Socket Mode listener idle (signed HTTP path is in use)", mode)
        while True:
            time.sleep(3600)
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    secret = os.environ.get("INTERNAL_FORWARD_SECRET", "")
    if not app_token.startswith("xapp" + "-") or not secret:
        log.error("SLACK_APP_TOKEN (app-level) and INTERNAL_FORWARD_SECRET must be set")
        return 2
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.web import WebClient

    url = os.environ.get("FORWARD_URL", DEFAULT_FORWARD_URL)
    sm = SocketModeClient(app_token=app_token, web_client=WebClient())
    sm.socket_mode_request_listeners.append(make_handler(forward_url=url, secret=secret))
    sm.connect()
    log.info("connected to Slack Socket Mode; forwarding interactions to %s", url)
    while True:
        time.sleep(30)
        if not sm.is_connected():
            log.warning("socket disconnected; reconnecting")
            sm.connect()


if __name__ == "__main__":
    sys.exit(main())
