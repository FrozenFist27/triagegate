"""Anthropic Claude client (official SDK).

Cost/latency choices for this project:
- claude-haiku-5-5 (cheapest tier, $0.10/$0.50 per MTok under 100K context)
- thinking disabled (simple structured classification; adaptive thinking would
  spend output tokens on reasoning we do not need)
- temperature left at API default (non-default values 400 on this model)
- prompt caching on the stable system prompt (few-shot examples + schema)
- max_tokens capped low: output is a small JSON object
"""
from __future__ import annotations

import time
from typing import Sequence

import anthropic

from .base import LLMResponse


class AnthropicClient:
    def __init__(self, model: str = "claude-haiku-5-5", api_key: str = "", max_retries: int = 2):
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key or None, max_retries=max_retries)

    def complete(
        self,
        system: str,
        messages: Sequence[dict],
        *,
        max_tokens: int = 700,
        cache_system: bool = True,
    ) -> LLMResponse:
        system_param: str | list[dict]
        if cache_system:
            system_param = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            system_param = system

        t0 = time.perf_counter()
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_param,
            messages=list(messages),
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        usage = resp.usage
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            latency_ms=latency_ms,
            model=getattr(resp, "model", self.model) or self.model,
        )
