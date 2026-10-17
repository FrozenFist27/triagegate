"""Generate the three TriageGate n8n workflows as importable JSON.

Design choices (so the workflows import and run with ZERO UI credential setup):
- Slack and HubSpot are called with HTTP Request nodes and Bearer tokens read from
  container env ($env.SLACK_BOT_TOKEN, $env.HUBSPOT_SERVICE_KEY). No secret is ever
  stored in the workflow JSON.
- The IMAP trigger is the only node that needs an n8n credential ("TriageGate IMAP");
  it is exported disabled and referenced by name only.

Run:  python scripts/build_n8n_workflows.py
Output: n8n/workflows/{wf1,wf2,wf3}_*.json
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "n8n" / "workflows"
OUT.mkdir(parents=True, exist_ok=True)
NS = uuid.UUID("0b0f7e57-3a8e-4f3a-9b61-5f1d7a7c0001")  # stable ids -> clean diffs

API = "http://triage-api:8000"
TOK = {"name": "X-Internal-Token", "value": "={{ $env.TRIAGE_API_TOKEN }}"}
SLACK_H = [
    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.SLACK_BOT_TOKEN }}"},
    {"name": "Content-Type", "value": "application/json; charset=utf-8"},
]
HS_H = [
    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.HUBSPOT_SERVICE_KEY }}"},
    {"name": "Content-Type", "value": "application/json"},
]


def hs(path, suffix_expr=""):
    """HubSpot URL expression; HUBSPOT_API_BASE lets the demo point at the local mock."""
    tail = f" + {suffix_expr}" if suffix_expr else ""
    return "={{ ($env.HUBSPOT_API_BASE || 'https://api.hubapi.com') + '" + path + "'" + tail + " }}"


def node(name, ntype, pos, params, tv=1, creds=None, disabled=False, on_error=None, extra=None):
    n = {
        "parameters": params,
        "id": str(uuid.uuid5(NS, name)),
        "name": name,
        "type": ntype,
        "typeVersion": tv,
        "position": pos,
    }
    if creds:
        n["credentials"] = creds
    if disabled:
        n["disabled"] = True
    if on_error:
        n["onError"] = on_error
    if extra:
        n.update(extra)
    return n


def http(name, pos, method, url, body=None, headers=None, timeout=30000, on_error=None, extra=None):
    p = {
        "method": method,
        "url": url,
        "sendHeaders": True,
        "headerParameters": {"parameters": headers or [TOK]},
        "options": {"timeout": timeout},
    }
    if body is not None:
        p.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
    return node(name, "n8n-nodes-base.httpRequest", pos, p, tv=4.2, on_error=on_error, extra=extra)


def code(name, pos, js):
    return node(name, "n8n-nodes-base.code", pos, {"jsCode": js}, tv=2)


def cond(left, right, op="equals", typ="string"):
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
        "conditions": [
            {"id": str(uuid.uuid5(NS, left + right)), "leftValue": left, "rightValue": right,
             "operator": ({"type": typ, "operation": op, "singleValue": True}
                          if op in ("true", "false", "exists", "notExists")
                          else {"type": typ, "operation": op})}
        ],
        "combinator": "and",
    }


def if_node(name, pos, left, right, op="equals", typ="string"):
    return node(name, "n8n-nodes-base.if", pos,
                {"conditions": cond(left, right, op, typ), "options": {}}, tv=2)


def link(*pairs):
    """link(("A","B"), ("A","C",1)) -> connections dict (output index default 0)."""
    conns: dict = {}
    for p in pairs:
        src, dst = p[0], p[1]
        idx = p[2] if len(p) > 2 else 0
        outs = conns.setdefault(src, {"main": []})["main"]
        while len(outs) <= idx:
            outs.append([])
        outs[idx].append({"node": dst, "type": "main", "index": 0})
    return conns


def workflow(name, nodes, connections, note):
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {"instanceId": "sanitized"},
        "active": False,
        "tags": [],
        "versionId": str(uuid.uuid5(NS, name)),
        "id": str(uuid.uuid5(NS, "id:" + name))[:16].replace("-", ""),
        "description": note,
    }


# ---------------------------------------------------------------------------
# WF1 - Triage Intake
# ---------------------------------------------------------------------------
NORMALIZE_WEBHOOK = r"""
const j = $input.first().json;
const h = j.headers || {};
const b = j.body || {};
const expected = $env.N8N_INGEST_TOKEN || '';
const authorized = expected.length > 0 && (h['x-ingest-token'] || '') === expected;
return [{ json: {
  authorized,
  run_tag: (b.run_tag || '').toString(),
  email: {
    message_id: (b.message_id || ('webhook-' + Date.now())).toString(),
    source: b.source || 'webhook',
    from_email: (b.from_email || '').toString(),
    from_name: (b.from_name || '').toString(),
    subject: (b.subject || '').toString().slice(0, 500),
    body_text: (b.body_text || '').toString().slice(0, 8000),
    received_at: b.received_at || new Date().toISOString(),
  }
}}];
""".strip()

NORMALIZE_IMAP = r"""
// Read-only IMAP trigger output -> InboundEmail shape. Never replies, never deletes.
const j = $input.first().json;
const fromObj = j.from && j.from.value && j.from.value[0] ? j.from.value[0] : {};
const addr = fromObj.address || (typeof j.from === 'string' ? (j.from.match(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/) || [''])[0] : '');
const name = fromObj.name || '';
const body = (j.textPlain || j.text || j.textHtml || '').toString();
return [{ json: {
  authorized: true,
  run_tag: 'imap',
  email: {
    message_id: (j.messageId || (j.metadata && j.metadata['message-id']) || ('imap-' + Date.now())).toString(),
    source: 'imap',
    from_email: addr,
    from_name: name,
    subject: (j.subject || '').toString().slice(0, 500),
    body_text: body.slice(0, 8000),
    received_at: j.date ? new Date(j.date).toISOString() : new Date().toISOString(),
  }
}}];
""".strip()

BUILD_RESPONSE = r"""
const t = $('HTTP: Triage').first().json;
let slackPosted = null;
try { slackPosted = !!$('Slack: Post Approval').first().json.ok; } catch (e) {}
return [{ json: {
  run_id: t.run_id, status: t.status, route: t.route, attempts: t.attempts,
  triage: t.triage || null, approval_id: t.approval_id || null, slack_posted: slackPosted,
}}];
""".strip()


def wf1():
    webhook = node("Webhook: triage-ingest", "n8n-nodes-base.webhook", [0, 0],
                   {"httpMethod": "POST", "path": "triage-ingest", "responseMode": "responseNode", "options": {}},
                   tv=2, extra={"webhookId": str(uuid.uuid5(NS, "wh-ingest"))})
    imap = node("Email Trigger (IMAP) [read-only, disabled]", "n8n-nodes-base.emailReadImap", [0, 240],
                {"mailbox": "INBOX", "postProcessAction": "nothing", "format": "simple",
                 "options": {"forceReconnect": 60}},
                tv=2, creds={"imap": {"id": "sanitized", "name": "TriageGate IMAP"}}, disabled=True)
    norm_w = code("Normalize: webhook", [240, 0], NORMALIZE_WEBHOOK)
    norm_i = code("Normalize: IMAP", [240, 240], NORMALIZE_IMAP)
    authz = if_node("IF: authorized", [480, 120], "={{ $json.authorized }}", "", "true", "boolean")
    authz["parameters"]["conditions"]["conditions"][0]["rightValue"] = ""
    unauth = node("Respond: 401", "n8n-nodes-base.respondToWebhook", [720, 300],
                  {"respondWith": "json", "responseBody": '={{ { "error": "unauthorized" } }}',
                   "options": {"responseCode": 401}}, tv=1.1)
    triage = http("HTTP: Triage", [720, 80], "POST",
                  "={{ 'http://triage-api:8000/triage' + ($json.run_tag ? '?run_tag=' + encodeURIComponent($json.run_tag) : '') }}",
                  body="={{ JSON.stringify($json.email) }}", timeout=120000,
                  extra={"retryOnFail": True, "maxTries": 2, "waitBetweenTries": 2000})
    route = if_node("IF: needs approval?", [960, 80], "={{ $json.route }}", "approval")
    slack = http("Slack: Post Approval", [1200, 0], "POST", "https://slack.com/api/chat.postMessage",
                 body="={{ JSON.stringify({ channel: $env.SLACK_APPROVAL_CHANNEL_ID, text: $json.slack_text, blocks: $json.slack_blocks }) }}",
                 headers=SLACK_H, on_error="continueRegularOutput")
    slack_ok = if_node("IF: Slack ok?", [1440, 0], "={{ $json.ok }}", "", "true", "boolean")
    save = http("HTTP: Save Slack Ref", [1680, -40], "POST",
                "={{ 'http://triage-api:8000/approvals/' + $('HTTP: Triage').first().json.approval_id + '/slack-ref' }}",
                body="={{ JSON.stringify({ channel: $json.channel, ts: $json.ts }) }}", on_error="continueRegularOutput")
    build = code("Code: Build response", [1920, 120], BUILD_RESPONSE)
    respond = node("Respond: result", "n8n-nodes-base.respondToWebhook", [2160, 120],
                   {"respondWith": "firstIncomingItem", "options": {}}, tv=1.1)

    nodes = [webhook, imap, norm_w, norm_i, authz, unauth, triage, route, slack, slack_ok, save, build, respond]
    conns = link(
        ("Webhook: triage-ingest", "Normalize: webhook"),
        ("Email Trigger (IMAP) [read-only, disabled]", "Normalize: IMAP"),
        ("Normalize: webhook", "IF: authorized"),
        ("Normalize: IMAP", "IF: authorized"),
        ("IF: authorized", "HTTP: Triage", 0),
        ("IF: authorized", "Respond: 401", 1),
        ("HTTP: Triage", "IF: needs approval?"),
        ("IF: needs approval?", "Slack: Post Approval", 0),
        ("IF: needs approval?", "Code: Build response", 1),
        ("Slack: Post Approval", "IF: Slack ok?"),
        ("IF: Slack ok?", "HTTP: Save Slack Ref", 0),
        ("IF: Slack ok?", "Code: Build response", 1),
        ("HTTP: Save Slack Ref", "Code: Build response"),
        ("Code: Build response", "Respond: result"),
    )
    return workflow("TriageGate - WF1 Triage Intake", nodes, conns,
                    "Webhook (eval/replay) or read-only IMAP -> triage-api -> Slack approval card.")


# ---------------------------------------------------------------------------
# WF2 - Slack approval handler
# ---------------------------------------------------------------------------
EXTRACT_RAW = r"""
// Slack signs the exact raw bytes, so read the raw body binary, not the parsed form.
const item = $input.first();
const h = item.json.headers || {};
const buf = await this.helpers.getBinaryDataBuffer(0, 'data');
return [{ json: {
  raw_body: buf.toString('utf8'),
  signature: h['x-slack-signature'] || '',
  timestamp: h['x-slack-request-timestamp'] || '',
}}];
""".strip()

CONTACT_ID = r"""
const d = $('HTTP: Decide').first().json;
let r = null, step = '';
for (const n of ['HS: Update Contact', 'HS: Create Contact']) {
  try { const j = $(n).first().json; if (j) { r = j; step = n; } } catch (e) {}
}
r = r || {};
let id = r.id ? String(r.id) : null;
let error = null;
if (!id) {
  const txt = JSON.stringify(r);
  const m = txt.match(/Existing ID: ?(\d+)/);       // HubSpot 409: contact already exists
  if (m) { id = m[1]; }
  else { error = (step || 'contact') + ' failed: ' + txt.slice(0, 300); }
}
return [{ json: { contact_id: id, error, payload: d.crm_payload } }];
""".strip()

BUILD_CRM_RESULT = r"""
const c = $('Code: Contact ID').first().json;
const d = $('HTTP: Decide').first().json;
const out = { status: 'success', hubspot_contact_id: c.contact_id, hubspot_company_id: null,
              hubspot_ticket_id: null, error: null };
