"""Unit tests for eval/metrics.py on a toy confusion matrix."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metrics import (
    accuracy,
    confusion_matrix,
    confusion_matrix_md,
    cost_summary,
    field_accuracy,
    latency_stats,
    normalize_text,
    per_class_scores,
    schema_stats,
)


def test_toy_confusion_matrix():
    y_true = ["a", "a", "b", "b", "c", "d"]
    y_pred = ["a", "b", "b", "b", "c", "a"]
    cm = confusion_matrix(y_true, y_pred, labels=["a", "b", "c", "d"])
    assert cm["a"]["a"] == 1 and cm["a"]["b"] == 1
    assert cm["b"]["b"] == 2
    assert cm["c"]["c"] == 1
    assert cm["d"]["a"] == 1
    assert accuracy(y_true, y_pred) == 4 / 6


def test_per_class_precision_recall_f1():
    y_true = ["spam", "spam", "spam", "lead", "lead"]
    y_pred = ["spam", "spam", "lead", "lead", "lead"]
    cm = confusion_matrix(y_true, y_pred, labels=["spam", "lead"])
    s = per_class_scores(cm)
    # spam: tp=2 fp=0 fn=1 -> p=1.0 r=2/3
    assert s["spam"]["precision"] == 1.0
    assert abs(s["spam"]["recall"] - 2 / 3) < 1e-3
    assert abs(s["spam"]["f1"] - (2 * 1.0 * (2 / 3)) / (1.0 + 2 / 3)) < 1e-6
    assert s["lead"]["support"] == 2


def test_zero_division_scores():
    cm = confusion_matrix(["a"], ["b"], labels=["a", "b"])
    s = per_class_scores(cm)
    assert s["a"]["precision"] == 0.0 and s["a"]["recall"] == 0.0 and s["a"]["f1"] == 0.0


def test_normalize_text_company():
    assert normalize_text("Acme Widgets") == normalize_text("acme  widgets!")
    assert normalize_text(None) == ""
    assert normalize_text("Kitewell K.K.") == "kitewellkk"


def test_field_accuracy_null_company_match():
    rows = [
        {"id": "1", "label": {"company": None, "contact_email": None, "urgency": "low"},
         "pred": {"company": None, "contact_email": None, "urgency": "low"}},
        {"id": "2", "label": {"company": "Acme", "contact_email": "a@b.c", "urgency": "high"},
         "pred": {"company": "acme!", "contact_email": "a@b.c", "urgency": "medium"}},
    ]
    fa = field_accuracy(rows)
    assert fa["company"]["accuracy"] == 1.0  # null==null and case/punct-insensitive match
    assert fa["contact_email"]["accuracy"] == 1.0
    assert fa["urgency"]["accuracy"] == 0.5
    assert fa["urgency"]["misses"][0]["got"] == "medium"


def test_field_accuracy_with_none_prediction():
    rows = [
        {"id": "1", "label": {"company": "Acme", "contact_email": "a@b.c", "urgency": "high"},
         "pred": None},
    ]
    fa = field_accuracy(rows)
    assert fa["company"]["correct"] == 0
    assert fa["contact_email"]["correct"] == 0


def test_schema_stats():
    rows = [
        {"attempts": 1, "status": "ok"},
        {"attempts": 1, "status": "ok"},
        {"attempts": 2, "status": "ok"},
        {"attempts": 3, "status": "needs_review"},
    ]
    s = schema_stats(rows)
    assert s["first_attempt_valid"] == 2
    assert s["first_attempt_valid_rate"] == 0.5
    assert s["runs_with_retries"] == 2
    assert s["needs_review"] == 1


def test_confusion_matrix_md_renders_table():
    cm = confusion_matrix(["a", "b"], ["a", "b"], labels=["a", "b"])
    md = confusion_matrix_md(cm, ["a", "b"])
    assert "| **a** | 1 | 0 |" in md
    assert md.startswith("# Confusion matrix")


def test_latency_and_cost():
    assert latency_stats([])["p50"] is None
    ls = latency_stats([10, 20, 30, 40, 50])
    assert ls["p50"] == 30 and ls["n"] == 5
    cost = cost_summary(
        [{"input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 500}] * 10,
        price_in=0.10, price_out=0.50,
    )
    assert cost["emails"] == 10
    assert cost["usd_total"] > 0
    # input_tokens are uncached: 1000@0.10 + 500 cached@0.01 + 200 out@0.50 = 205/1e6 per email, x10
    assert abs(cost["usd_total"] - 0.00205) < 1e-9
