#!/usr/bin/env python
"""List HubSpot records carrying the "[TriageGate test]" marker (read-only; never prints the key).

  python scripts/count_hubspot_test_records.py
"""
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
KEY = dotenv_values(ROOT / ".env")["HUBSPOT_SERVICE_KEY"]
H = {"Authorization": f"Bearer {KEY}"}
SPEC = {"contacts": ("lastname", ["email", "lastname"]),
        "companies": ("name", ["name"]),
        "tickets": ("subject", ["subject"])}

for obj, (prop, props) in SPEC.items():
    r = httpx.post(f"https://api.hubapi.com/crm/v3/objects/{obj}/search", headers=H, timeout=30, json={
        "filterGroups": [{"filters": [{"propertyName": prop, "operator": "CONTAINS_TOKEN", "value": "TriageGate"}]}],
        "properties": props, "limit": 100})
    if r.status_code != 200:
        print(obj, "search failed", r.status_code)
        continue
    res = r.json()["results"]
    print(f"{obj}: {len(res)}")
    for x in res:
        print("  ", x["id"], {k: v for k, v in x["properties"].items() if k in props})
