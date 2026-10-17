"""Fake LLM client for tests and fault injection - no network, no tokens spent."""
from __future__ import annotations

import json
from typing import Callable, Sequence

from .base import LLMResponse

VALID_DEFAULT = {
    "category": "sales_lead",
    "confidence": 0.9,
    "company": "Acme Widgets",
    "contact_name": "Jane Doe",
    "contact_email": "jane@acme-widgets.example.com",
    "request_summary": "Wants a demo of the product next week.",
    "urgency": "medium",
    "urgency_reason": "Specific request with a timeframe.",
}


class FakeLLMClient:
    """Returns scripted responses.

    responder(msgs) -> str lets tests drive exact behaviour; the default
    responder returns a valid JSON object, or honours `mode`:
      - "valid"            (default)
      - "first_attempt_invalid"  first call invalid, then valid
      - "always_invalid"   every call invalid
      - "code_fenced"      valid JSON wrapped in ```json fences
    """

    def __init__(self, mode: str = "valid", responder: Callable[[Sequence[dict]], str] | None = None):
        self.mode = mode
        self.responder = responder
        self.calls: list[dict] = []

    def complete(
        self,
        system: str,
        messages: Sequence[dict],
        *,
        max_tokens: int = 700,
        cache_system: bool = True,
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        if self.responder is not None:
            text = self.responder(messages)
        elif self.mode == "always_invalid":
            text = "not json at all"
        elif self.mode == "first_attempt_invalid":
            text = "not json at all" if len(self.calls) == 1 else json.dumps(VALID_DEFAULT)
        elif self.mode == "code_fenced":
            text = "```json\n" + json.dumps(VALID_DEFAULT) + "\n```"
        else:
            text = json.dumps(VALID_DEFAULT)
        return LLMResponse(
            text=text,
            input_tokens=1000,
            output_tokens=150,
            latency_ms=5,
            model="fake",
        )
