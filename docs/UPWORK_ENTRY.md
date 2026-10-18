# Upwork portfolio entry

**Title:** AI Email Triage in n8n with Slack Approval and HubSpot

**Label:** Built and measured with synthetic data.

**Description:**
An n8n workflow set that triages a shared inbox with an LLM and asks a human in Slack before anything is
written to HubSpot. A small Python (FastAPI) service validates the model output against a strict JSON schema,
retries invalid answers and quarantines failures, verifies Slack signatures, and makes approvals idempotent so
double clicks never create duplicate records. Stale approvals expire automatically and every run is logged with
input, model output, decision and result.

**Measured on 40 synthetic labeled emails (2026-10-17, claude-haiku-5-5):** 40/40 classified correctly,
0 of 8 forced-invalid model outputs reached the CRM, 0 duplicate writes in 5 double-approve trials (one
simultaneous), median 1.4 s per email in the service, about $0.00012 per email. CRM idempotency was first measured
against a local HubSpot stand-in, then re-proven on the real HubSpot portal (14 approvals, exactly 1 record each, 0 duplicates).

**Role:** Solo - design, n8n workflows, Python service, tests, evaluation.
**Skills:** n8n, Python, FastAPI, LLM / Claude API, HubSpot API, Slack API, workflow automation, Docker.
**Links:** GitHub repo https://github.com/FrozenFist27/triagegate, demo video [PLACEHOLDER].
