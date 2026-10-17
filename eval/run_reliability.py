#!/usr/bin/env python
"""Live reliability proofs, run THROUGH n8n (webhook -> triage-api -> Slack -> ...).

  fault   Needs the triage-api container started with LLM_FAULT_INJECTION=<mode>:
            first_attempt_invalid  attempt 1 is garbage, the real model answers on the retry
            always_invalid         every attempt is garbage -> quarantine (needs_review)
          Proves invalid output is retried / quarantined and never reaches Slack or the CRM.
  expiry  Needs the triage-api container started with APPROVAL_TTL_MINUTES=2.
          Ingests leads, waits for WF3 to expire them, then clicks Approve: nothing is written.

  python eval/run_reliability.py fault --mode first_attempt_invalid --n 5
  python eval/run_reliability.py fault --mode always_invalid --n 3
  python eval/run_reliability.py expiry --n 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import dotenv_values
from simulate_click import signed_request

ENV = dotenv_values(ROOT / ".env")
API = "http://localhost:8000"
N8N = "http://localhost:5678/webhook"
HDR = {"X-Internal-Token": ENV["TRIAGE_API_TOKEN"]}
ROWS = [json.loads(line) for line in (ROOT / "eval/dataset/emails.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def ingest(row: dict, tag: str) -> dict:
    body = {"message_id": f"{row['id']}#{tag}", "source": "eval", "run_tag": tag,
            "from_email": row["from_email"], "from_name": row.get("from_name", ""),
            "subject": row["subject"], "body_text": row["body_text"]}
    r = httpx.post(f"{N8N}/triage-ingest", json=body, headers={"X-Ingest-Token": ENV["N8N_INGEST_TOKEN"]}, timeout=180)
    r.raise_for_status()
    return r.json()


def get(path: str):
    return httpx.get(f"{API}{path}", headers=HDR, timeout=30).json()


def mock_counts() -> dict | None:
    try:
        return httpx.get("http://localhost:8010/_mock/state", timeout=5).json()["counts"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def cmd_fault(a) -> dict:
    health = httpx.get(f"{API}/health").json()
    tag = f"fault-{a.mode}"
    pick = [r for r in ROWS if r["label"]["category"] in ("sales_lead", "support_request")][: a.n]
    before = mock_counts()
    n_writes_before = len(get("/crm-writes"))
    outs = []
    for r in pick:
        o = ingest(r, tag)
        run = next((x for x in get(f"/runs?limit=500&run_tag={tag}") if x["run_id"] == o["run_id"]), {})
        attempts = json.loads(run.get("attempts_json") or "[]") if isinstance(run.get("attempts_json"), str) else run.get("attempts", [])
        outs.append({"id": r["id"], "attempts": o["attempts"], "status": o["status"], "route": o["route"],
                     "approval_created": bool(o.get("approval_id")), "slack_posted": o.get("slack_posted"),
                     "first_attempt_error": (attempts[0].get("error") if attempts else None)})
        print(f"  {r['id']}: attempts={o['attempts']} status={o['status']} route={o['route']} "
              f"approval={bool(o.get('approval_id'))} slack_posted={o.get('slack_posted')}")
    time.sleep(2)
    after = mock_counts()
    res = {
        "generated_at": datetime.now(UTC).isoformat(),
        "fault_injection": a.mode, "container_reported_model": health.get("model"), "n": len(outs),
        "caught_and_retried": sum(1 for o in outs if o["attempts"] >= 2),
        "recovered_on_retry": sum(1 for o in outs if o["attempts"] == 2 and o["status"] == "ok"),
        "quarantined_needs_review": sum(1 for o in outs if o["status"] == "needs_review"),
        "approvals_created_from_invalid_output": sum(1 for o in outs if o["status"] == "needs_review" and o["approval_created"]),
        "slack_cards_posted_for_quarantined": sum(1 for o in outs if o["status"] == "needs_review" and o["slack_posted"]),
        "crm_writes_during_run": len(get("/crm-writes")) - n_writes_before,
        "crm_records_created_during_run": (None if not (before and after) else
                                         {k: after[k] - before[k] for k in after}),
        "outcomes": outs,
    }
    return res


def cmd_expiry(a) -> dict:
    health = httpx.get(f"{API}/health").json()
    ttl = health["approval_ttl_minutes"]
    if ttl > 5:
        sys.exit(f"triage-api TTL is {ttl} min; restart it with APPROVAL_TTL_MINUTES=2 first")
    tag = "expiry-demo"
    leads = [r for r in ROWS if r["label"]["category"] == "sales_lead"][-a.n:]
    ids = []
    for r in leads:
        o = ingest(r, tag)
        if o.get("approval_id"):
            ids.append(o["approval_id"])
    print(f"created {len(ids)} approvals with TTL={ttl} min; waiting for WF3 to expire them ...")
    before_writes = len(get("/crm-writes"))
    t0 = time.time()
    while time.time() - t0 < 300:
        st = {x["approval_id"]: x["status"] for x in get("/approvals")}
        if all(st.get(i) == "expired" for i in ids):
            break
        time.sleep(10)
    waited = round(time.time() - t0)
    for i in ids:
        raw, headers = signed_request(i, "approve", ENV["SLACK_SIGNING_SECRET"], "eval-runner")
        httpx.post(f"{N8N}/slack-actions", content=raw, headers=headers, timeout=30)
    time.sleep(6)
    ev = get("/events?limit=2000")
    st = {x["approval_id"]: x["status"] for x in get("/approvals")}
    return {
        "generated_at": datetime.now(UTC).isoformat(), "ttl_minutes": ttl, "n": len(ids),
        "waited_seconds_for_expiry": waited,
        "status_after_clicks": [st.get(i) for i in ids],
        "approval_expired_events": sum(1 for e in ev if e["event"] == "approval_expired" and e["approval_id"] in ids),
        "click_on_expired_events": sum(1 for e in ev if e["event"] == "click_on_expired" and e["approval_id"] in ids),
        "crm_writes_created": len(get("/crm-writes")) - before_writes,
        "mock_counts": mock_counts(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fault")
    f.add_argument("--mode", required=True, choices=["first_attempt_invalid", "always_invalid"])
    f.add_argument("--n", type=int, default=5)
    e = sub.add_parser("expiry")
    e.add_argument("--n", type=int, default=3)
    a = ap.parse_args()
    res = cmd_fault(a) if a.cmd == "fault" else cmd_expiry(a)
    name = f"fault_{a.mode}.json" if a.cmd == "fault" else "expiry_demo.json"
    out = ROOT / "eval/results" / name
    out.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "outcomes"}, indent=2))
    print("saved", out)


if __name__ == "__main__":
    main()
