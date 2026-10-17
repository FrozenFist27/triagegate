#!/bin/bash
# Import the committed workflows into the running n8n container, activate them, restart n8n.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
docker compose exec -T n8n n8n import:workflow --separate --input=/workflows
docker compose exec -T n8n n8n update:workflow --all --active=true
docker compose restart n8n
timeout 90 bash -c 'until curl -s localhost:5678/healthz | grep -q ok; do sleep 2; done'
sleep 6
echo "n8n ready"
