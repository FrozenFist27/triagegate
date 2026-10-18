from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import listener

SECRET = "unit-test-secret"
URL = "http://n8n.test/webhook/slack-actions"
PAYLOAD = {
    "type": "block_actions",
    "user": {"id": "U1", "username": "rev"},
    "actions": [{"action_id": "triage_approve", "value": "appr-1"}],
}


class FakeSocket:
    def __init__(self, events):
        self.responses = []
        self.events = events

    def send_socket_mode_response(self, resp):
        self.events.append("ack")
        self.responses.append(resp)


def make_http(events, status=200):
    seen = []

    def h(request: httpx.Request):
        events.append("forward")
        seen.append(request)
        return httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(h)), seen


def req(type_="interactive", payload=PAYLOAD, eid="env-1"):
    return SimpleNamespace(type=type_, payload=payload, envelope_id=eid)


def test_ack_then_forward_with_secret_and_roundtrip_body():
    events: list[str] = []
    http, seen = make_http(events)
    sock = FakeSocket(events)
    listener.make_handler(forward_url=URL, secret=SECRET, http=http)(sock, req())
    assert events == ["ack", "forward"]  # ack first
    assert sock.responses[0].envelope_id == "env-1"
    r = seen[0]
    assert r.headers[listener.SECRET_HEADER] == SECRET
    form = urllib.parse.parse_qs(r.content.decode())
    assert json.loads(form["payload"][0]) == PAYLOAD


def test_acks_even_when_forward_fails():
    events: list[str] = []
    http, seen = make_http(events, status=500)
    sock = FakeSocket(events)
    ok = listener.forward_interaction(PAYLOAD, url=URL, secret=SECRET, client=http, sleep=lambda s: None)
    assert ok is False and len(seen) == 3  # retried, then gave up
    events.clear()
    listener.make_handler(forward_url=URL, secret=SECRET, http=http)(sock, req())
    assert events[0] == "ack"


def test_non_interactive_events_acked_not_forwarded():
    events: list[str] = []
    http, seen = make_http(events)
    sock = FakeSocket(events)
    listener.make_handler(forward_url=URL, secret=SECRET, http=http)(sock, req(type_="events_api"))
    assert events == ["ack"] and not seen


def test_duplicate_clicks_each_forwarded_and_acked():
    events: list[str] = []
    http, seen = make_http(events)
    sock = FakeSocket(events)
    h = listener.make_handler(forward_url=URL, secret=SECRET, http=http)
    h(sock, req(eid="e1"))
    h(sock, req(eid="e2"))
    # dedupe is the approval path's job (see triage_api/tests/test_socket_forward.py)
    assert len(seen) == 2 and len(sock.responses) == 2
