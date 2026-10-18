"""Add SLACK_APP_TOKEN (copied from the private secrets.env) and a random INTERNAL_FORWARD_SECRET
to the project .env. Idempotent; values are never printed. Run: python scripts/add_socket_env.py
"""
from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = Path(r"C:/Users/Sambudh/upwork-portfolio/secrets.env")
ENV = ROOT / ".env"


def read(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    cur = read(ENV)
    src = read(SECRETS)
    add: list[str] = []
    if not cur.get("SLACK_APP_TOKEN"):
        if not src.get("SLACK_APP_TOKEN"):
            raise SystemExit("SLACK_APP_TOKEN missing from secrets.env")
        add.append(f"SLACK_APP_TOKEN={src['SLACK_APP_TOKEN']}")
    if not cur.get("INTERNAL_FORWARD_SECRET"):
        add.append(f"INTERNAL_FORWARD_SECRET={secrets.token_urlsafe(32)}")
    if not cur.get("SLACK_MODE"):
        add.append("SLACK_MODE=socket")
    if add:
        text = ENV.read_text(encoding="utf-8")
        ENV.write_text(text.rstrip("\n") + "\n\n# --- Slack Socket Mode ---\n" + "\n".join(add) + "\n", encoding="utf-8")
    print("added keys:", [a.split("=")[0] for a in add] or "none (already present)")


if __name__ == "__main__":
    main()
