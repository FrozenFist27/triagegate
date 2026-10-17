"""FastAPI app: triage, approvals, CRM results, expiry, run log."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response

from .approvals import (
    decide,
    expire_due_approvals,
    get_approval,
    list_approvals,
    record_crm_result,
    set_slack_ref,
)
from .config import get_settings
from .db import ensure_database, log_event
from .runlog import export_jsonl, list_runs
from .schemas import (
    CrmResultIn,
    DecideIn,
    DecideOutcome,
    DecideResponse,
    ExpireResponse,
    InboundEmail,
    SlackRefIn,
    TriageResponse,
)
from .slack import (
    extract_action,
    parse_slack_payload,
    verify_slack_signature,
)
from .triage import run_triage

app = FastAPI(title="TriageGate triage-api", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    ensure_database()


def require_token(x_internal_token: Optional[str] = Header(default=None)) -> None:
    expected = get_settings().triage_api_token
    if not x_internal_token or x_internal_token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-Internal-Token")


@app.get("/health")
def health() -> dict:
    from .db import get_conn

    try:
        conn = get_conn()
        conn.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception as e:  # pragma: no cover
        db_ok = False
        return {"status": "error", "db": False, "detail": str(e)[:200]}
    s = get_settings()
    return {
        "status": "ok",
        "db": db_ok,
        "provider": s.llm_provider,
        "model": s.llm_model,
        "approval_ttl_minutes": s.approval_ttl_minutes,
    }


@app.post("/triage", response_model=TriageResponse, dependencies=[Depends(require_token)])
def triage(email: InboundEmail, run_tag: Optional[str] = Query(default=None)) -> TriageResponse:
    # approval row + Slack blocks are created inside run_triage so that the n8n
    # webhook path, eval direct mode and tests all behave identically.
    return run_triage(email, run_tag=run_tag)


@app.post("/approvals/{approval_id}/slack-ref", dependencies=[Depends(require_token)])
def slack_ref(approval_id: str, body: SlackRefIn) -> dict:
    ok = set_slack_ref(approval_id, body.channel, body.ts)
    if not ok:
        raise HTTPException(status_code=404, detail="approval not found")
    log_event("slack_ref_saved", approval_id=approval_id,
              detail={"channel": body.channel, "ts": body.ts})
    return {"ok": True}


@app.post("/approvals/decide", response_model=DecideResponse, dependencies=[Depends(require_token)])
def approvals_decide(body: DecideIn) -> DecideResponse:
    ok, reason = verify_slack_signature(
        raw_body=body.raw_body, signature=body.signature, timestamp=body.timestamp
    )
    if not ok:
        log_event("invalid_signature", detail={"reason": reason})
        return DecideResponse(
            outcome=DecideOutcome.invalid_signature,
            message=f"signature rejected: {reason}",
        )

    try:
        payload = parse_slack_payload(body.raw_body)
    except ValueError as e:
        log_event("malformed_payload", detail={"error": str(e)})
        return DecideResponse(outcome=DecideOutcome.malformed, message=str(e))

    action_id, approval_id, user = extract_action(payload)
    if not approval_id or action_id not in ("triage_approve", "triage_reject"):
        log_event("malformed_payload", detail={"action_id": action_id})
        return DecideResponse(outcome=DecideOutcome.malformed, message="missing approval action")

    decision = "approve" if action_id == "triage_approve" else "reject"
    decided_by = user.get("username") or user.get("id") or "unknown"
    outcome, crm_payload = decide(approval_id, decision=decision, decided_by=decided_by)
    approval = get_approval(approval_id)
    return DecideResponse(outcome=outcome, approval=approval, crm_payload=crm_payload)


@app.post("/approvals/{approval_id}/crm-result", dependencies=[Depends(require_token)])
def crm_result(approval_id: str, body: CrmResultIn) -> dict:
    if get_approval(approval_id) is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return record_crm_result(approval_id, body)


@app.post("/approvals/expire", response_model=ExpireResponse, dependencies=[Depends(require_token)])
def expire() -> ExpireResponse:
    rows = expire_due_approvals()
    return ExpireResponse(expired=rows, count=len(rows))


@app.get("/runs", dependencies=[Depends(require_token)])
def runs(limit: int = Query(default=100, le=1000), run_tag: Optional[str] = None) -> list[dict]:
    return list_runs(limit=limit, run_tag=run_tag)


@app.get("/runs/export.jsonl", dependencies=[Depends(require_token)])
def runs_export(run_tag: Optional[str] = None) -> Response:
    content = export_jsonl(run_tag=run_tag)
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="runs_export.jsonl"'},
    )


@app.get("/approvals", dependencies=[Depends(require_token)])
def approvals(status: Optional[str] = None) -> list[dict]:
    return list_approvals(status=status)


@app.get("/events", dependencies=[Depends(require_token)])
def events(limit: int = Query(default=200, le=2000)) -> list[dict]:
    from .db import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
