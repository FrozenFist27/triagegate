"""Category -> route mapping and business rules."""
from __future__ import annotations

import json

from app.llm.fake_client import FakeLLMClient
from app.schemas import InboundEmail, Route, TriageResult, TriageStatus
from app.triage import apply_business_rules, route_for, run_triage


def _result(**over):
    base = {
        "category": "sales_lead",
        "confidence": 0.9,
        "company": "Acme",
        "contact_name": "Jane Doe",
        "contact_email": "jane@acme.example.com",
        "request_summary": "Wants a demo.",
        "urgency": "medium",
        "urgency_reason": "Timeframe stated.",
    }
    base.update(over)
    return TriageResult.model_validate(base)


def _email(**over):
    base = dict(
        message_id="m1", source="webhook", from_email="jane@acme.example.com",
        from_name="Jane", subject="s", body_text="b",
        received_at="2026-10-17T10:00:00+00:00",
    )
    base.update(over)
    return InboundEmail(**base)


def test_lead_and_support_route_to_approval():
    assert route_for(_result(category="sales_lead"), "ok") == Route.approval
    assert route_for(_result(category="support_request"), "ok") == Route.approval


def test_invoice_and_spam_log_only():
    assert route_for(_result(category="invoice"), "ok") == Route.log_only
    assert route_for(_result(category="spam"), "ok") == Route.log_only


def test_failed_status_routes_needs_review():
    assert route_for(None, "needs_review") == Route.needs_review


def test_missing_contact_email_falls_back_to_from():
    r = _result(contact_email=None)
    email = _email(from_email="ops@beta-industries.example.com")
    out, err = apply_business_rules(r, email)
    assert err is None
    assert str(out.contact_email) == "ops@beta-industries.example.com"


def test_missing_contact_email_and_no_from_is_fatal():
    r = _result(contact_email=None, category="support_request")
    out, err = apply_business_rules(r, _email(from_email=""))
    assert err is not None


def test_invoice_needs_no_contact_email():
    r = _result(category="invoice", contact_email=None, company=None, contact_name=None)
    out, err = apply_business_rules(r, _email(from_email=""))
    assert err is None


def test_free_mail_company_not_taken_from_domain():
    r = _result(company="gmail", contact_email="jane@gmail.com")
    out, err = apply_business_rules(r, _email(from_email="jane@gmail.com"))
    assert err is None
    assert out.company is None


def test_lead_route_creates_approval_row(db_path):
    resp = run_triage(_email(message_id="lead-1"), client=FakeLLMClient())
    assert resp.route == Route.approval
    from app.approvals import get_approval_by_run

    a = get_approval_by_run(resp.run_id)
    assert a is not None and a["status"] == "pending"


def test_invoice_route_creates_no_approval(db_path):
    client = FakeLLMClient(responder=lambda msgs: json.dumps({
        "category": "invoice", "confidence": 0.9, "company": None, "contact_name": None,
        "contact_email": None, "request_summary": "Please find the invoice attached.",
        "urgency": "low", "urgency_reason": "",
    }))
    resp = run_triage(_email(message_id="inv-1"), client=client)
    assert resp.route == Route.log_only
    from app.approvals import get_approval_by_run

    assert get_approval_by_run(resp.run_id) is None


def test_never_valid_creates_no_approval(db_path):
    resp = run_triage(_email(message_id="bad-1"), client=FakeLLMClient(mode="always_invalid"))
    assert resp.status == TriageStatus.needs_review
    from app.approvals import get_approval_by_run

    assert get_approval_by_run(resp.run_id) is None
