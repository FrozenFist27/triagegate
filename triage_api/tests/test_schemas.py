"""TriageResult schema strictness."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import TriageResult

VALID = {
    "category": "sales_lead",
    "confidence": 0.9,
    "company": "Acme",
    "contact_name": "Jane Doe",
    "contact_email": "jane@acme.example.com",
    "request_summary": "Wants a demo.",
    "urgency": "medium",
    "urgency_reason": "Timeframe stated.",
}


def test_valid_result():
    r = TriageResult.model_validate(VALID)
    assert r.category.value == "sales_lead"
    assert r.confidence == 0.9


def test_extra_key_rejected():
    data = {**VALID, "hallucinated_field": "nope"}
    with pytest.raises(ValidationError):
        TriageResult.model_validate(data)


def test_bad_enum_rejected():
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**VALID, "category": "rocket_science"})


def test_bad_email_rejected():
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**VALID, "contact_email": "not-an-email"})


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**VALID, "confidence": 1.5})
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**VALID, "confidence": -0.1})


def test_summary_length_enforced():
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**VALID, "request_summary": "x" * 301})


def test_null_company_allowed():
    TriageResult.model_validate({**VALID, "company": None, "contact_name": None})
