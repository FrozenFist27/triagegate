# Upwork portfolio entry (DEMO PROJECT - not client work)

**Title:** AI Email Triage in n8n with Slack Approval and HubSpot (Demo Project)

**Label:** Demo project built with synthetic data. Not a real client engagement.

**Description:**
An n8n workflow set that triages a shared inbox with an LLM and asks a human in Slack before anything is
written to HubSpot. A small Python (FastAPI) service validates the model output against a strict JSON schema,
retries invalid answers and quarantines failures, verifies Slack signatures, and makes approvals idempotent so
double clicks never create duplicate records. Stale approvals expire automatically and every run is logged with
input, model output, decision and result.

**Measured on 40 synthetic labeled emails (2026-10-17, claude-haiku-5-5):** 40/40 classified correctly,
0 of 8 forced-invalid model outputs reached the CRM, 0 duplicate writes in 5 double-approve trials (one
simultaneous), median 1.4 s per email in the service, about $0.00012 per email. CRM idempotency was measured
against a local HubSpot stand-in because the supplied test key was read-only [update after write scopes are added].

**Role:** Solo - design, n8n workflows, Python service, tests, evaluation.
**Skills:** n8n, Python, FastAPI, LLM / Claude API, HubSpot API, Slack API, workflow automation, Docker.
**Links:** GitHub repo [PLACEHOLDER - after the owner approves publishing], demo video [PLACEHOLDER].
