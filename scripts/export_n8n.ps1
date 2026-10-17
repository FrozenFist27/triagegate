# Export the workflows from the running n8n container and sanitize them.
# Run from the project root:  powershell -File scripts/export_n8n.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$stage = Join-Path $env:TEMP "n8n-export"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

docker compose exec -T n8n n8n export:workflow --all --separate --output=/workflows
# compose mounts ./n8n/workflows at /workflows, so the export lands in the repo dir
Write-Host "Exported to n8n/workflows - now sanitizing..."
python scripts/sanitize_workflows.py n8n/workflows
Write-Host "Done. Review the diff before committing."
