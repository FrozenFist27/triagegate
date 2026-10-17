#!/usr/bin/env python
"""Replay the 40 synthetic emails through TriageGate and compute metrics.

Modes:
  --mode api     POST to the triage-api directly (default; fastest)
  --mode n8n     POST to the n8n ingest webhook (exercises the full workflow)

No email is ever sent. Nothing here talks to IMAP.

Usage:
  python eval/run_eval.py --mode api --run-tag 2026-10-17
  python eval/run_eval.py --mode n8n --n8n-url http://localhost:5678/webhook/triage-ingest
  python eval/run_eval.py --simulate-clicks   # also drives approve/reject/double/expired
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent
sys.path.insert(0, str(EVAL_DIR))

from metrics import (
    CATEGORIES,
    accuracy,
    confusion_matrix,
    confusion_matrix_md,
    cost_summary,
    field_accuracy,
    latency_stats,
    per_class_scores,
    schema_stats,
)

DEFAULT_API = "http://localhost:8000"


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def http_json(url: str, payload: dict | None = None, headers: dict | None = None,
              method: str = "POST", timeout: float = 90.0) -> tuple[int, dict | str]:
    data = None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def main() -> int:
    ap = argparse.ArgumentParser(description="TriageGate eval runner")
    ap.add_argument("--mode", choices=["api", "n8n"], default="api")
    ap.add_argument("--api-url", default=DEFAULT_API)
    ap.add_argument("--n8n-url", default="", help="full n8n webhook URL for ingest")
    ap.add_argument("--dataset", default=str(EVAL_DIR / "dataset" / "emails.jsonl"))
    ap.add_argument("--run-tag", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--slack-url", default="http://localhost:5678/webhook/slack-actions",
                    help="n8n Slack-interactivity webhook that receives signed synthetic clicks")
    ap.add_argument("--simulate-clicks", action="store_true",
                    help="drive approve / double-approve / reject / expired paths")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="posts per batch; kept at 1 to respect rate limits")
    args = ap.parse_args()

    env = {**load_env_file(PROJECT_ROOT / ".env"), **os.environ}
    token = env.get("TRIAGE_API_TOKEN", "")
    signing_secret = env.get("SLACK_SIGNING_SECRET", "")
    price_in = float(env.get("LLM_PRICE_IN_PER_MTOK", "0.10"))
    price_out = float(env.get("LLM_PRICE_OUT_PER_MTOK", "0.50"))
    api_base = args.api_url.rstrip("/")

    if args.mode == "n8n":
        if not args.n8n_url:
            print("ERROR: --mode n8n requires --n8n-url <ingest webhook>", file=sys.stderr)
            return 2
        ingest_url = args.n8n_url
    else:
        ingest_url = f"{api_base}/triage"


    rows = [json.loads(l) for l in Path(args.dataset).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        rows = rows[: args.limit]
    print(f"Replaying {len(rows)} emails via {args.mode} -> {ingest_url} (run_tag={args.run_tag})")

    headers = {"X-Internal-Token": token}
    ingest_headers = (
        {"X-Ingest-Token": env.get("N8N_INGEST_TOKEN", "")} if args.mode == "n8n" else headers
    )
    results: list[dict] = []
    t_start = time.time()

    for i, row in enumerate(rows, 1):
        body = {
            "message_id": f"{row['id']}#{args.run_tag}",
            "source": "eval",
            "from_email": row["from_email"],
            "from_name": row.get("from_name", ""),
            "subject": row["subject"],
            "body_text": row["body_text"],
            "received_at": datetime.now(UTC).isoformat(),
        }
        if args.mode == "n8n":
            body["run_tag"] = args.run_tag
        t0 = time.time()
        status, resp = http_json(ingest_url, body, headers=ingest_headers, timeout=120)
        e2e_ms = int((time.time() - t0) * 1000)
        if status != 200 or not isinstance(resp, dict):
            print(f"  [{i}/{len(rows)}] {row['id']} FAILED http={status}: {resp}", file=sys.stderr)
            results.append({"id": row["id"], "label": row["label"], "pred": None,
                            "error": str(resp)[:300], "e2e_ms": e2e_ms})
            continue

        pred = resp.get("triage")
        results.append({
            "id": row["id"],
            "label": row["label"],
            "pred": pred,
            "status": resp.get("status"),
            "route": resp.get("route"),
            "attempts": resp.get("attempts", 1),
            "approval_id": resp.get("approval_id"),
            "run_id": resp.get("run_id"),
            "e2e_ms": e2e_ms,
            "latency_ms": None,
        })
        ok = (pred or {}).get("category") == row["label"]["category"]
        print(f"  [{i}/{len(rows)}] {row['id']} {row['label']['category']:>15} "
              f"-> {(pred or {}).get('category', '?'):>15} "
              f"{'OK' if ok else 'MISS'} (attempts={resp.get('attempts')}, {e2e_ms}ms)")

    # ---- metrics -------------------------------------------------------------
    # every email counts; a quarantined (needs_review) email is a miss, never silently dropped
    scored = list(results)
    y_true = [r["label"]["category"] for r in scored]
    y_pred = [(r.get("pred") or {}).get("category", "needs_review") for r in scored]
    cm = confusion_matrix(y_true, y_pred, labels=CATEGORIES)
    cls_scores = per_class_scores(cm)
    # extraction is only meaningful (and only written to the CRM) for lead / support emails
    fa = field_accuracy([{"id": r["id"], "label": r["label"], "pred": r.get("pred")} for r in scored
                         if r["label"]["category"] in ("sales_lead", "support_request")])

    schema = schema_stats([
        {"attempts": r.get("attempts") or 1,
         "status": "needs_review" if r.get("pred") is None else "ok"}
        for r in scored
    ])

    # token/cost from the run log
    token_rows: list[dict] = []
    latencies: list[float] = []
    runs_export: list[dict] = []
    if token:
        st, runs_resp = http_json(
            f"{api_base}/runs?limit=500&run_tag={args.run_tag}",
            payload=None, headers=headers, method="GET",
        )
        if st == 200 and isinstance(runs_resp, list):
            for run in runs_resp:
                runs_export.append(run)
                latencies.append(float(run.get("latency_ms") or 0))
                token_rows.append({
                    "input_tokens": run.get("input_tokens") or 0,
                    "output_tokens": run.get("output_tokens") or 0,
                    "cache_read_tokens": run.get("cache_read_tokens") or 0,
                })
    cost = cost_summary(token_rows or [{"input_tokens": 0, "output_tokens": 0}], price_in, price_out)
    if not token_rows:
        cost["emails"] = 0
        cost["usd_total"] = 0.0
        cost["usd_per_email"] = 0.0

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_tag": args.run_tag,
        "mode": args.mode,
        "dataset": str(Path(args.dataset).relative_to(PROJECT_ROOT)),
        "n_emails": len(rows),
        "n_scored": len(scored),
        "n_errors": sum(1 for r in results if r.get("error")),
        "classification": {
            "accuracy": round(accuracy(y_true, y_pred), 4),
            "per_class": cls_scores,
            "confusion_matrix": cm,
            "predicted_distribution": dict(Counter(y_pred)),
            "label_distribution": dict(Counter(y_true)),
        },
        "extraction": fa,
        "schema": schema,
        "latency_ms": latency_stats(latencies) if latencies else latency_stats([r["e2e_ms"] for r in results]),
        "e2e_ms": latency_stats([float(r["e2e_ms"]) for r in results]),
        "cost": cost,
        "reliability": {"note": "populated by --simulate-clicks / forced-fault run"},
    }

    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    clicks = {}
    if args.simulate_clicks:
        clicks = simulate_clicks(results, args.slack_url, api_base, token, signing_secret)
        summary["reliability"] = clicks

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "confusion_matrix.md").write_text(confusion_matrix_md(cm), encoding="utf-8")
    (out_dir / "runs_export.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in runs_export) + "\n",
        encoding="utf-8",
    )

    elapsed = time.time() - t_start
    print()
    print(f"Accuracy          : {summary['classification']['accuracy']}")
    print(f"First-attempt OK  : {schema['first_attempt_valid_rate']} "
          f"({schema['first_attempt_valid']}/{schema['total']})")
    print(f"Needs review      : {schema['needs_review']}")
    print(f"Cost              : ${cost['usd_total']:.6f} total, ${cost['usd_per_email']:.6f}/email")
    print(f"Wall time         : {elapsed:.1f}s")
    print(f"Results -> {out_dir / 'summary.json'}")
    return 0


def hubspot_state() -> dict:
    """Record counts from the CRM actually in use (mock if HUBSPOT_API_BASE is set)."""
    base = os.environ.get("HUBSPOT_API_BASE") or load_env_file(PROJECT_ROOT / ".env").get("HUBSPOT_API_BASE")
    if base and "hubspot-mock" in base:
        st, resp = http_json("http://localhost:8010/_mock/state", method="GET")
        if st == 200 and isinstance(resp, dict):
            return {"target": "mock", **resp}
    return {"target": "real", "note": "count real records via scripts/count_hubspot_test_records.py"}


def wait_for(fn, timeout=40.0, every=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def simulate_clicks(results: list[dict], slack_url: str, api_base: str, token: str,
                    signing_secret: str) -> dict:
    """Signed synthetic Slack clicks -> n8n WF2 (full path incl. HubSpot).

    N: 4 single approvals, 5 double-approve trials (the last is two simultaneous clicks),
    3 rejections, 1 bad-signature click. Nothing is clicked in real Slack.
    """
    import threading

    import httpx

    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from simulate_click import signed_request

    hdr = {"X-Internal-Token": token}

    def post_click(approval_id, action="approve", secret=None, ts=None):
        raw, headers = signed_request(approval_id, action, secret or signing_secret, "eval-runner", ts)
        return httpx.post(slack_url, content=raw, headers=headers, timeout=30).status_code

    def approval(aid):
        _st, rows = http_json(f"{api_base}/approvals", headers=hdr, method="GET")
        return next((a for a in rows if a["approval_id"] == aid), None)

    def settled(aid):
        a = approval(aid)
        return a if a and a["status"] in ("crm_written", "crm_failed", "rejected") else None

    def writes():
        return http_json(f"{api_base}/crm-writes", headers=hdr, method="GET")[1]

    def events():
        return http_json(f"{api_base}/events?limit=2000", headers=hdr, method="GET")[1]

    pool = [r for r in results if r.get("approval_id")]
    leads = [r for r in pool if (r["pred"] or {}).get("category") == "sales_lead"]
    sups = [r for r in pool if (r["pred"] or {}).get("category") == "support_request"]
    single = leads[:2] + sups[:2]
    double = leads[2:5] + sups[2:4]
    rejects = leads[5:6] + sups[4:6]
    out: dict = {"approvals_created": len(pool), "clicks_via": "signed synthetic Slack payloads -> n8n webhook"}

    for r in single:
        post_click(r["approval_id"])
    for r in double[:4]:
        post_click(r["approval_id"])
        post_click(r["approval_id"])          # the double click
    t = [threading.Thread(target=post_click, args=(double[4]["approval_id"],)) for _ in range(2)]
    [x.start() for x in t]
    [x.join() for x in t]                     # the concurrent double click
    for r in rejects:
        post_click(r["approval_id"], "reject")

    for r in single + double + rejects:
        wait_for(lambda a=r["approval_id"]: settled(a))
    time.sleep(3)

    wr = writes()
    per_approval = Counter(w["approval_id"] for w in wr)
    ev = events()

    def clicks_of(aid):
        return (approval(aid) or {}).get("click_count")

    out["single_approve"] = {
        "n": len(single),
        "crm_written": sum(1 for r in single if (approval(r["approval_id"]) or {}).get("status") == "crm_written"),
        "crm_write_rows_each": [per_approval.get(r["approval_id"], 0) for r in single],
    }
    out["double_approve"] = {
        "n": len(double),
        "concurrent_trials": 1,
        "clicks_registered_each": [clicks_of(r["approval_id"]) for r in double],
        "crm_write_rows_each": [per_approval.get(r["approval_id"], 0) for r in double],
        "duplicate_crm_writes": sum(max(per_approval.get(r["approval_id"], 0) - 1, 0) for r in double),
        "duplicate_clicks_ignored": sum(1 for e in ev if e["event"] == "click_duplicate"),
    }
    out["reject"] = {
        "n": len(rejects),
        "status_rejected": sum(1 for r in rejects if (approval(r["approval_id"]) or {}).get("status") == "rejected"),
        "crm_writes": sum(per_approval.get(r["approval_id"], 0) for r in rejects),
    }

    # bad signature on a still-pending approval must change nothing
    spare = next((r for r in pool if (approval(r["approval_id"]) or {}).get("status") == "pending"), None)
    sig_events_before = sum(1 for e in ev if e["event"] == "invalid_signature")
    if spare:
        post_click(spare["approval_id"], secret="wrong-secret")
        time.sleep(3)
        sig_after = sum(1 for e in events() if e["event"] == "invalid_signature")
        out["bad_signature"] = {
            "rejected_events_logged": sig_after - sig_events_before,
            "approval_still_pending": (approval(spare["approval_id"]) or {}).get("status") == "pending",
        }
    out["crm_write_rows_total"] = len(wr)
    out["hubspot"] = hubspot_state()
    out["approved_approvals"] = sorted({w["approval_id"] for w in wr})
    return out


if __name__ == "__main__":
    raise SystemExit(main())
