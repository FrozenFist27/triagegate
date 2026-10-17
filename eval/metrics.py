"""Evaluation metrics: accuracy, per-class P/R/F1, confusion matrix, field accuracy."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

CATEGORIES = ["sales_lead", "support_request", "invoice", "spam"]
URGENCIES = ["low", "medium", "high"]


def normalize_text(value: str | None) -> str:
    """Case/punctuation-insensitive comparison key for company names."""
    if value is None:
        return ""
    v = unicodedata.normalize("NFKD", str(value)).lower().strip()
    v = re.sub(r"[^a-z0-9]+", "", v)
    return v


def confusion_matrix(y_true: list[str], y_pred: list[str],
                     labels: Iterable[str] = CATEGORIES) -> dict[str, dict[str, int]]:
    labels = list(labels)
    cm = {t: {p: 0 for p in labels} for t in labels}
    for t, p in zip(y_true, y_pred):
        if t in cm and p in cm[t]:
            cm[t][p] += 1
    return cm


def per_class_scores(cm: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for cls, row in cm.items():
        tp = row[cls]
        fn = sum(row.values()) - tp
        fp = sum(cm[t][cls] for t in cm) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[cls] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }
    return out


def accuracy(y_true: list[str], y_pred: list[str]) -> float:
    if not y_true:
        return 0.0
    return sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true)


def field_accuracy(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """rows: [{label: {...}, pred: {...} | None}]"""
    company = {"correct": 0, "total": 0, "misses": []}
    email = {"correct": 0, "total": 0, "misses": []}
    urgency = {"correct": 0, "total": 0, "misses": []}

    for row in rows:
        label = row["label"]
        pred = row.get("pred")

        # company: both-null counts as correct
        company["total"] += 1
        lc = label.get("company")
        pc = (pred or {}).get("company")
        if normalize_text(lc) == normalize_text(pc):
            company["correct"] += 1
        else:
            company["misses"].append({"id": row.get("id"), "expected": lc, "got": pc})

        if label.get("contact_email") is not None or (pred or {}).get("contact_email"):
            email["total"] += 1
            le = (label.get("contact_email") or "").lower()
            pe = ((pred or {}).get("contact_email") or "").lower()
            if le == pe:
                email["correct"] += 1
            else:
                email["misses"].append({"id": row.get("id"), "expected": le or None, "got": pe or None})

        urgency["total"] += 1
        if label.get("urgency") == (pred or {}).get("urgency"):
            urgency["correct"] += 1
        else:
            urgency["misses"].append(
                {"id": row.get("id"), "expected": label.get("urgency"),
                 "got": (pred or {}).get("urgency")}
            )

    def pack(d: dict[str, Any]) -> dict[str, Any]:
        return {
            "accuracy": round(d["correct"] / d["total"], 4) if d["total"] else None,
            "correct": d["correct"],
            "total": d["total"],
            "misses": d["misses"][:10],
        }

    return {"company": pack(company), "contact_email": pack(email), "urgency": pack(urgency)}


def schema_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows need: attempts (int), status ('ok'|'needs_review'), triage (dict|None)"""
    total = len(rows)
    first_valid = sum(1 for r in rows if r.get("attempts") == 1 and r.get("status") == "ok")
    retries = sum(1 for r in rows if (r.get("attempts") or 1) > 1)
    needs_review = sum(1 for r in rows if r.get("status") == "needs_review")
    return {
        "total": total,
        "first_attempt_valid": first_valid,
        "first_attempt_valid_rate": round(first_valid / total, 4) if total else None,
        "runs_with_retries": retries,
        "needs_review": needs_review,
    }


def confusion_matrix_md(cm: dict[str, dict[str, int]], labels: list[str] | None = None) -> str:
    labels = labels or list(cm.keys())
    header = "| actual \\ predicted | " + " | ".join(labels) + " |"
    sep = "|" + "---|" * (len(labels) + 1)
    lines = ["# Confusion matrix", "", header, sep]
    for t in labels:
        cells = " | ".join(str(cm[t][p]) for p in labels)
        lines.append(f"| **{t}** | {cells} |")
    return "\n".join(lines) + "\n"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    frac = k - lo
    return round(vals[lo] * (1 - frac) + vals[hi] * frac, 2)


def latency_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"p50": None, "p95": None, "mean": None, "n": 0}
    return {
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "mean": round(sum(values) / len(values), 2),
        "n": len(values),
    }


def cost_summary(token_rows: list[dict[str, Any]], price_in: float, price_out: float) -> dict[str, Any]:
    """token_rows: {input_tokens (uncached), output_tokens, cache_read_tokens}"""
    total_in = sum(r.get("input_tokens", 0) for r in token_rows)
    total_out = sum(r.get("output_tokens", 0) for r in token_rows)
    total_cached = sum(r.get("cache_read_tokens", 0) for r in token_rows)
    n = len(token_rows) or 1
    # Anthropic reports input_tokens EXCLUDING cache reads; cache reads bill at 0.1x input.
    # (Cache writes, ~1.25x on a ~3K-token prompt once per 5 min, are negligible and not tracked.)
    usd = (total_in * price_in + total_cached * price_in * 0.10 + total_out * price_out) / 1_000_000
    return {
        "emails": len(token_rows),
        "input_tokens_total": total_in,
        "output_tokens_total": total_out,
        "cache_read_tokens_total": total_cached,
        "input_tokens_per_email": round(total_in / n, 1),
        "cache_read_tokens_per_email": round(total_cached / n, 1),
        "output_tokens_per_email": round(total_out / n, 1),
        "usd_total": round(usd, 6),
        "usd_per_email": round(usd / n, 6),
        "price_in_per_mtok": price_in,
        "price_out_per_mtok": price_out,
    }
