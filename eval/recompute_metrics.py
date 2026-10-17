#!/usr/bin/env python
"""Recompute eval/results/summary.json metrics from the saved run export (no LLM calls, no n8n).

Keeps the `reliability` block and end-to-end timings of the existing summary.
  python eval/recompute_metrics.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from metrics import (  # noqa: E402
    CATEGORIES, accuracy, confusion_matrix, confusion_matrix_md, cost_summary, field_accuracy,
    latency_stats, per_class_scores, schema_stats,
)

res = HERE / "results"
summary = json.loads((res / "summary.json").read_text(encoding="utf-8"))
runs = [json.loads(line) for line in (res / "runs_export.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
labels = {json.loads(line)["id"]: json.loads(line)["label"]
          for line in (HERE / "dataset/emails.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
by_id = {r["message_id"].split("#")[0]: r for r in runs}

y_true, y_pred, fa_rows, sch = [], [], [], []
for eid, lab in labels.items():
    run = by_id.get(eid)
    pred = json.loads(run["triage_json"]) if run and run.get("triage_json") else None
    y_true.append(lab["category"])
    y_pred.append(pred["category"] if pred else "needs_review")
    if lab["category"] in ("sales_lead", "support_request"):
        fa_rows.append({"id": eid, "label": lab, "pred": pred})
    sch.append({"attempts": run["attempt_count"] if run else 1, "status": run["status"] if run else "needs_review"})

cm = confusion_matrix(y_true, y_pred, labels=CATEGORIES)
price_in, price_out = summary["cost"]["price_in_per_mtok"], summary["cost"]["price_out_per_mtok"]
summary["classification"].update({"accuracy": round(accuracy(y_true, y_pred), 4), "per_class": per_class_scores(cm),
                                  "confusion_matrix": cm})
summary["extraction"] = field_accuracy(fa_rows)
summary["extraction_scope"] = "sales_lead + support_request emails only (the ones that can reach the CRM)"
summary["schema"] = schema_stats(sch)
summary["latency_ms"] = latency_stats([float(r.get("latency_ms") or 0) for r in runs])
summary["cost"] = cost_summary([{"input_tokens": r.get("input_tokens") or 0, "output_tokens": r.get("output_tokens") or 0,
                                 "cache_read_tokens": r.get("cache_read_tokens") or 0} for r in runs], price_in, price_out)
(res / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
(res / "confusion_matrix.md").write_text(confusion_matrix_md(cm), encoding="utf-8")
print("accuracy", summary["classification"]["accuracy"], "cost/email", summary["cost"]["usd_per_email"])
