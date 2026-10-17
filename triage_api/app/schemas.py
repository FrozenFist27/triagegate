"""Pydantic schemas: inbound email, strict LLM output, API request/response models."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


class Category(str, Enum):
    sales_lead = "sales_lead"
    support_request = "support_request"
    invoice = "invoice"
    spam = "spam"


class Urgency(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Route(str, Enum):
    approval = "approval"
    log_only = "log_only"
    needs_review = "needs_review"


class TriageStatus(str, Enum):
    ok = "ok"
    needs_review = "needs_review"
    duplicate_message = "duplicate_message"


class InboundEmail(BaseModel):
    """Email handed from n8n (webhook / IMAP / eval) to POST /triage."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=400)
    source: Literal["imap", "webhook", "eval"] = "webhook"
    from_email: str = Field(default="", max_length=320)
    from_name: str = Field(default="", max_length=200)
    subject: str = Field(default="", max_length=500)
    body_text: str = Field(default="", max_length=8000)
    received_at: str = Field(default_factory=utcnow_iso)

    @field_validator("body_text")
    @classmethod
    def truncate_body(cls, v: str) -> str:
        return v[:8000]


class TriageResult(BaseModel):
    """Strict LLM output schema. extra='forbid' so hallucinated keys fail validation."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    company: Optional[str] = Field(default=None, max_length=200)
    contact_name: Optional[str] = Field(default=None, max_length=200)
    contact_email: Optional[EmailStr] = None
    request_summary: str = Field(min_length=1, max_length=300)
    urgency: Urgency
    urgency_reason: str = Field(default="", max_length=200)


class Attempt(BaseModel):
    attempt: int
    raw_output: str = ""
    error: Optional[str] = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class TriageResponse(BaseModel):
    run_id: str
    status: TriageStatus
    route: Route
    triage: Optional[TriageResult] = None
    attempts: int = 0
    approval_id: Optional[str] = None
    slack_blocks: list[dict[str, Any]] = Field(default_factory=list)
    slack_text: str = ""


class SlackRefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1, max_length=100)
    ts: str = Field(min_length=1, max_length=100)


class DecideIn(BaseModel):
    """n8n forwards the raw Slack interactivity body + its signature headers."""

    model_config = ConfigDict(extra="forbid")

    raw_body: str
    signature: str
    timestamp: str


class CrmResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "failed"]
    hubspot_contact_id: Optional[str] = None
    hubspot_company_id: Optional[str] = None
    hubspot_ticket_id: Optional[str] = None
    error: Optional[str] = None


class DecideOutcome(str, Enum):
    write_crm = "write_crm"
    rejected = "rejected"
    expired = "expired"
    duplicate = "duplicate"
    invalid_signature = "invalid_signature"
    not_found = "not_found"
    malformed = "malformed"


class DecideResponse(BaseModel):
    outcome: DecideOutcome
    approval: Optional[dict[str, Any]] = None
    crm_payload: Optional[dict[str, Any]] = None
    message: str = ""


class ExpireResponse(BaseModel):
    expired: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
