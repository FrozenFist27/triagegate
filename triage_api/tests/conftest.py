"""Shared fixtures: temp SQLite db, FastAPI TestClient, fake LLM."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

TEST_SIGNING_SECRET = "test_signing_secret"
TEST_TOKEN = "test-internal-token"


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(p))
    monkeypatch.setenv("TRIAGE_API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SIGNING_SECRET)
    monkeypatch.setenv("APPROVAL_TTL_MINUTES", "1440")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_FAULT_INJECTION", "off")

    from app.config import get_settings

    get_settings.cache_clear()

    from app import db as dbmod

    dbmod.init_db(p)
    yield p
    get_settings.cache_clear()


@pytest.fixture()
def client(db_path):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth():
    return {"X-Internal-Token": TEST_TOKEN}


@pytest.fixture()
def sample_email():
    from app.schemas import InboundEmail

    def _make(**kwargs):
        base = dict(
            message_id="msg-001",
            source="webhook",
            from_email="jane@acme-widgets.example.com",
            from_name="Jane Doe",
            subject="Demo request",
            body_text="Hi, we would like a demo next week.",
            received_at="2026-10-17T10:00:00+00:00",
        )
        base.update(kwargs)
        return InboundEmail(**base)

    return _make
