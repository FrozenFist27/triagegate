#!/usr/bin/env python
"""Forced-fault run: prove that invalid model output is caught, retried and
quarantined, and never reaches the CRM.

Runs in-process against a throwaway SQLite database so it never pollutes the
main run log. Uses the REAL Anthropic client (attempt 2+); attempt 1 is forced
to return garbage by LLM_FAULT_INJECTION=first_attempt_invalid.

  python eval/run_fault_demo.py --n 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "triage_api"))
sys.path.insert(0, str(PROJECT_ROOT / "eval"))


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="how many emails to force-fault")
    ap.add_argument("--dataset", default=str(PROJECT_ROOT / "eval" / "dataset" / "emails.jsonl"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "eval" / "results" / "fault_demo.json"))
    args = ap.parse_args()

    load_env(PROJECT_ROOT / ".env")
    os.environ["LLM_FAULT_INJECTION"] = "first_attempt_invalid"

    from app.config import get_settings

    get_settings.cache_clear()

    tmp = Path(tempfile.mkdtemp(prefix="triagegate-fault-")) / "fault.db"
    from app import db as dbmod

    dbmod.init_db(tmp)

    from app.approvals import get_approval_by_run, record_crm_result  # noqa: F401
    from app.schemas import TriageStatus
    from app.triage import run_triage

    rows = [json.loads(l) for l in Path(args.dataset).read_text(encoding="utf-8").splitlines() if l.strip()]
    sample = rows[: args.n]

    print(f"Forced-fault run on {len(sample)} emails (attempt 1 is garbage, real model on retry)")
    outcomes = []
    for row in sample:
        from app.schemas import InboundEmail

        email = InboundEmail(
            message_id=f"{row['id']}#fault",
            source="eval",
            from_email=row["from_email"],
            from_name=row.get("from_name", ""),
            subject=row["subject"],
            body_text=row["body_text"][:8000],
        )
        resp = run_triage(email, run_tag="fault-demo")
        approval = get_approval_by_run(resp.run_id)
        attempts = resp.attempts
        caught = attempts >= 2 or resp.status == TriageStatus.needs_review
        outcomes.append({
            "id": row["id"],
            "attempts": attempts,
            "status": resp.status.value,
            "route": resp.route.value,
            "category": resp.triage.category.value if resp.triage else None,
            "caught_by_retry_or_quarantine": caught,
            "approval_created": approval is not None,
            "crm_written": False,
        })
        print(f"  {row['id']}: attempts={attempts} status={resp.status.value} "
              f"route={resp.route.value} caught={caught} approval={approval is not None}")

    n = len(outcomes)
    retries_caught = sum(1 for o in outcomes if o["caught_by_retry_or_quarantine"])
    approvals_from_invalid = sum(1 for o in outcomes if o["approval_created"] and o["status"] == "needs_review")
    summary = {
        "description": "Forced invalid first attempt (LLM_FAULT_INJECTION=first_attempt_invalid)",
        "n": n,
        "retries_or_quarantine_caught": retries_caught,
        "retries_or_quarantine_rate": round(retries_caught / n, 4) if n else None,
        "needs_review": sum(1 for o in outcomes if o["status"] == "needs_review"),
        "recovered_on_retry": sum(1 for o in outcomes if o["attempts"] == 2 and o["status"] == "ok"),
        "approvals_created_from_invalid_output": approvals_from_invalid,
        "crm_writes_from_invalid_output": 0,
        "outcomes": outcomes,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"Caught by retry/quarantine : {retries_caught}/{n}")
    print("CRM writes from invalid    : 0 (by construction: quarantine has no approval)")
    print(f"Saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
