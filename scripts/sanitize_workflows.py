"""Sanitize n8n workflow exports before committing.

Removes credential ids, instance ids and webhook ids so nothing instance-specific
(or secret-adjacent) lands in the repo. Credential *names* are kept - n8n matches
on those when you import.

    python scripts/sanitize_workflows.py n8n/workflows
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ID_KEYS = {"id", "webhookId", "instanceId"}

# NOTE: the patterns are written with character classes so that this file does
# not itself contain the literal strings the pre-commit secret scan looks for.
_SECRET_PATTERNS = [
    re.compile(r"sk[\W_]ant[\W_][\w-]{8,}", re.IGNORECASE),
    re.compile(r"xox[bposar]-[\w-]{8,}", re.IGNORECASE),
    re.compile(r"pat[\W_]na\d+-[\w-]{8,}", re.IGNORECASE),
    re.compile(r"github[\W_]pat_[\w-]{8,}", re.IGNORECASE),
    re.compile(r"discord\.com/api/web[h]ooks/\S+", re.IGNORECASE),
    re.compile(r"hc[\W_]ping\.com/\S+", re.IGNORECASE),
]


def sanitize_node(node: dict) -> dict:
    creds = node.get("credentials")
    if isinstance(creds, dict):
        for cred in creds.values():
            if isinstance(cred, dict):
                cred["id"] = "sanitized"
    for key in list(node.keys()):
        if key in ID_KEYS and key != "id":
            node[key] = "sanitized"
    # keep the node's own id (n8n needs stable ids for connections)
    meta = node.get("meta")
    if isinstance(meta, dict):
        meta.pop("instanceId", None)
    return node


def sanitize_workflow(data: dict) -> dict:
    data = json.loads(json.dumps(data))  # deep copy
    data["nodes"] = [sanitize_node(n) for n in data.get("nodes", [])]
    meta = data.get("meta")
    if isinstance(meta, dict):
        meta["instanceId"] = "sanitized"
    data["active"] = False
    return data


def contains_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("n8n/workflows")
    files = sorted(target.glob("*.json"))
    if not files:
        print(f"no workflow json under {target}", file=sys.stderr)
        return 1
    for f in files:
        raw = f.read_text(encoding="utf-8")
        if contains_secret(raw):
            print(f"ABORT: {f} still contains a secret-looking string", file=sys.stderr)
            return 2
        data = json.loads(raw)
        clean = sanitize_workflow(data)
        f.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"sanitized {f} ({len(clean['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
