# Architecture

See the diagram and component table in the README. Key invariants:

1. `/triage` never creates an approval for a run whose output failed validation (`route=needs_review`).
2. `/approvals/decide` verifies the Slack v0 signature (HMAC-SHA256, 5-minute window) before touching state.
3. The approval transition is one atomic `UPDATE ... WHERE status='pending' AND expires_at > now`; exactly one
   caller receives `write_crm`. `crm_writes.approval_id` is `UNIQUE` as a second layer.
4. Expired, rejected and duplicate clicks return no CRM payload and are recorded in the append-only `events` table.
5. Slack and HubSpot secrets are read from the n8n container environment; none is stored in workflow JSON.

Tables: `runs` (input, every attempt's raw output and error, decision, result, tokens), `approvals`, `crm_writes`, `events`.
Endpoints: `/health`, `/triage`, `/approvals/{id}/slack-ref`, `/approvals/decide`, `/approvals/{id}/crm-result`,
`/approvals/expire`, `/runs`, `/runs/export.jsonl`, `/approvals`, `/crm-writes`, `/events` (all but `/health` need `X-Internal-Token`).
