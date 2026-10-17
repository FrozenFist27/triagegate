# HubSpot setup

TriageGate talks to HubSpot with plain REST calls (no HubSpot node, no webhooks from HubSpot).
Authentication is a **service key** sent as a Bearer token (`HUBSPOT_SERVICE_KEY`).

## Required scopes

| Scope | Why |
|---|---|
| `crm.objects.contacts.read` / `.write` | search by email, create / update the contact |
| `crm.objects.companies.read` / `.write` | create the company for a sales lead (associated to the contact) |
| `crm.objects.tickets.read` / `.write` | create the ticket for a support request (associated to the contact) |
| `crm.schemas.tickets.read` | read ticket pipelines (default `0` / stage `1`) |

**Important:** the `.write` scopes are required. With read-only scopes every write returns
`403 This app hasn't been granted all required scopes`; TriageGate then records `crm_failed`,
updates the Slack card ("HubSpot write FAILED - logged") and creates nothing. This is exactly
what the key supplied for this build did (see `docs/test-records.md`).

## What gets written (all marked `[TriageGate test]`)

| Email category | Records |
|---|---|
| sales_lead | Contact (lastname `<name> [TriageGate test]`, `hs_lead_status=NEW`, `lifecyclestage=lead`) + Company (`[TriageGate test] <company>`) associated to it (type 280) |
| support_request | Contact + Ticket (`subject` starts with `[TriageGate test]`, pipeline `0` stage `1`, priority from urgency) associated to the contact (type 16) |

The contact is looked up by email first; an existing contact is PATCHed, and a `409 ... Existing ID: n`
race is treated as "found". Idempotency per approval is guaranteed one layer earlier (atomic
`pending -> approved` transition + `UNIQUE(approval_id)` in `crm_writes`).

## Cleaning up test records

Search HubSpot for `[TriageGate test]` (contacts: last name, companies: name, tickets: subject) and
delete them, or run `python scripts/count_hubspot_test_records.py` to list ids. IDs created by a run are
also listed in `docs/test-records.md`.
