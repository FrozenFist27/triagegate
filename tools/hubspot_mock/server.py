"""Minimal in-memory stand-in for the HubSpot CRM v3 endpoints TriageGate uses.

NOT HubSpot. It exists so the n8n write path (search -> upsert contact -> company/ticket
with associations) and the idempotency guarantees can be exercised while the real service
key lacks write scopes. Mirrors the real API shapes: 409 'Existing ID: n' on duplicate email.
"""
from __future__ import annotations

import itertools
import threading

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="HubSpot mock (TriageGate demo)")
_lock = threading.Lock()
_ids = itertools.count(1000)
DB: dict[str, dict[str, dict]] = {"contacts": {}, "companies": {}, "tickets": {}}
LOG: list[dict] = []


def _auth(request: Request) -> None:
    if not request.headers.get("authorization", "").startswith("Bearer "):
        raise HTTPException(401, "missing bearer")


@app.post("/crm/v3/objects/contacts/search")
async def search(request: Request):
    _auth(request)
    body = await request.json()
    flt = body["filterGroups"][0]["filters"][0]
    with _lock:
        res = [{"id": i, "properties": r["properties"]} for i, r in DB["contacts"].items()
               if r["properties"].get(flt["propertyName"]) == flt["value"]]
    return {"total": len(res), "results": res[: body.get("limit", 10)]}


@app.post("/crm/v3/objects/{obj}")
async def create(obj: str, request: Request):
    _auth(request)
    if obj not in DB:
        raise HTTPException(404)
    body = await request.json()
    props = body.get("properties", {})
    with _lock:
        if obj == "contacts":
            for i, r in DB["contacts"].items():
                if r["properties"].get("email") == props.get("email"):
                    raise HTTPException(409, f"Contact already exists. Existing ID: {i}")
        rid = str(next(_ids))
        DB[obj][rid] = {"properties": props, "associations": body.get("associations", [])}
        LOG.append({"op": "create", "obj": obj, "id": rid})
    return {"id": rid, "properties": props}


@app.patch("/crm/v3/objects/contacts/{rid}")
async def patch(rid: str, request: Request):
    _auth(request)
    body = await request.json()
    with _lock:
        if rid not in DB["contacts"]:
            raise HTTPException(404)
        DB["contacts"][rid]["properties"].update(body.get("properties", {}))
        LOG.append({"op": "update", "obj": "contacts", "id": rid})
    return {"id": rid}


@app.get("/_mock/state")
def state():
    return {"counts": {k: len(v) for k, v in DB.items()}, "log": LOG, "records": DB}
