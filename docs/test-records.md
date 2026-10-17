# Test records created in HubSpot

Every record TriageGate creates is marked `[TriageGate test]` (contact last name, company name, ticket subject).

## Real HubSpot portal: 0 records created

The HubSpot service key supplied for this build is **read-only**: contacts, companies and tickets can be
read and searched, but every create call returns `403 This app hasn't been granted all required scopes`.
Nothing was created, so there is nothing to clean up in the real portal. The failure path was exercised
for real and behaves as designed: the approval ends as `crm_failed`, the error is logged in `crm_writes`,
and the Slack card is updated to say the write failed (evidence: `eval/results/real_hubspot_check.json`).

To finish the real-CRM proof, add the `.write` scopes (see `docs/hubspot_setup.md`) to the service key and run:

```
python eval/run_eval.py --mode n8n --n8n-url http://localhost:5678/webhook/triage-ingest --run-tag real-1 --simulate-clicks --limit 14
python scripts/count_hubspot_test_records.py     # lists the ids it created
```

## Local HubSpot stand-in (NOT HubSpot)

The eval run of 2026-10-17 wrote to `tools/hubspot_mock` (a small in-memory service with the same endpoints
and the same 409 "Existing ID" behaviour) so the n8n write path and idempotency could be measured.
These ids exist only inside that mock container and vanish when it stops; nothing to delete in HubSpot.

| approval | contact id | company id | ticket id |
|---|---|---|---|
| 0fb71f89 | 1000 | 1001 | - |
| 76bf083f | 1002 | 1003 | - |
| de400b21 | 1004 | - | 1005 |
| 539052e5 | 1006 | - | 1007 |
| d4d97744 | 1008 | 1009 | - |
| 19f3be5e | 1010 | 1011 | - |
| 43f81cf1 | 1012 | - | - |
| c25447d2 | 1013 | - | 1014 |
| ebc15a57 | 1015 | - | 1016 |

Totals in the mock: 9 contacts, 4 companies, 4 tickets for 9 distinct approved approvals.
