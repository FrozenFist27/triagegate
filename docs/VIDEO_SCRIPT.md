# Demo video script (<= 2 min) - TriageGate (demo project, synthetic data)

Numbers shown on screen must be read from `eval/results/summary.json` (2026-10-17 run).

| Time | Screen | Narration |
|---|---|---|
| 0:00-0:12 | Cluttered inbox mock (`site/demo.html`) | "AI that writes to your CRM without checks is a liability. This is TriageGate, a demo project on synthetic data." |
| 0:12-0:30 | Architecture diagram from the README | "n8n orchestrates. A small Python service validates every model answer, owns the approval state, and logs everything." |
| 0:30-0:55 | POST a lead to the ingest webhook; n8n execution view; Slack card appears | "A lead arrives. The model's JSON is schema-checked, then a Slack card shows company, contact, request and urgency." |
| 0:55-1:10 | Click Approve; show the HubSpot record; click Approve again / replay `simulate_click.py` | "One click writes one record. Click again, or two people at once, and the second is logged and ignored. Zero duplicates in five trials." |
| 1:10-1:22 | Reject a support card; show the run-log row; show an expired card | "Reject writes nothing. Stale approvals expire on a schedule and clicking them does nothing." |
| 1:22-1:37 | Run with `LLM_FAULT_INJECTION=first_attempt_invalid`; run log shows attempt 1 error then attempt 2 | "Poisoned model output is caught and retried, and if it never validates it is quarantined. Zero invalid outputs reached the CRM." |
| 1:37-1:52 | `summary.json` table | "Forty labeled emails: 100 percent on this synthetic set, 1.4 seconds median, about a hundredth of a cent per email." |
| 1:52-2:00 | Repo + Upwork profile | "Demo project. Code on GitHub, available for n8n automation work on Upwork." |

Recording notes: never show `.env`, tokens or the Slack signing secret. Say plainly that HubSpot writes in the
recorded run went to the local stand-in unless the service key's write scopes have since been added.