const errs = [];
if (c.error) errs.push(c.error);
for (const [n, key] of [['HS: Create Company', 'hubspot_company_id'], ['HS: Create Ticket', 'hubspot_ticket_id']]) {
  try {
    const j = $(n).first().json;
    if (j && j.id) out[key] = String(j.id);
    else errs.push(n + ' failed: ' + JSON.stringify(j).slice(0, 300));
  } catch (e) {}
}
if (errs.length) { out.status = 'failed'; out.error = errs.join(' | ').slice(0, 900); }
const who = (d.approval && d.approval.decided_by) || 'reviewer';
const ids = [out.hubspot_contact_id && ('contact ' + out.hubspot_contact_id),
             out.hubspot_company_id && ('company ' + out.hubspot_company_id),
             out.hubspot_ticket_id && ('ticket ' + out.hubspot_ticket_id)].filter(Boolean).join(', ');
const headline = out.status === 'success'
  ? 'Approved by ' + who + ' - written to HubSpot (' + ids + ').'
  : 'Approved by ' + who + ' but the HubSpot write FAILED - logged for review.';
return [{ json: { result: out, headline, channel: d.approval && d.approval.slack_channel,
                  ts: d.approval && d.approval.slack_ts, approval_id: d.approval && d.approval.approval_id } }];
