"""Triage loop: call LLM -> parse -> validate -> retry -> route.

Guarantees (see IDEATION 4.4):
- up to LLM_MAX_ATTEMPTS (default 3) attempts; each failure appends the
  validation error to the next prompt
- after max attempts the run is `needs_review` - no approval, no CRM write
- every attempt (raw output, error, tokens, latency) is logged
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .config import get_settings
from .db import log_event
from .llm.base import LLMClient, get_client
from .runlog import get_run_by_message, insert_run
from .schemas import (
    Attempt,
    Category,
    InboundEmail,
    Route,
    TriageResponse,
    TriageResult,
    TriageStatus,
    new_id,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_FREE_MAIL = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "proton.me",
    "protonmail.com", "icloud.com", "aol.com", "gmx.com", "mail.com", "live.com",
}

_APPROVAL_CATEGORIES = {Category.sales_lead, Category.support_request}


def _load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def build_system_prompt() -> str:
    schema = json.dumps(TriageResult.model_json_schema(), indent=2)
    # NOTE: plain str.replace, not str.format - the prompt itself is full of JSON
    # braces (few-shot examples) which str.format would try to interpolate.
    return _load("triage_system.md").replace("{schema}", schema)


def build_user_prompt(email: InboundEmail) -> str:
    from_header = f"{email.from_name} <{email.from_email}>" if email.from_name else email.from_email
    return (
        _load("triage_user.md")
        .replace("{from_header}", from_header)
        .replace("{subject}", email.subject)
        .replace("{body}", email.body_text)
    )


def build_repair_prompt(error: str) -> str:
    return _load("repair.md").replace("{error}", error)


def strip_code_fences(text: str) -> str:
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # tolerate prose around a JSON object
    if text.startswith("{") and text.endswith("}"):
        return text
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_and_validate(raw: str) -> TriageResult:
    cleaned = strip_code_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e.msg} at pos {e.pos}") from e
    if not isinstance(data, dict):
        raise ValueError("output must be a JSON object")
    try:
        return TriageResult.model_validate(data)
    except ValidationError as e:
        errs = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5]
        )
        raise ValueError(f"schema validation failed: {errs}") from e


def apply_business_rules(result: TriageResult, email: InboundEmail) -> tuple[TriageResult, Optional[str]]:
    """Return (result, fatal_error). Sales/support need a contact_email.

    Fallback order: extracted -> email From address. If neither works the run
    must be quarantined (never written to CRM).
    """
    if result.category not in _APPROVAL_CATEGORIES:
        return result, None

    if not result.contact_email:
        from_email = (email.from_email or "").strip().lower()
        if from_email and "@" in from_email and not from_email.startswith("no-reply"):
            data = result.model_dump()
            data["contact_email"] = from_email
            result = TriageResult.model_validate(data)
        else:
            return result, "sales_lead/support_request requires a contact_email and no From address was available"

    # company must never come from a free-mail domain
    if result.company and email.from_email:
        domain = email.from_email.rsplit("@", 1)[-1].lower()
        if domain in _FREE_MAIL and result.company.strip().lower() in domain.split(".")[0]:
            data = result.model_dump()
            data["company"] = None
            result = TriageResult.model_validate(data)
    return result, None


def route_for(result: Optional[TriageResult], status: str) -> Route:
    if status != TriageStatus.ok.value or result is None:
        return Route.needs_review
    if result.category in _APPROVAL_CATEGORIES:
        return Route.approval
    return Route.log_only


def run_triage(
    email: InboundEmail,
    *,
    run_tag: Optional[str] = None,
    client: Optional[LLMClient] = None,
    persist: bool = True,
) -> TriageResponse:
    settings = get_settings()
    llm = client or get_client()
    fault = (settings.llm_fault_injection or "off").lower()

    # de-dup: same message_id already processed (eval re-runs pass a #run_tag suffix)
    if persist:
        existing = get_run_by_message(email.message_id)
        if existing:
            log_event("duplicate_message", run_id=existing["run_id"],
                      detail={"message_id": email.message_id})
            prev_triage = (
                TriageResult.model_validate_json(existing["triage_json"])
                if existing.get("triage_json") else None
            )
            return TriageResponse(
                run_id=existing["run_id"],
                status=TriageStatus.duplicate_message,
                route=Route(existing["route"]),
                triage=prev_triage,
                attempts=existing.get("attempt_count", 0),
            )

    run_id = new_id()
    system = build_system_prompt()
    user = build_user_prompt(email)
    messages: list[dict] = [{"role": "user", "content": user}]
    attempts: list[Attempt] = []
    result: Optional[TriageResult] = None
    error: Optional[str] = None
    total_latency = 0

    max_attempts = max(1, settings.llm_max_attempts)
    for i in range(1, max_attempts + 1):
        if fault == "always_invalid" or (fault == "first_attempt_invalid" and i == 1):
            from .llm.fake_client import FakeLLMClient

            resp = FakeLLMClient(mode="always_invalid").complete(system, messages)
        else:
            resp = llm.complete(system, messages, max_tokens=700, cache_system=True)
        total_latency += resp.latency_ms

        try:
            result = parse_and_validate(resp.text)
            result, fatal = apply_business_rules(result, email)
            if fatal:
                error = fatal
                result = None
            else:
                error = None
        except ValueError as e:
            error = str(e)
            result = None

        attempts.append(
            Attempt(
                attempt=i,
                raw_output=resp.text,
                error=error,
                latency_ms=resp.latency_ms,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                cache_read_tokens=resp.cache_read_tokens,
                cache_write_tokens=resp.cache_write_tokens,
            )
        )

        if error is None:
            break

        log_event(
            "triage_attempt_failed",
            run_id=run_id,
            detail={"attempt": i, "error": error[:500]},
        )
        # corrective retry: append the failure to the conversation
        repair = build_repair_prompt(error[:800])
        messages = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": resp.text[:4000]},
            {"role": "user", "content": repair},
        ]

    status = TriageStatus.ok if result is not None else TriageStatus.needs_review
    route = route_for(result, status.value)

    if persist:
        insert_run(
            run_id=run_id,
            email=email,
            attempts=attempts,
            triage=result,
            status=status.value,
            route=route.value,
            run_tag=run_tag,
            model=settings.llm_model,
            latency_ms=total_latency,
        )
        log_event(
            "triage_completed",
            run_id=run_id,
            detail={
                "status": status.value,
                "route": route.value,
                "attempts": len(attempts),
                "category": result.category.value if result else None,
            },
        )

    resp = TriageResponse(
        run_id=run_id,
        status=status,
        route=route,
        triage=result,
        attempts=len(attempts),
    )

    # Approval creation lives here (not in the HTTP layer) so every entry point -
    # n8n webhook, eval direct mode, tests - produces the same object graph.
    if persist and route == Route.approval:
        from .approvals import create_approval, get_approval
        from .slack import make_approval_message

        approval_id = create_approval(resp)
        if approval_id:
            approval = get_approval(approval_id) or {}
            blocks, text = make_approval_message(
                resp, approval_id, approval.get("expires_at", "")
            )
            resp.approval_id = approval_id
            resp.slack_blocks = blocks
            resp.slack_text = text

    return resp
