"""TriageResult -> HubSpot property dicts.

n8n passes these straight to HubSpot HTTP Request nodes; Python owns the mapping so
it is unit-testable without touching the CRM.
"""
from __future__ import annotations

from typing import Any, Optional

from .schemas import Category, TriageResult

_FREE_MAIL = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "proton.me",
    "protonmail.com", "icloud.com", "aol.com", "gmx.com", "mail.com", "live.com",
}

TEST_PREFIX = "[TriageGate test] "  # every record this demo creates is clearly marked


def split_name(full: Optional[str]) -> tuple[str, str]:
    if not full:
        return "", ""
    parts = full.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def domain_of(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    dom = email.rsplit("@", 1)[-1].lower().strip(".")
    if not dom or dom in _FREE_MAIL:
        return None
    return dom


def contact_properties(triage: TriageResult) -> dict[str, Any]:
    first, last = split_name(triage.contact_name)
    props: dict[str, Any] = {}
    if triage.contact_email:
        props["email"] = str(triage.contact_email)
    if first:
        props["firstname"] = first
    if last:
        props["lastname"] = last
    if triage.company:
        props["company"] = triage.company
    if triage.category == Category.sales_lead:
        props["hs_lead_status"] = "NEW"
        props["lifecyclestage"] = "lead"
    return props


def company_properties(triage: TriageResult) -> Optional[dict[str, Any]]:
    if triage.category != Category.sales_lead or not triage.company:
        return None
    props: dict[str, Any] = {"name": TEST_PREFIX + triage.company[:190]}
    dom = domain_of(str(triage.contact_email) if triage.contact_email else None)
    if dom:
        props["domain"] = dom
    return props


def ticket_properties(triage: TriageResult, subject: str, run_id: str) -> dict[str, Any]:
    priority = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}[triage.urgency.value]
    return {
        "content": f"{triage.request_summary}\n\n[TriageGate test] run {run_id}",
        "hs_pipeline": "0",
        "hs_pipeline_stage": "1",
        "hs_ticket_priority": priority,
        "subject": TEST_PREFIX + (subject or triage.request_summary)[:190],
    }


def build_crm_payload(triage: TriageResult, *, subject: str = "", run_id: str = "") -> dict[str, Any]:
    """Everything n8n needs to write this record to HubSpot."""
    contact = contact_properties(triage)
    if triage.contact_email:
        # mark test records clearly: append tag to company only (emails must stay valid)
        contact["email"] = str(triage.contact_email)

    payload: dict[str, Any] = {
        "category": triage.category.value,
        "contact": contact,
        "company": company_properties(triage),
        "ticket": None,
        "associations": [],
        "test_marker": TEST_PREFIX.strip(),
    }
    if triage.category == Category.support_request:
        payload["ticket"] = ticket_properties(triage, subject, run_id)
        payload["associations"] = ["ticket_to_contact"]
    if triage.category == Category.sales_lead:
        payload["associations"] = ["company_to_contact", "contact_to_company"]
    return payload
