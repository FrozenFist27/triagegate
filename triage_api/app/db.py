"""SQLite connection + migrations. SQLite serializes writers, which is what makes
the approval transition atomic (single UPDATE ... WHERE status='pending')."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  message_id TEXT UNIQUE NOT NULL,
  source TEXT NOT NULL,
  run_tag TEXT,
  received_at TEXT,
  created_at TEXT NOT NULL,
  input_json TEXT NOT NULL,
  attempts_json TEXT NOT NULL,
  attempt_count INTEGER NOT NULL,
  triage_json TEXT,
  status TEXT NOT NULL,
  route TEXT NOT NULL,
  decision TEXT,
  result TEXT,
  model TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  latency_ms INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  status TEXT NOT NULL,
  slack_channel TEXT,
  slack_ts TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT,
  click_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS crm_writes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  approval_id TEXT UNIQUE NOT NULL REFERENCES approvals(approval_id),
  status TEXT NOT NULL,
  hubspot_contact_id TEXT,
  hubspot_company_id TEXT,
  hubspot_ticket_id TEXT,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  run_id TEXT,
  approval_id TEXT,
  event TEXT NOT NULL,
  detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
"""

_db_path: Path | None = None


def init_db(path: Path | str) -> Path:
    """Create tables and remember the active path for get_conn()."""
    global _db_path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    try:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
    finally:
        conn.close()
    _db_path = p
    return p


def set_db_path(path: Path | str) -> None:
    global _db_path
    _db_path = Path(path)


def get_db_path() -> Path:
    if _db_path is None:
        from .config import resolve_db_path

        return resolve_db_path()
    return _db_path


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Explicit BEGIN IMMEDIATE so concurrent approval clicks serialize properly."""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def log_event(
    event: str,
    run_id: str | None = None,
    approval_id: str | None = None,
    detail: dict | None = None,
) -> None:
    import json
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO events (ts, run_id, approval_id, event, detail_json) VALUES (?,?,?,?,?)",
            (ts, run_id, approval_id, event, json.dumps(detail or {}, ensure_ascii=False)),
        )
    finally:
        conn.close()


def ensure_database() -> None:
    """Idempotent bootstrap called at app start."""
    init_db(get_db_path())


if __name__ == "__main__":  # pragma: no cover
    init_db(Path(os.environ.get("DB_PATH", "data/triagegate.db")))
    print("database migrated")
