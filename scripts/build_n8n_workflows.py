"""Generate the three TriageGate n8n workflows as importable JSON.

Hand-authoring n8n JSON is error prone; this script keeps node ids, connection
names and positions consistent. Run:

    python scripts/build_n8n_workflows.py

Output: n8n/workflows/{wf1,wf2,wf3}_*.json  (sanitized: no credential ids)
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "n8n" / "workflows"
OUT.mkdir(parents=True, exist_ok=True)


def nid() -> str:
    return str(uuid.uuid4())


def node(name: str, ntype: str, position: list[int], parameters: dict,
         type_version: int = 1, credentials: dict | None = None,
         disabled: bool = False) -> dict:
    n = {
        "parameters": parameters,
        "id": nid(),
        "name": name,
        "type": ntype,
        "typeVersion": type_version,
        "position": position,
    }
    if credentials:
        n["credentials"] = credentials
    if disabled:
        n["disabled"] = True
    return n


def workflow(name: str, nodes: list[dict], connections: dict, comments: str = "") -> dict:
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1", "saveManualExecutions": True},
        "pinData": {},
        "meta": {"instanceId": "sanitized"},
        "active": False,
        "tags": [],
        "pinData": {},
        "__note__": comments or None,
    }


# --------------------------------------------------------------------------
# WF1: Triage Intake
# --------------------------------------------------------------------------
def wf1() -> dict:
    webhook = node(
        "Webhook: triage-ingest", "n8n-nodes-base.webhook", [0, 0],
        {
            "httpMethod": "POST",
            "path": "triage-ingest",
            "responseMode": "responseNode",
            "options": {},
        },
        type_version=2,
        credentials={"headerAuth": {"id": "sanitized", "name": "TriageGate Ingest Token"}},
    )
    imap = node(
        "Email Trigger (IMAP) [enable manually]", "n8n-nodes-base.emailTrigger", [0, 220],
        {
            "mailbox": "INBOX",
            "options": {},
            "downloadAttachments": False,
        },
        type_version=1,
        credentials={"imap": {"id": "sanitized", "name": "TriageGate IMAP"}},
        disabled=True,
    )
    normalize = node(
        "Set: Normalize", "n8n-nodes-base.set", [240, 110],
        {
            "mode": "manual",
            "duplicateItem": False,
            "assignments": {
                "assignments": [
                    {"id": "1", "name": "message_id",
                     "value": "={{ $json.messageId || $json.MessageID || $json['ID'] || $json.id || $('Webhook: triage-ingest').item.json.headers['x-eval-message-id'] || ('webhook-' + Date.now()) }}",
                     "type": "string"},
                    {"id": "2", "name": "source",
                     "value": "={{ $json.source || ($node['Email Trigger (IMAP) [enable manually]').name && $execution.active ? 'imap' : 'webhook') }}",
                     "type": "string"},
                    {"id": "3", "name": "from_email",
                     "value": "={{ ($json.from || $json.sender || $json.senderAddress || '').toString().match(/[\\w.+-]+@[\\w.-]+\\.[A-Za-z]{2,}/)?.[0] || '' }}",
                     "type": "string"},
                    {"id": "4", "name": "from_name",
                     "value": "={{ ($json.from || $json.fromName || $json.sender || '').toString().replace(/[\\w.+-]+@[\\w.-]+\\.[A-Za-z]{2,}/, '').replace(/[<>\"']/g, '').trim() }}",
                     "type": "string"},
                    {"id": "5", "name": "subject", "value": "={{ ($json.subject || '').toString().slice(0, 500) }}", "type": "string"},
                    {"id": "6", "name": "body_text",
                     "value": "={{ ($json.text || $json.body || $json.bodyText || $json.textPlain || '').toString().slice(0, 8000) }}",
                     "type": "string"},
                    {"id": "7", "name": "received_at",
                     "value": "={{ $json.date || $json.receivedAt || new Date().toISOString() }}",
                     "type": "string"},
                    {"id": "8", "name": "source_hint",
                     "value": "={{ $json.source || 'webhook' }}", "type": "string"},
                ]
            },
            "options": {},
        },
        type_version=3.4,
    )
    triage = node(
        "HTTP: Triage", "n8n-nodes-base.httpRequest", [480, 110],
        {
            "method": "POST",
            "url": "http://triage-api:8000/triage",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ message_id: $json.message_id, source: $json.source_hint === 'imap' ? 'imap' : 'webhook', from_email: $json.from_email, from_name: $json.from_name, subject: $json.subject, body_text: $json.body_text, received_at: $json.received_at }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "X-Internal-Token", "value": "={{ $env.TRIAGE_API_TOKEN }}"},
                    {"name": "Content-Type", "value": "application/json"},
                ]
            },
            "options": {"timeout": 60000},
        },
        type_version=4.2,
        credentials={"httpHeaderAuth": {"id": "sanitized", "name": "TriageGate Internal Token"}},
    )
    if_route = node(
        "IF: needs approval?", "n8n-nodes-base.if", [720, 110],
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                "conditions": [
                    {
                        "id": "c1",
                        "leftValue": "={{ $json.route }}",
                        "rightValue": "approval",
                        "operator": {"type": "string", "operation": "equals"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        type_version=2,
    )
    slack_post = node(
        "Slack: Post Approval", "n8n-nodes-base.slack", [960, 0],
        {
            "resource": "message",
            "operation": "post",
            "select": "channel",
            "channelId": {"__rl": True, "mode": "id", "value": "={{ $env.SLACK_APPROVAL_CHANNEL_ID }}"},
            "text": "={{ $json.slack_text }}",
            "otherOptions": {"includeLinkToWorkflow": False},
        },
        type_version=2.2,
        credentials={"slackApi": {"id": "sanitized", "name": "TriageGate Slack Bot"}},
    )
    # attach blocks via jsonBody override on a following HTTP node is more portable
    slack_blocks = node(
        "Slack: Post Blocks (chat.postMessage)", "n8n-nodes-base.httpRequest", [1200, 0],
        {
            "method": "POST",
            "url": "https://slack.com/api/chat.postMessage",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ channel: $env.SLACK_APPROVAL_CHANNEL_ID, text: $('HTTP: Triage').item.json.slack_text, blocks: $('HTTP: Triage').item.json.slack_blocks }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.SLACK_BOT_TOKEN }}"},
                    {"name": "Content-Type", "value": "application/json; charset=utf-8"},
                ]
            },
            "options": {},
        },
        type_version=4.2,
    )
    save_ref = node(
        "HTTP: Save Slack Ref", "n8n-nodes-base.httpRequest", [1440, 0],
        {
            "method": "POST",
            "url": "=http://triage-api:8000/approvals/{{ $('HTTP: Triage').item.json.approval_id }}/slack-ref",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ channel: $json.channel, ts: $json.ts }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [{"name": "X-Internal-Token", "value": "={{ $env.TRIAGE_API_TOKEN }}"}]
            },
            "options": {},
        },
        type_version=4.2,
    )
    respond = node(
        "Respond: result", "n8n-nodes-base.respondToWebhook", [1440, 220],
        {
            "respondWith": "json",
            "responseBody": "={{ JSON.stringify({ run_id: $('HTTP: Triage').item.json.run_id, status: $('HTTP: Triage').item.json.status, route: $('HTTP: Triage').item.json.route, category: $('HTTP: Triage').item.json.triage ? $('HTTP: Triage').item.json.triage.category : null, approval_id: $('HTTP: Triage').item.json.approval_id }) }}",
            "options": {},
        },
        type_version=1.1,
    )

    nodes = [webhook, imap, normalize, triage, if_route, slack_post, slack_blocks, save_ref, respond]
    connections = {
        "Webhook: triage-ingest": {"main": [[{"node": "Set: Normalize", "type": "main", "index": 0}]]},
        "Email Trigger (IMAP) [enable manually]": {"main": [[{"node": "Set: Normalize", "type": "main", "index": 0}]]},
        "Set: Normalize": {"main": [[{"node": "HTTP: Triage", "type": "main", "index": 0}]]},
        "HTTP: Triage": {"main": [[{"node": "IF: needs approval?", "type": "main", "index": 0}]]},
        "IF: needs approval?": {
            "main": [
                [{"node": "Slack: Post Blocks (chat.postMessage)", "type": "main", "index": 0}],
                [{"node": "Respond: result", "type": "main", "index": 0}],
            ]
        },
        "Slack: Post Blocks (chat.postMessage)": {"main": [[{"node": "HTTP: Save Slack Ref", "type": "main", "index": 0}]]},
        "HTTP: Save Slack Ref": {"main": [[{"node": "Respond: result", "type": "main", "index": 0}]]},
    }
    return workflow(
        "TriageGate - WF1 Triage Intake",
        nodes, connections,
        comments="Ingest -> triage-api -> Slack approval card. IMAP node is imported disabled.",
    )


# --------------------------------------------------------------------------
# WF2: Slack Approval Handler
# --------------------------------------------------------------------------
def wf2() -> dict:
    webhook = node(
        "Webhook: slack-actions", "n8n-nodes-base.webhook", [0, 0],
        {
            "httpMethod": "POST",
            "path": "slack-actions",
            "responseMode": "onReceived",
            "responseData": "firstEntryJson",
            "options": {},
        },
        type_version=2,
    )
    decide = node(
        "HTTP: Decide", "n8n-nodes-base.httpRequest", [260, 0],
        {
            "method": "POST",
            "url": "http://triage-api:8000/approvals/decide",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ raw_body: $json.body || $json.rawBody || $json.data || '', signature: $json.headers['x-slack-signature'] || '', timestamp: $json.headers['x-slack-request-timestamp'] || '' }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [{"name": "X-Internal-Token", "value": "={{ $env.TRIAGE_API_TOKEN }}"}]
            },
            "options": {"timeout": 20000},
        },
        type_version=4.2,
    )
    switch = node(
        "Switch: outcome", "n8n-nodes-base.switch", [500, 0],
        {
            "rules": {
                "values": [
                    {
                        "conditions": {
                            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                            "conditions": [
                                {"id": "w1", "leftValue": "={{ $json.outcome }}", "rightValue": "write_crm",
                                 "operator": {"type": "string", "operation": "equals"}}
                            ],
                            "combinator": "and",
                        },
                        "renameOutput": True,
                        "outputKey": "write_crm",
                    },
                    {
                        "conditions": {
                            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                            "conditions": [
                                {"id": "w2", "leftValue": "={{ $json.outcome }}", "rightValue": "rejected",
                                 "operator": {"type": "string", "operation": "equals"}}
                            ],
                            "combinator": "and",
                        },
                        "renameOutput": True,
                        "outputKey": "rejected",
                    },
                    {
                        "conditions": {
                            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                            "conditions": [
                                {"id": "w3", "leftValue": "={{ $json.outcome }}", "rightValue": "expired",
                                 "operator": {"type": "string", "operation": "equals"}}
                            ],
                            "combinator": "and",
                        },
                        "renameOutput": True,
                        "outputKey": "expired",
                    },
                ],
                "options": {"fallbackOutput": "extra", "renameFallbackOutput": True},
            },
            "options": {},
        },
        type_version=3,
    )

    hs_search = node(
        "HubSpot: Search Contact", "n8n-nodes-base.httpRequest", [760, -160],
        {
            "method": "POST",
            "url": "https://api.hubapi.com/crm/v3/objects/contacts/search",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ filterGroups: [{ filters: [{ propertyName: 'email', operator: 'EQ', value: $json.crm_payload.contact.email }] }], properties: ['email', 'firstname', 'lastname', 'company'], limit: 1 }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.HUBSPOT_SERVICE_KEY }}"},
                    {"name": "Content-Type", "value": "application/json"},
                ]
            },
            "options": {"timeout": 30000},
            "onError": "continueRegularOutput",
        },
        type_version=4.2,
    )
    hs_contact = node(
        "HubSpot: Upsert Contact", "n8n-nodes-base.httpRequest", [1000, -160],
        {
            "method": "POST",
            "url": "=https://api.hubapi.com/crm/v3/objects/contacts{{ $('Switch: outcome').item.json.crm_payload.contact && $json.results && $json.results.length ? '/' + $json.results[0].id : '' }}",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ properties: $('Switch: outcome').item.json.crm_payload.contact }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.HUBSPOT_SERVICE_KEY }}"},
                    {"name": "Content-Type", "value": "application/json"},
                ]
            },
            "options": {"timeout": 30000},
            "onError": "continueRegularOutput",
        },
        type_version=4.2,
    )
    hs_company = node(
        "HubSpot: Create Company", "n8n-nodes-base.httpRequest", [1240, -260],
        {
            "method": "POST",
            "url": "https://api.hubapi.com/crm/v3/objects/companies",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ $('Switch: outcome').item.json.crm_payload.company ? JSON.stringify({ properties: $('Switch: outcome').item.json.crm_payload.company }) : JSON.stringify({ properties: { name: '[TriageGate test] ' + ($('Switch: outcome').item.json.crm_payload.contact.company || 'Unknown') } }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.HUBSPOT_SERVICE_KEY }}"},
                    {"name": "Content-Type", "value": "application/json"},
                ]
            },
            "options": {"timeout": 30000},
            "onError": "continueRegularOutput",
        },
        type_version=4.2,
    )
    hs_ticket = node(
        "HubSpot: Create Ticket", "n8n-nodes-base.httpRequest", [1240, -60],
        {
            "method": "POST",
            "url": "https://api.hubapi.com/crm/v3/objects/tickets",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ properties: $('Switch: outcome').item.json.crm_payload.ticket }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.HUBSPOT_SERVICE_KEY }}"},
                    {"name": "Content-Type", "value": "application/json"},
                ]
            },
            "options": {"timeout": 30000},
            "onError": "continueRegularOutput",
        },
        type_version=4.2,
    )
    if_lead = node(
        "IF: sales_lead?", "n8n-nodes-base.if", [1120, -60],
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                "conditions": [
                    {"id": "l1", "leftValue": "={{ $('Switch: outcome').item.json.crm_payload.category }}",
                     "rightValue": "sales_lead", "operator": {"type": "string", "operation": "equals"}}
                ],
                "combinator": "and",
            },
            "options": {},
        },
        type_version=2,
    )
    crm_ok = node(
        "HTTP: CRM result success", "n8n-nodes-base.httpRequest", [1500, -160],
        {
            "method": "POST",
            "url": "=http://triage-api:8000/approvals/{{ $('Switch: outcome').item.json.approval.approval_id }}/crm-result",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ status: 'success', hubspot_contact_id: ($json.id || $('HubSpot: Upsert Contact').item.json.id || null), hubspot_company_id: ($('HubSpot: Create Company').isExecuted ? $('HubSpot: Create Company').item.json.id : null), hubspot_ticket_id: ($('HubSpot: Create Ticket').isExecuted ? $('HubSpot: Create Ticket').item.json.id : null) }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [{"name": "X-Internal-Token", "value": "={{ $env.TRIAGE_API_TOKEN }}"}]
            },
            "options": {},
        },
        type_version=4.2,
    )
    crm_fail = node(
        "HTTP: CRM result failed", "n8n-nodes-base.httpRequest", [1500, 60],
        {
            "method": "POST",
            "url": "=http://triage-api:8000/approvals/{{ $('Switch: outcome').item.json.approval.approval_id }}/crm-result",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ status: 'failed', error: ($json.error && $json.error.message ? $json.error.message : 'HubSpot write failed').toString().slice(0, 400) }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [{"name": "X-Internal-Token", "value": "={{ $env.TRIAGE_API_TOKEN }}"}]
            },
            "options": {},
        },
        type_version=4.2,
    )
    slack_update = node(
        "Slack: Update Message", "n8n-nodes-base.httpRequest", [1760, -80],
        {
            "method": "POST",
            "url": "https://slack.com/api/chat.update",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ channel: $('Switch: outcome').item.json.approval.slack_channel, ts: $('Switch: outcome').item.json.approval.slack_ts, text: ($json.outcome === 'write_crm' ? ':white_check_mark: Approved and written to HubSpot.' : ($('Switch: outcome').item.json.outcome === 'rejected' ? 'Rejected - no CRM record created.' : ($('Switch: outcome').item.json.outcome === 'expired' ? 'Expired - no action taken.' : 'Processed.'))), blocks: [{ type: 'section', text: { type: 'mrkdwn', text: ($json.outcome === 'write_crm' ? ':white_check_mark: Approved and written to HubSpot by <@' + ($('Switch: outcome').item.json.approval.decided_by || 'unknown') + '>.' : ($('Switch: outcome').item.json.outcome === 'rejected' ? ':x: Rejected - no CRM record created.' : ($('Switch: outcome').item.json.outcome === 'expired' ? ':clock1: Expired - no action taken.' : 'Processed.'))) } }] }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.SLACK_BOT_TOKEN }}"},
                    {"name": "Content-Type", "value": "application/json; charset=utf-8"},
                ]
            },
            "options": {},
            "onError": "continueRegularOutput",
        },
        type_version=4.2,
    )
    slack_rejected = node(
        "Slack: Update Rejected", "n8n-nodes-base.httpRequest", [760, 120],
        {
            "method": "POST",
            "url": "https://slack.com/api/chat.update",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ channel: $json.approval.slack_channel, ts: $json.approval.slack_ts, text: 'Rejected by ' + ($json.approval.decided_by || 'unknown') + ' - no CRM record created.' }) }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.SLACK_BOT_TOKEN }}"},
                    {"name": "Content-Type", "value": "application/json; charset=utf-8"},
                ]
            },
            "options": {},
            "onError": "continueRegularOutput",
        },
        type_version=4.2,
    )
    slack_expired = node(
        "Slack: Update Expired", "n8n-nodes-base.httpRequest", [760, 260],
        {
            "method": "POST",
            "url": "https://slack.com/api/chat.update",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ $json.approval && $json.approval.slack_channel ? JSON.stringify({ channel: $json.approval.slack_channel, ts: $json.approval.slack_ts, text: 'Expired - no action taken.' }) : '{}' }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.SLACK_BOT_TOKEN }}"},
                    {"name": "Content-Type", "value": "application/json; charset=utf-8"},
                ]
            },
            "options": {},
            "onError": "continueRegularOutput",
        },
        type_version=4.2,
    )
    noop = node(
        "NoOp: duplicate / bad signature", "n8n-nodes-base.noOp", [760, 400],
        {"content": "Already processed or signature rejected - logged by triage-api, nothing to do."},
    )

    nodes = [
        webhook, decide, switch, hs_search, hs_contact, hs_company, hs_ticket,
        if_lead, crm_ok, crm_fail, slack_update, slack_rejected, slack_expired, noop,
    ]
    connections = {
        "Webhook: slack-actions": {"main": [[{"node": "HTTP: Decide", "type": "main", "index": 0}]]},
        "HTTP: Decide": {"main": [[{"node": "Switch: outcome", "type": "main", "index": 0}]]},
        "Switch: outcome": {
            "main": [
                [{"node": "HubSpot: Search Contact", "type": "main", "index": 0}],
                [{"node": "Slack: Update Rejected", "type": "main", "index": 0}],
                [{"node": "Slack: Update Expired", "type": "main", "index": 0}],
                [{"node": "NoOp: duplicate / bad signature", "type": "main", "index": 0}],
            ]
        },
        "HubSpot: Search Contact": {"main": [[{"node": "HubSpot: Upsert Contact", "type": "main", "index": 0}]]},
        "HubSpot: Upsert Contact": {"main": [[{"node": "IF: sales_lead?", "type": "main", "index": 0}]]},
        "IF: sales_lead?": {
            "main": [
                [{"node": "HubSpot: Create Company", "type": "main", "index": 0}],
                [{"node": "HubSpot: Create Ticket", "type": "main", "index": 0}],
            ]
        },
        "HubSpot: Create Company": {"main": [[{"node": "HTTP: CRM result success", "type": "main", "index": 0}]]},
        "HubSpot: Create Ticket": {"main": [[{"node": "HTTP: CRM result success", "type": "main", "index": 0}]]},
        "HTTP: CRM result success": {"main": [[{"node": "Slack: Update Message", "type": "main", "index": 0}]]},
    }
    return workflow(
        "TriageGate - WF2 Slack Approval Handler",
        nodes, connections,
        comments="Slack interactivity -> signed decide -> idempotent HubSpot write -> message update.",
    )


# --------------------------------------------------------------------------
# WF3: Approval Expiry
# --------------------------------------------------------------------------
def wf3() -> dict:
    schedule = node(
        "Schedule: every 5 min", "n8n-nodes-base.scheduleTrigger", [0, 0],
        {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}},
        type_version=1.2,
    )
    expire = node(
        "HTTP: Expire", "n8n-nodes-base.httpRequest", [240, 0],
        {
            "method": "POST",
            "url": "http://triage-api:8000/approvals/expire",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "{}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [{"name": "X-Internal-Token", "value": "={{ $env.TRIAGE_API_TOKEN }}"}]
            },
            "options": {},
        },
        type_version=4.2,
    )
    split = node(
        "Split Out: expired", "n8n-nodes-base.splitOut", [480, 0],
        {
            "fieldToSplitOut": "expired",
            "options": {"destinationFieldNameName": "expired"},
        },
        type_version=2,
    )
    update = node(
        "Slack: Mark Expired", "n8n-nodes-base.httpRequest", [720, 0],
        {
            "method": "POST",
            "url": "https://slack.com/api/chat.update",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ $json.slack_channel && $json.slack_ts ? JSON.stringify({ channel: $json.slack_channel, ts: $json.slack_ts, text: 'Expired - no action taken.' }) : '{}' }}",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.SLACK_BOT_TOKEN }}"},
                    {"name": "Content-Type", "value": "application/json; charset=utf-8"},
                ]
            },
            "options": {},
            "onError": "continueRegularOutput",
        },
        type_version=4.2,
    )
    nodes = [schedule, expire, split, update]
    connections = {
        "Schedule: every 5 min": {"main": [[{"node": "HTTP: Expire", "type": "main", "index": 0}]]},
        "HTTP: Expire": {"main": [[{"node": "Split Out: expired", "type": "main", "index": 0}]]},
        "Split Out: expired": {"main": [[{"node": "Slack: Mark Expired", "type": "main", "index": 0}]]},
    }
    return workflow(
        "TriageGate - WF3 Approval Expiry",
        nodes, connections,
        comments="Schedule -> expire stale approvals -> mark the Slack cards expired.",
    )


def main() -> int:
    for name, builder in (
        ("wf1_triage_intake.json", wf1),
        ("wf2_slack_approval_handler.json", wf2),
        ("wf3_approval_expiry.json", wf3),
    ):
        wf = builder()
        # drop the helper key so n8n sees a plain workflow
        wf.pop("__note__", None)
        path = OUT / name
        path.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(wf['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