""".strip()


def slack_update(name, pos, channel, ts, text, blocks):
    return http(name, pos, "POST", "https://slack.com/api/chat.update",
                body="={{ JSON.stringify({ channel: " + channel + ", ts: " + ts + ", text: " + text + ", blocks: " + blocks + " }) }}",
                headers=SLACK_H, on_error="continueRegularOutput")


def wf2():
    webhook = node("Webhook: slack-actions", "n8n-nodes-base.webhook", [0, 0],
                   {"httpMethod": "POST", "path": "slack-actions", "responseMode": "onReceived",
                    "responseCode": 200, "responseData": "noData", "options": {"rawBody": True}},
                   tv=2, extra={"webhookId": str(uuid.uuid5(NS, "wh-slack"))})
    extract = code("Code: Extract raw body", [240, 0], EXTRACT_RAW)
    decide = http("HTTP: Decide", [480, 0], "POST", API + "/approvals/decide",
                  body="={{ JSON.stringify({ raw_body: $json.raw_body, signature: $json.signature, timestamp: $json.timestamp }) }}",
                  timeout=20000)

    def rule(key):
        return {"conditions": cond("={{ $json.outcome }}", key), "renameOutput": True, "outputKey": key}

    switch = node("Switch: outcome", "n8n-nodes-base.switch", [720, 0],
                  {"rules": {"values": [rule("write_crm"), rule("rejected"), rule("expired")]},
                   "options": {"fallbackOutput": "extra", "renameFallbackOutput": "no-op (duplicate / invalid)"}},
                  tv=3)
    noop = node("NoOp: duplicate / invalid_signature", "n8n-nodes-base.noOp", [980, 460], {})

    # --- reject / expired: only update the Slack card; Python already logged the event
    upd_rej = slack_update("Slack: Update (rejected)", [980, 160], "$json.approval.slack_channel",
                           "$json.approval.slack_ts", "$json.slack_text", "$json.slack_blocks")
    upd_exp = slack_update("Slack: Update (expired)", [980, 300], "$json.approval.slack_channel",
                           "$json.approval.slack_ts", "$json.slack_text", "$json.slack_blocks")

    # --- approved: HubSpot write
    P = "$('HTTP: Decide').first().json.crm_payload"
    search = http("HS: Search Contact", [980, -200], "POST",
                  hs("/crm/v3/objects/contacts/search"),
                  body="={{ JSON.stringify({ filterGroups: [{ filters: [{ propertyName: 'email', operator: 'EQ', value: " + P + ".contact.email }] }], properties: ['email'], limit: 1 }) }}",
                  headers=HS_H, on_error="continueRegularOutput")
    exists = if_node("IF: contact exists?", [1200, -200], "={{ ($json.results || []).length > 0 }}", "", "true", "boolean")
    update_c = http("HS: Update Contact", [1440, -300], "PATCH",
                    hs("/crm/v3/objects/contacts/", "$json.results[0].id"),
                    body="={{ JSON.stringify({ properties: " + P + ".contact }) }}",
                    headers=HS_H, on_error="continueRegularOutput")
    create_c = http("HS: Create Contact", [1440, -120], "POST", hs("/crm/v3/objects/contacts"),
                    body="={{ JSON.stringify({ properties: " + P + ".contact }) }}",
                    headers=HS_H, on_error="continueRegularOutput")
    cid = code("Code: Contact ID", [1680, -200], CONTACT_ID)
    is_support = if_node("IF: support_request?", [1900, -200], "={{ $json.payload.category }}", "support_request")
    ticket = http("HS: Create Ticket", [2140, -300], "POST", hs("/crm/v3/objects/tickets"),
                  body="={{ JSON.stringify({ properties: $json.payload.ticket, associations: [{ to: { id: $json.contact_id }, types: [{ associationCategory: 'HUBSPOT_DEFINED', associationTypeId: 16 }] }] }) }}",
                  headers=HS_H, on_error="continueRegularOutput")
    has_co = if_node("IF: has company?", [2140, -100], "={{ !!$json.payload.company && !!$json.contact_id }}", "", "true", "boolean")
    company = http("HS: Create Company", [2380, -140], "POST", hs("/crm/v3/objects/companies"),
                   body="={{ JSON.stringify({ properties: $json.payload.company, associations: [{ to: { id: $json.contact_id }, types: [{ associationCategory: 'HUBSPOT_DEFINED', associationTypeId: 280 }] }] }) }}",
                   headers=HS_H, on_error="continueRegularOutput")
    result = code("Code: Build CRM result", [2620, -200], BUILD_CRM_RESULT)
    crm_res = http("HTTP: CRM result", [2860, -200], "POST",
                   "={{ 'http://triage-api:8000/approvals/' + $json.approval_id + '/crm-result' }}",
                   body="={{ JSON.stringify($json.result) }}")
    final = slack_update("Slack: Update (final)", [3100, -200],
                         "$('Code: Build CRM result').first().json.channel",
                         "$('Code: Build CRM result').first().json.ts",
                         "$('Code: Build CRM result').first().json.headline",
                         "[{ type: 'section', text: { type: 'mrkdwn', text: '*' + $('Code: Build CRM result').first().json.headline + '*' } }]")

    nodes = [webhook, extract, decide, switch, noop, upd_rej, upd_exp, search, exists, update_c, create_c, cid,
             is_support, ticket, has_co, company, result, crm_res, final]
    conns = link(
        ("Webhook: slack-actions", "Code: Extract raw body"),
        ("Code: Extract raw body", "HTTP: Decide"),
        ("HTTP: Decide", "Switch: outcome"),
        ("Switch: outcome", "HS: Search Contact", 0),
        ("Switch: outcome", "Slack: Update (rejected)", 1),
        ("Switch: outcome", "Slack: Update (expired)", 2),
        ("Switch: outcome", "NoOp: duplicate / invalid_signature", 3),
        ("HS: Search Contact", "IF: contact exists?"),
        ("IF: contact exists?", "HS: Update Contact", 0),
        ("IF: contact exists?", "HS: Create Contact", 1),
        ("HS: Update Contact", "Code: Contact ID"),
        ("HS: Create Contact", "Code: Contact ID"),
        ("Code: Contact ID", "IF: support_request?"),
        ("IF: support_request?", "HS: Create Ticket", 0),
        ("IF: support_request?", "IF: has company?", 1),
        ("IF: has company?", "HS: Create Company", 0),
        ("IF: has company?", "Code: Build CRM result", 1),
        ("HS: Create Ticket", "Code: Build CRM result"),
        ("HS: Create Company", "Code: Build CRM result"),
        ("Code: Build CRM result", "HTTP: CRM result"),
        ("HTTP: CRM result", "Slack: Update (final)"),
    )
    return workflow("TriageGate - WF2 Slack Approval Handler", nodes, conns,
                    "Slack interactivity -> signature check + atomic approval -> HubSpot write (once) -> card update.")


# ---------------------------------------------------------------------------
# WF3 - approval expiry
# ---------------------------------------------------------------------------
def wf3():
    sched = node("Schedule: every minute", "n8n-nodes-base.scheduleTrigger", [0, 0],
                 {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}}, tv=1.2)
    expire = http("HTTP: Expire overdue", [240, 0], "POST", API + "/approvals/expire", body="={{ '{}' }}")
    split = node("Split Out: expired", "n8n-nodes-base.splitOut", [480, 0],
                 {"fieldToSplitOut": "expired", "options": {}}, tv=1)
    has_ref = if_node("IF: has Slack ref?", [720, 0], "={{ $json.slack_ts }}", "", "exists", "string")
    upd = slack_update("Slack: Update (expired)", [960, -60], "$json.slack_channel", "$json.slack_ts",
                       "$json.slack_text", "$json.slack_blocks")
    nodes = [sched, expire, split, has_ref, upd]
    conns = link(
        ("Schedule: every minute", "HTTP: Expire overdue"),
        ("HTTP: Expire overdue", "Split Out: expired"),
        ("Split Out: expired", "IF: has Slack ref?"),
        ("IF: has Slack ref?", "Slack: Update (expired)", 0),
    )
    return workflow("TriageGate - WF3 Approval Expiry", nodes, conns,
                    "Every minute: expire overdue approvals (TTL 24h prod / 2 min demo) and update the Slack cards.")


def main():
    for fname, wf in [("wf1_triage_intake.json", wf1()), ("wf2_slack_approval_handler.json", wf2()),
                      ("wf3_approval_expiry.json", wf3())]:
        (OUT / fname).write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", fname)


if __name__ == "__main__":
    main()
