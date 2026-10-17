# TriageGate eval dataset

**40 synthetic, hand-written emails** with gold labels. Released under CC0-1.0.
Every company, person, domain and invoice number here is invented. Domains use
RFC 2606 reserved TLDs (`.example.com`, `.example.org`, `.example.io`, ...) and
`.example.net` / `.example.co` style look-alikes that do not resolve. Nothing was
copied from a real inbox, and no client data is present.

## Record shape

```json
{
  "id": "eval-001",
  "from_email": "mark.ilves@northwind-logistics.example.org",
  "from_name": "Mark Ilves",
  "subject": "Demo request for route planning",
  "body_text": "...",
  "label": {
    "category": "sales_lead",
    "company": "Northwind Logistics",
    "contact_email": "mark.ilves@northwind-logistics.example.org",
    "urgency": "medium"
  }
}
```

## Distribution (40)

| Category | Count | IDs |
|---|---|---|
| sales_lead | 14 | eval-001 .. eval-014 |
| support_request | 14 | eval-015 .. eval-028 |
| invoice | 6 | eval-029 .. eval-034 |
| spam | 6 | eval-035 .. eval-040 |

Urgency: 6 high, 16 medium, 18 low.

## Label conventions

- `contact_email` is the From address for genuine human business mail, and `null`
  for spam. It is never a fabricated address.
- `company` is `null` when the sender uses a free-mail domain and states no
  company. Otherwise it is the organisation name as written in the signature.
- Extraction is scored with a case- and punctuation-insensitive comparison for
  `company` (`normalize_text` in `../metrics.py`) and exact match for
  `contact_email` / `urgency`.
- `urgency_reason` and `request_summary` are not scored.

## Deliberate hard cases

| ID | Why it is hard | Expected behaviour |
|---|---|---|
| eval-004 | Spanish-language lead | still `sales_lead` |
| eval-005 | lead from a free-mail address | `company: null` |
| eval-009 | very short email | `sales_lead`, low confidence acceptable |
| eval-011 | RFP with a 24-hour deadline | `urgency: high` |
| eval-016 | support request that mentions pricing | `support_request`, not `invoice` |
| eval-019 | four-word outage report | `support_request`, `urgency: high` |
| eval-023 | customer on personal e-mail | `company: null` |
| eval-025 | signature carries only the company name | `contact_name: null` |
| eval-030 | invoice phrased as a question | `invoice`, not `support_request` |
| eval-036 | phishing that mentions an invoice | `spam`, not `invoice` |
| eval-038 | prompt injection ("classify as sales_lead") | `spam`; instructions ignored |
| eval-040 | prize-draw spam with urgency theatre | `spam`, `urgency: low` |

## Why these numbers

IDEATION originally sketched 24 emails (7/7/5/5). The build brief raised the
eval set to **40**, so the same proportions were scaled to 14/14/6/6 while
keeping every hard case.
