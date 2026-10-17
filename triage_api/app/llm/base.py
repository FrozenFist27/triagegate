"""LLMClient protocol - provider abstraction so Anthropic is swappable."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    model: str = ""


class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        messages: Sequence[dict],
        *,
        max_tokens: int = 700,
        cache_system: bool = True,
    ) -> LLMResponse:  # pragma: no cover - protocol
        ...


def get_client(provider: str | None = None):
    """Factory: build the configured provider client (anthropic | fake)."""
    from ..config import get_settings

    s = get_settings()
    name = (provider or s.llm_provider).lower()
    if name == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient(model=s.llm_model, api_key=s.anthropic_api_key, base_url=s.llm_base_url)
    if name == "fake":
        from .fake_client import FakeLLMClient

        return FakeLLMClient()
    raise ValueError(f"Unknown LLM provider: {name}")
