"""TriageResult -> HubSpot property mapping."""
from __future__ import annotations

from app.crm_mapping import (
    TEST_PREFIX,
    build_crm_payload,
    company_properties,
    contact_properties,
    domain_of,
    split_name,
    ticket_properties,
)
from app.schemas import TriageResult


def lead(**over):
    base = {
        "category": "sales_lead", "confidence": 0.9, "company": "Acme Widgets",
        "contact_name": "Jane Doe", "contact_email": "jane@acme-widgets.example.com",
        "request_summary": "Wants a demo.", "urgency": "medium",
        "urgency_reason": "Timeframe.",
    }
    base.update(over)
    return TriageResult.model_validate(base)


def support(**over):
    base = {
        "category": "support_request", "confidence": 0.95, "company": "Acme",
        "contact_name": "Sam Ito", "contact_email": "sam@acme.example.com",
        "request_summary": "API returning 500.", "urgency": "high",
        "urgency_reason": "Outage.",
    }
    base.update(over)
    return TriageResult.model_validate(base)


def test_lead_maps_to_contact_and_company():
    p = build_crm_payload(lead(), subject="Demo request", run_id="r1")
    assert p["contact"]["email"] == "jane@acme-widgets.example.com"
    assert p["contact"]["firstname"] == "Jane" and p["contact"]["lastname"] == "Doe"
    assert p["contact"]["hs_lead_status"] == "NEW"
    assert p["contact"]["lifecyclestage"] == "lead"
    assert p["company"]["name"].startswith(TEST_PREFIX)
    assert p["company"]["domain"] == "acme-widgets.example.com"
    assert p["ticket"] is None
    assert "company_to_contact" in p["associations"]


def test_support_maps_to_ticket_with_priority():
    p = build_crm_payload(support(), subject="500 errors", run_id="r2")
    assert p["ticket"] is not None
    assert p["ticket"]["hs_ticket_priority"] == "HIGH"
    assert p["ticket"]["subject"].startswith(TEST_PREFIX)
    assert p["ticket"]["hs_pipeline"] == "0"
    assert p["company"] is None
    assert p["contact"]["email"] == "sam@acme.example.com"


def test_ticket_priority_mapping():
    for urg, expected in (("low", "LOW"), ("medium", "MEDIUM"), ("high", "HIGH")):
        t = support(urgency=urg)
        assert ticket_properties(t, "s", "r")["hs_ticket_priority"] == expected


def test_name_splitting():
    assert split_name("Jane Doe") == ("Jane", "Doe")
    assert split_name("Mary Ann Smith") == ("Mary Ann", "Smith")
    assert split_name("Plato") == ("Plato", "")
    assert split_name(None) == ("", "")


def test_free_mail_has_no_company_domain():
    assert domain_of("jane@gmail.com") is None
    assert domain_of("jane@acme.example.com") == "acme.example.com"
    assert domain_of(None) is None


def test_free_mail_lead_has_no_company_domain():
    p = build_crm_payload(
        lead(contact_email="jane@gmail.com", company=None), subject="", run_id="r3"
    )
    assert p["company"] is None


def test_company_marked_as_test_record():
    assert company_properties(lead())["name"].startswith("[TriageGate test]")


def test_no_invented_contact_email():
    p = build_crm_payload(lead(contact_email=None, contact_name=None), subject="", run_id="r4")
    assert "email" not in p["contact"]
    assert "firstname" not in p["contact"]


def test_contact_properties_direct():
    props = contact_properties(support())
    assert props["firstname"] == "Sam"
    assert props["lastname"] == "Ito"
    assert "hs_lead_status" not in props  # support is not a lead
