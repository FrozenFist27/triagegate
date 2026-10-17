"""Run log: every triage run is recorded with input, model output, decision and result."""
from __future__ import annotations

import json
from typing import Any, Optional

from .db import get_conn
from .schemas import Attempt, InboundEmail, TriageResult, utcnow_iso


def insert_run(
    *,
    run_id: str,
    email: InboundEmail,
    attempts: list[Attempt],
    triage: Optional[TriageResult],
    status: str,
    route: str,
    run_tag: Optional[str],
    model: str,
    latency_ms: int,
) -> None:
    in_tok = sum(a.input_tokens for a in attempts)
    out_tok = sum(a.output_tokens for a in attempts)
    cache_read = sum(a.cache_read_tokens for a in attempts)
    cache_write = sum(a.cache_write_tokens for a in attempts)
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO runs
               (run_id, message_id, source, run_tag, received_at, created_at, input_json,
                attempts_json, attempt_count, triage_json, status, route, decision, result,
                model, input_tokens, output_tokens, cache_read_tokens, cost_usd, latency_ms)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                email.message_id,
                email.source,
                run_tag,
                email.received_at,
                utcnow_iso(),
                email.model_dump_json(),
                json.dumps([a.model_dump() for a in attempts], ensure_ascii=False),
                len(attempts),
                triage.model_dump_json() if triage else None,
                status,
                route,
                "log_only" if route == "log_only" else ("needs_review" if route == "needs_review" else None),
                "none",
                model,
                in_tok,
                out_tok,
                cache_read,
                # cost accounting: cached input billed at 0.1x on Anthropic
                _cost(in_tok, out_tok, cache_read, cache_write),
                latency_ms,
            ),
        )
    finally:
        conn.close()


def _cost(in_tok: int, out_tok: int, cache_read: int, cache_write: int) -> float:
    from .config import get_settings

    s = get_settings()
    billable_in = in_tok - cache_read - cache_write
    return round(
        (max(billable_in, 0) * s.llm_price_in_per_mtok
         + cache_read * s.llm_price_in_per_mtok * 0.10
         + cache_write * s.llm_price_in_per_mtok * 1.25
         + out_tok * s.llm_price_out_per_mtok) / 1_000_000,
        6,
    )


def set_decision(run_id: str, decision: str, result: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE runs SET decision=?, result=? WHERE run_id=?",
            (decision, result, run_id),
        )
    finally:
        conn.close()


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_run_by_message(message_id: str) -> Optional[dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM runs WHERE message_id=?", (message_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_runs(limit: int = 100, run_tag: Optional[str] = None) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        if run_tag:
            rows = conn.execute(
                "SELECT * FROM runs WHERE run_tag=? ORDER BY created_at DESC LIMIT ?",
                (run_tag, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def export_jsonl(run_tag: Optional[str] = None) -> str:
    runs = list_runs(limit=100000, run_tag=run_tag)
    return "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in runs)
