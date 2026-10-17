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
import hashlib
import hmac
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


def sign_slack(raw_body: str, secret: str, ts: str) -> str:
    return "v0=" + hmac.new(
        secret.encode("utf-8"), f"v0:{ts}:{raw_body}".encode(), hashlib.sha256
    ).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="TriageGate eval runner")
    ap.add_argument("--mode", choices=["api", "n8n"], default="api")
    ap.add_argument("--api-url", default=DEFAULT_API)
    ap.add_argument("--n8n-url", default="", help="full n8n webhook URL for ingest")
    ap.add_argument("--dataset", default=str(EVAL_DIR / "dataset" / "emails.jsonl"))
    ap.add_argument("--run-tag", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--decide-url", default="", help="where signed Slack payloads are posted")
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

    decide_url = (args.decide_url or f"{api_base}/approvals/decide").rstrip("/")

    rows = [json.loads(l) for l in Path(args.dataset).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        rows = rows[: args.limit]
    print(f"Replaying {len(rows)} emails via {args.mode} -> {ingest_url} (run_tag={args.run_tag})")

    headers = {"X-Internal-Token": token}
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
        t0 = time.time()
        status, resp = http_json(ingest_url, body, headers=headers, timeout=120)
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
    scored = [r for r in results if r.get("pred") is not None or r.get("error")]
    y_true = [r["label"]["category"] for r in scored]
    y_pred = [(r.get("pred") or {}).get("category", "needs_review") for r in scored]
    cm = confusion_matrix(y_true, y_pred, labels=CATEGORIES)
    cls_scores = per_class_scores(cm)
    fa = field_accuracy([{"id": r["id"], "label": r["label"], "pred": r.get("pred")} for r in scored])

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
        clicks = simulate_clicks(results, decide_url, token, signing_secret)
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


def simulate_clicks(results: list[dict], decide_url: str, token: str, signing_secret: str) -> dict:
    """Drive approve / double-approve / reject / expired through the decide endpoint
    with correctly signed synthetic Slack payloads (no real Slack click needed)."""
    headers = {"X-Internal-Token": token}
    approvals = [r for r in results if r.get("approval_id")]
    out = {
        "approvals_created": len(approvals),
        "approved": 0,
        "rejected": 0,
        "duplicates": 0,
        "expired_clicks": 0,
        "invalid_signatures": 0,
        "crm_writes": 0,
        "double_approve_trials": 0,
        "double_approve_single_write": 0,
    }
    if not approvals:
        return out

    def click(approval_id: str, action: str = "triage_approve", *, secret=None, ts=None):
        payload = {
            "actions": [{"action_id": action, "value": approval_id}],
            "user": {"id": "U_EVAL", "username": "eval-runner"},
        }
        raw = "payload=" + json.dumps(payload)
        stamp = ts or str(int(time.time()))
        sig = sign_slack(raw, secret or signing_secret, stamp)
        st, resp = http_json(
            decide_url,
            {"raw_body": raw, "signature": sig, "timestamp": stamp},
            headers=headers,
        )
        return (resp or {}).get("outcome") if isinstance(resp, dict) else f"http_{st}"

    # 1) single approve on the first half
    for r in approvals[: len(approvals) // 2]:
        outcome = click(r["approval_id"])
        if outcome == "write_crm":
            out["approved"] += 1
            out["crm_writes"] += 1
        elif outcome == "duplicate":
            out["duplicates"] += 1

    # 2) double-approve trials: click the SAME payload twice
    for r in approvals[len(approvals) // 2: len(approvals) // 2 + 5]:
        out["double_approve_trials"] += 1
        o1 = click(r["approval_id"])
        o2 = click(r["approval_id"])
        if o1 == "write_crm" and o2 == "duplicate":
            out["double_approve_single_write"] += 1
        out["approved"] += 1 if o1 == "write_crm" else 0
        out["duplicates"] += 1 if o2 == "duplicate" else 0
        out["crm_writes"] += 1 if o1 == "write_crm" else 0

    # 3) reject the remainder
    for r in approvals[len(approvals) // 2 + 5:]:
        outcome = click(r["approval_id"], action="triage_reject")
        if outcome == "rejected":
            out["rejected"] += 1
        elif outcome == "duplicate":
            out["duplicates"] += 1

    # 4) a bad-signature click must be refused
    if approvals:
        o = click(approvals[0]["approval_id"], secret="wrong-secret")
        if o == "invalid_signature":
            out["invalid_signatures"] += 1

    # 5) click on an expired approval
    expired = expire_one(approvals, decide_url, token, signing_secret)
    out["expired_clicks"] = 1 if expired else 0
    out["expired_approvals_marked"] = expired
    return out


def expire_one(approvals: list[dict], decide_url: str, token: str, signing_secret: str) -> bool:
    """Force one approval past its TTL, expire it, then click it."""
    api_base = decide_url.rsplit("/approvals", 1)[0]
    headers = {"X-Internal-Token": token}
    target = approvals[-1]["approval_id"]

    # move the expiry into the past using a tiny inline sqlite update when the db is local
    db_path = PROJECT_ROOT / "data" / "triagegate.db"
    if db_path.exists():
        import sqlite3

        conn = sqlite3.connect(db_path, timeout=15)
        try:
            conn.execute(
                "UPDATE approvals SET expires_at='2000-01-01T00:00:00+00:00' WHERE approval_id=?",
                (target,),
            )
            conn.commit()
        finally:
            conn.close()
    else:
        return False

    st, resp = http_json(f"{api_base}/approvals/expire", {}, headers=headers)
    if st != 200:
        return False

    payload = {"actions": [{"action_id": "triage_approve", "value": target}],
               "user": {"id": "U_EVAL", "username": "eval-runner"}}
    raw = "payload=" + json.dumps(payload)
    stamp = str(int(time.time()))
    sig = sign_slack(raw, signing_secret, stamp)
    st, resp = http_json(decide_url, {"raw_body": raw, "signature": sig, "timestamp": stamp},
                         headers=headers)
    return isinstance(resp, dict) and resp.get("outcome") == "expired"


if __name__ == "__main__":
    raise SystemExit(main())
