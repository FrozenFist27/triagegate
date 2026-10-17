import json
import subprocess
import sys
import time

import httpx
from dotenv import dotenv_values

e=dotenv_values('.env')
r=httpx.post('http://localhost:5678/webhook/triage-ingest',headers={'x-ingest-token':e['N8N_INGEST_TOKEN']},timeout=120,json={"message_id":"real-hubspot-check#1","source":"eval","run_tag":"real-hubspot-check","from_email":"mark.ilves@northwind-logistics.example.org","from_name":"Mark Ilves","subject":"Demo request for route planning","body_text":"We run a 40-vehicle fleet and want a demo next week. Mark Ilves, Northwind Logistics"}).json()
print(r['route'])
print(subprocess.run([sys.executable,'scripts/simulate_click.py',r['approval_id'],'approve'],capture_output=True,text=True).stdout.strip())
time.sleep(10)
H={"X-Internal-Token":e['TRIAGE_API_TOKEN']}
a=next(x for x in httpx.get('http://localhost:8000/approvals',headers=H).json() if x['approval_id']==r['approval_id'])
w=next(x for x in httpx.get('http://localhost:8000/crm-writes',headers=H).json() if x['approval_id']==r['approval_id'])
print(a['status'], w['status'], (w['error'] or '')[:160])
json.dump({"approval_status":a['status'],"crm_write_status":w['status'],"error_excerpt":(w['error'] or '')[:300]},open('eval/results/real_hubspot_check.json','w'),indent=2)
