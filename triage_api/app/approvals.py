"""Approval state machine + idempotency.

The core invariant: exactly one approval row may ever move to `approved`, and only
while it is `pending` and unexpired. The transition is a single atomic UPDATE guarded
by `status='pending' AND expires_at > now`, so two concurrent Slack clicks yield one
`write_crm` and one `duplicate`. A second UNIQUE layer lives in crm_writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .config import get_settings
from .db import get_conn, log_event, transaction
from .schemas import (
    CrmResultIn,
    DecideOutcome,
    Route,
    TriageResponse,
    new_id,
    utcnow_iso,
)


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def create_approval(triage_resp: TriageResponse) -> Optional[str]:
    """Create a pending approval row for an approval-routed run. Returns approval_id."""
    if triage_resp.route != Route.approval:
        return None
    ttl = get_settings().approval_ttl_minutes
    now = datetime.now(timezone.utc)
    approval_id = new_id()
    expires_at = (now + timedelta(minutes=ttl)).isoformat()
    with transaction() as conn:
        existing = conn.execute(
            "SELECT approval_id FROM approvals WHERE run_id=?", (triage_resp.run_id,)
        ).fetchone()
        if existing:
            return existing["approval_id"]
        conn.execute(
            """INSERT INTO approvals (approval_id, run_id, status, created_at, expires_at, click_count)
               VALUES (?,?,?,?,?,0)""",
            (approval_id, triage_resp.run_id, "pending", now.isoformat(), expires_at),
        )
    log_event(
        "approval_created",
        run_id=triage_resp.run_id,
        approval_id=approval_id,
        detail={"expires_at": expires_at, "ttl_minutes": ttl},
    )
    return approval_id


def set_slack_ref(approval_id: str, channel: str, ts: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE approvals SET slack_channel=?, slack_ts=? WHERE approval_id=?",
            (channel, ts, approval_id),
        )
        return cur.rowcount == 1
    finally:
        conn.close()


def get_approval(approval_id: str) -> Optional[dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_approval_by_run(run_id: str) -> Optional[dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM approvals WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def decide(approval_id: str, *, decision: str, decided_by: str) -> tuple[DecideOutcome, Optional[dict]]:
    """Atomic pending -> approved/rejected transition.

    Returns (outcome, crm_payload). crm_payload is non-None only for the single
    caller that won the transition and must write to the CRM.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with transaction() as conn:
        conn.execute(
            "UPDATE approvals SET click_count = click_count + 1 WHERE approval_id=?",
            (approval_id,),
        )
        cur = conn.execute(
            """UPDATE approvals
               SET status=?, decided_by=?, decided_at=?
               WHERE approval_id=? AND status='pending' AND expires_at > ?""",
            ("approved" if decision == "approve" else "rejected", decided_by, now_iso,
             approval_id, now_iso),
        )
        won = cur.rowcount == 1
        row = conn.execute(
            "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()

    if row is None:
        log_event("approval_not_found", approval_id=approval_id, detail={"decision": decision})
        return DecideOutcome.not_found, None

    approval = dict(row)

    if won:
        log_event(
            "approval_decided",
            run_id=approval["run_id"],
            approval_id=approval_id,
            detail={"decision": decision, "decided_by": decided_by},
        )
        if decision == "approve":
            payload = _build_crm_payload(approval["run_id"], approval_id)
            return DecideOutcome.write_crm, payload
        return DecideOutcome.rejected, None

    # transition lost - why?
    if approval["status"] in ("approved", "rejected", "crm_written", "crm_failed"):
        log_event(
            "click_duplicate",
            run_id=approval["run_id"],
            approval_id=approval_id,
            detail={"attempted": decision, "current_status": approval["status"]},
        )
        return DecideOutcome.duplicate, None
    if approval["status"] == "expired" or approval["expires_at"] <= now_iso:
        log_event(
            "click_on_expired",
            run_id=approval["run_id"],
            approval_id=approval_id,
            detail={"attempted": decision, "expires_at": approval["expires_at"]},
        )
        return DecideOutcome.expired, None
    log_event(
        "click_duplicate",
        run_id=approval["run_id"],
        approval_id=approval_id,
        detail={"attempted": decision, "current_status": approval["status"]},
    )
    return DecideOutcome.duplicate, None


def _build_crm_payload(run_id: str, approval_id: str) -> Optional[dict[str, Any]]:
    import json as _json

    from .crm_mapping import build_crm_payload

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT triage_json, input_json FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["triage_json"]:
        return None
    from .schemas import TriageResult

    triage = TriageResult.model_validate_json(row["triage_json"])
    subject = ""
    try:
        subject = _json.loads(row["input_json"]).get("subject", "")
    except (TypeError, ValueError):
        pass
    payload = build_crm_payload(triage, subject=subject, run_id=run_id)
    payload["approval_id"] = approval_id
    payload["run_id"] = run_id
    return payload


def decision_card(run_id: str, headline: str) -> tuple[str, list[dict[str, Any]]]:
    """Replacement text + Block Kit for chat.update after a decision / expiry."""
    from .slack import build_decision_blocks

    conn = get_conn()
    try:
        row = conn.execute("SELECT triage_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    summary = ""
    if row and row["triage_json"]:
        from .schemas import TriageResult

        summary = TriageResult.model_validate_json(row["triage_json"]).request_summary
    return headline, build_decision_blocks(headline, summary)


def record_crm_result(approval_id: str, result: CrmResultIn) -> dict[str, Any]:
    """Idempotent: crm_writes.approval_id is UNIQUE, a duplicate insert is ignored."""
    now = utcnow_iso()
    inserted = False
    with transaction() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO crm_writes
               (approval_id, status, hubspot_contact_id, hubspot_company_id,
                hubspot_ticket_id, error, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                approval_id,
                result.status,
                result.hubspot_contact_id,
                result.hubspot_company_id,
                result.hubspot_ticket_id,
                result.error,
                now,
            ),
        )
        inserted = cur.rowcount == 1

        new_status = "crm_written" if result.status == "success" else "crm_failed"
        conn.execute(
            "UPDATE approvals SET status=? WHERE approval_id=? AND status='approved'",
            (new_status, approval_id),
        )
        row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        approval = dict(row) if row else None
        run_id = approval["run_id"] if approval else None

    if run_id:
        if result.status == "success":
            ids = ",".join(
                x for x in (result.hubspot_contact_id, result.hubspot_company_id,
                            result.hubspot_ticket_id) if x
            )
            _update_run_result(run_id, "approved", f"crm_written:{ids}")
        else:
            _update_run_result(run_id, "approved", f"crm_failed:{(result.error or '')[:200]}")

    log_event(
        "crm_write_success" if result.status == "success" else "crm_write_failed",
        run_id=run_id,
        approval_id=approval_id,
        detail={
            "duplicate_insert": not inserted,
            "contact": result.hubspot_contact_id,
            "company": result.hubspot_company_id,
            "ticket": result.hubspot_ticket_id,
            "error": result.error,
        },
    )
    return {"inserted": inserted, "approval": approval}


def _update_run_result(run_id: str, decision: str, result: str) -> None:
    from .runlog import set_decision

    set_decision(run_id, decision, result)


def expire_due_approvals() -> list[dict[str, Any]]:
    """Mark overdue pending approvals expired; return them for Slack message updates."""
    now_iso = datetime.now(timezone.utc).isoformat()
    expired: list[dict[str, Any]] = []
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status='pending' AND expires_at <= ?",
            (now_iso,),
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE approvals SET status='expired', decided_at=? WHERE approval_id=? AND status='pending'",
                (now_iso, r["approval_id"]),
            )
            expired.append(dict(r))
    for a in expired:
        a["slack_text"], a["slack_blocks"] = decision_card(
            a["run_id"], "Expired - no action taken. Nothing was written to the CRM."
        )
        log_event(
            "approval_expired",
            run_id=a["run_id"],
            approval_id=a["approval_id"],
            detail={"expires_at": a["expires_at"]},
        )
        _update_run_result(a["run_id"], "expired", "ignored")
    return expired


def list_approvals(status: Optional[str] = None) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def crm_write_count(approval_id: str) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM crm_writes WHERE approval_id=?", (approval_id,)
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()
