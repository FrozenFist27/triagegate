"""The key reliability claim: N clicks -> exactly one write_crm."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.approvals import (
    create_approval,
    crm_write_count,
    decide,
    get_approval,
    record_crm_result,
)
from app.schemas import CrmResultIn, Route, TriageResponse, TriageResult


def make_approval(db_path) -> str:
    triage = TriageResult.model_validate({
        "category": "sales_lead", "confidence": 0.9, "company": "Acme",
        "contact_name": "Jane Doe", "contact_email": "jane@acme.example.com",
        "request_summary": "Wants a demo.", "urgency": "medium",
        "urgency_reason": "Timeframe stated.",
    })
    resp = TriageResponse(
        run_id="run-" + db_path.stem, status="ok", route=Route.approval,
        triage=triage, attempts=1,
    )
    # runs row must exist for the FK
    from app.db import get_conn

    conn = get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO runs (run_id, message_id, source, created_at, input_json,
           attempts_json, attempt_count, triage_json, status, route)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (resp.run_id, "mid-" + db_path.stem, "eval", "2026-10-17T00:00:00+00:00",
         "{}", "[]", 1, triage.model_dump_json(), "ok", "approval"),
    )
    conn.close()
    approval_id = create_approval(resp)
    assert approval_id
    return approval_id


def test_approve_once_returns_write_crm(db_path):
    aid = make_approval(db_path)
    outcome, payload = decide(aid, decision="approve", decided_by="tester")
    assert outcome.value == "write_crm"
    assert payload is not None and payload["approval_id"] == aid
    assert payload["contact"]["email"] == "jane@acme.example.com"
    assert get_approval(aid)["status"] == "approved"


def test_second_click_is_duplicate(db_path):
    aid = make_approval(db_path)
    first, _ = decide(aid, decision="approve", decided_by="tester")
    second, payload = decide(aid, decision="approve", decided_by="tester")
    assert first.value == "write_crm"
    assert second.value == "duplicate"
    assert payload is None


def test_reject_then_approve_is_duplicate(db_path):
    aid = make_approval(db_path)
    first, _ = decide(aid, decision="reject", decided_by="tester")
    second, payload = decide(aid, decision="approve", decided_by="tester")
    assert first.value == "rejected"
    assert second.value == "duplicate"
    assert payload is None


def test_concurrent_clicks_yield_exactly_one_write_crm(db_path):
    aid = make_approval(db_path)

    def click():
        outcome, payload = decide(aid, decision="approve", decided_by="racer")
        return outcome.value, payload is not None

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda _: click(), range(10)))

    winners = [r for r in results if r[0] == "write_crm" and r[1]]
    assert len(winners) == 1, results
    assert sum(1 for r in results if r[0] == "duplicate") == 9
    assert get_approval(aid)["status"] == "approved"
    assert get_approval(aid)["click_count"] == 10


def test_crm_result_inserted_once(db_path):
    aid = make_approval(db_path)
    decide(aid, decision="approve", decided_by="tester")
    body = CrmResultIn(status="success", hubspot_contact_id="123")
    r1 = record_crm_result(aid, body)
    r2 = record_crm_result(aid, body)
    assert r1["inserted"] is True
    assert r2["inserted"] is False
    assert crm_write_count(aid) == 1
    assert get_approval(aid)["status"] == "crm_written"

    from app.runlog import get_run

    run = get_run(get_approval(aid)["run_id"])
    assert run["decision"] == "approved"
    assert run["result"].startswith("crm_written:123")


def test_crm_failed_status_recorded(db_path):
    aid = make_approval(db_path)
    decide(aid, decision="approve", decided_by="tester")
    record_crm_result(aid, CrmResultIn(status="failed", error="429 rate limited"))
    assert get_approval(aid)["status"] == "crm_failed"
    from app.runlog import get_run

    assert get_run(get_approval(aid)["run_id"])["result"].startswith("crm_failed:")


def test_duplicate_clicks_are_event_logged(db_path):
    from app.db import get_conn

    aid = make_approval(db_path)
    decide(aid, decision="approve", decided_by="tester")
    decide(aid, decision="approve", decided_by="tester")
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event='click_duplicate' AND approval_id=?",
        (aid,),
    ).fetchone()["n"]
    conn.close()
    assert n == 1
