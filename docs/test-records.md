# Test records created in HubSpot

Every record TriageGate creates is marked `[TriageGate test]` (contact last name, company name, ticket subject).

## Real HubSpot portal: 27 records created (2026-10-18)

After the `.write` scopes were added to the service key, the eval ran against the real portal
(`eval/results/real_hubspot_run_summary.json`, `real_hubspot_expiry.json`). 14 approvals were approved, each
produced exactly one write row. Rejected and expired approvals created nothing. Delete these in HubSpot when
done (search for "TriageGate"). List them with `python scripts/count_hubspot_test_records.py`.

| approval | contact id | company id | ticket id |
|---|---|---|---|
| 6a6b4bd3 | 571991871193 | 353240830695 | - |
| 11c6e382 | 571975206634 | 353240830694 | - |
| e23a5a8e | 572044293821 | 353505094336 | - |
| 667dcfb4 | 571984589501 | 353110215399 | - |
| 4014416a | 572029141744 | - | - |
| 3b11c127 | 572046072568 | 353240831674 | - |
| 47076843 | 571991873251 | 353244411611 | - |
| 25db2cbf | 571975209677 | - | 341944399583 |
| e72e9f53 | 571991873252 | - | 341525733091 |
| b7bd9f1f | 572044295907 | 353339485886 | - |
| 2725a262 | 571984592582 | 353510987465 | - |
| 9858aa6d | 571984592588 | 353514608353 | - |
| 4298e365 | 572022657749 | - | 341538298597 |
| 14dd2694 | 571984592592 | - | 341660126942 |

Totals: 14 contacts, 9 companies, 4 tickets (contact last name, company name and ticket subject carry
`[TriageGate test]`). Before the scopes were added, one real create attempt returned 403 and created nothing.

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
