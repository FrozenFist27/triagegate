"""Retry / quarantine behaviour: invalid model output is caught and never routed."""
from __future__ import annotations

import json

from app.llm.fake_client import FakeLLMClient
from app.schemas import Route, TriageStatus
from app.triage import parse_and_validate, run_triage, strip_code_fences


def test_valid_first_attempt(db_path, sample_email):
    resp = run_triage(sample_email(), client=FakeLLMClient(), persist=True)
    assert resp.status == TriageStatus.ok
    assert resp.attempts == 1
    assert resp.route == Route.approval


def test_invalid_then_valid_two_attempts(db_path, sample_email):
    resp = run_triage(sample_email(), client=FakeLLMClient(mode="first_attempt_invalid"))
    assert resp.status == TriageStatus.ok
    assert resp.attempts == 2


def test_never_valid_quarantined(db_path, sample_email):
    resp = run_triage(sample_email(), client=FakeLLMClient(mode="always_invalid"))
    assert resp.status == TriageStatus.needs_review
    assert resp.route == Route.needs_review
    assert resp.triage is None
    assert resp.attempts == 3
    # no approval row may exist for a quarantined run
    from app.approvals import get_approval_by_run

    assert get_approval_by_run(resp.run_id) is None

    # and the run log captured every raw attempt
    from app.runlog import get_run

    run = get_run(resp.run_id)
    attempts = json.loads(run["attempts_json"])
    assert len(attempts) == 3
    assert all(a["error"] for a in attempts)
    assert run["route"] == "needs_review"


def test_code_fenced_json_accepted(db_path, sample_email):
    resp = run_triage(sample_email(), client=FakeLLMClient(mode="code_fenced"))
    assert resp.status == TriageStatus.ok
    assert resp.attempts == 1


def test_attempts_logged_with_raw_outputs(db_path, sample_email):
    resp = run_triage(sample_email(), client=FakeLLMClient(mode="first_attempt_invalid"))
    from app.runlog import get_run

    run = get_run(resp.run_id)
    attempts = json.loads(run["attempts_json"])
    assert attempts[0]["raw_output"] == "not json at all"
    assert attempts[0]["error"] is not None
    assert attempts[1]["error"] is None
    assert json.loads(attempts[1]["raw_output"])["category"] == "sales_lead"


def test_repair_prompt_includes_error(db_path, sample_email):
    client = FakeLLMClient(mode="first_attempt_invalid")
    run_triage(sample_email(), client=client)
    assert len(client.calls) == 2
    repair_msgs = client.calls[1]["messages"]
    assert any("failed validation" in m["content"].lower() for m in repair_msgs if m["role"] == "user")


def test_fault_injection_env_first_attempt(db_path, sample_email, monkeypatch):
    monkeypatch.setenv("LLM_FAULT_INJECTION", "first_attempt_invalid")
    from app.config import get_settings

    get_settings.cache_clear()
    # real (fake-provider) client is bypassed by the injection path
    resp = run_triage(sample_email(), client=FakeLLMClient(), persist=True)
    assert resp.attempts == 2
    assert resp.status == TriageStatus.ok
    get_settings.cache_clear()


def test_parse_helpers():
    assert strip_code_fences("```json\n{\"a\":1}\n```") == '{"a":1}'
    assert parse_and_validate('junk {"category":"spam","confidence":0.5,"company":null,'
                              '"contact_name":null,"contact_email":null,'
                              '"request_summary":"Ad.","urgency":"low",'
                              '"urgency_reason":""} trailing').category.value == "spam"


def test_duplicate_message_id_short_circuits(db_path, sample_email):
    first = run_triage(sample_email(message_id="dup-1"))
    second = run_triage(sample_email(message_id="dup-1"))
    assert second.status == TriageStatus.duplicate_message
    assert second.run_id == first.run_id
