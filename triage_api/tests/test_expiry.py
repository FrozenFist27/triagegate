"""Approval expiry: stale items expire, clicks on them do nothing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.approvals import create_approval, decide, expire_due_approvals, get_approval
from app.schemas import Route, TriageResponse, TriageResult


def _seed(db_path, ttl_minutes=1440):
    from app.db import get_conn

    triage = TriageResult.model_validate({
        "category": "support_request", "confidence": 0.9, "company": "Acme",
        "contact_name": "Dev", "contact_email": "dev@acme.example.com",
        "request_summary": "API down.", "urgency": "high",
        "urgency_reason": "Outage.",
    })
    run_id = f"run-exp-{db_path.stem}"
    conn = get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO runs (run_id, message_id, source, created_at, input_json,
           attempts_json, attempt_count, triage_json, status, route)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (run_id, f"mid-exp-{db_path.stem}", "eval", "2026-10-17T00:00:00+00:00",
         "{}", "[]", 1, triage.model_dump_json(), "ok", "approval"),
    )
    conn.close()
    resp = TriageResponse(run_id=run_id, status="ok", route=Route.approval,
                          triage=triage, attempts=1)
    aid = create_approval(resp)
    assert aid
    return aid, run_id


def test_overdue_pending_expires(db_path):

    aid, run_id = _seed(db_path)
    # force expiry into the past
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from app.db import get_conn

    conn = get_conn()
    conn.execute("UPDATE approvals SET expires_at=? WHERE approval_id=?", (past, aid))
    conn.close()

    expired = expire_due_approvals()
    assert [e["approval_id"] for e in expired] == [aid]
    assert get_approval(aid)["status"] == "expired"

    from app.runlog import get_run

    assert get_run(run_id)["decision"] == "expired"
    # running again must not double-expire
    assert expire_due_approvals() == []


def test_click_on_expired_returns_expired_no_crm(db_path):
    aid, run_id = _seed(db_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from app.db import get_conn

    conn = get_conn()
    conn.execute("UPDATE approvals SET expires_at=? WHERE approval_id=?", (past, aid))
    conn.close()

    outcome, payload = decide(aid, decision="approve", decided_by="late-clicker")
    assert outcome.value == "expired"
    assert payload is None
    assert get_approval(aid)["status"] != "approved"

    from app.db import get_conn as gc

    c = gc()
    n = c.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event='click_on_expired' AND approval_id=?",
        (aid,),
    ).fetchone()["n"]
    c.close()
    assert n == 1


def test_not_yet_due_not_expired(db_path):
    aid, _ = _seed(db_path, ttl_minutes=1440)
    assert expire_due_approvals() == []
    assert get_approval(aid)["status"] == "pending"
