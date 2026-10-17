# Verify the committed workflow JSON imports cleanly into a FRESH n8n container.
# Run from the project root:  powershell -File scripts/verify_import.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$container = "triagegate-import-check"
docker rm -f $container 2>$null | Out-Null

Write-Host "Starting a throwaway n8n container..."
docker run -d --name $container `
  -v "${root}/n8n/workflows:/workflows:ro" `
  -e N8N_ENCRYPTION_KEY=import-check-key `
  -e N8N_DIAGNOSTICS_ENABLED=false `
  -p 5679:5678 `
  n8nio/n8n:1.64.0 | Out-Null

Start-Sleep -Seconds 25

Write-Host "Importing workflows..."
docker exec $container n8n import:workflow --separate --input=/workflows
$code = $LASTEXITCODE

$count = docker exec $container n8n list:workflow
Write-Host $count

docker rm -f $container | Out-Null

if ($code -ne 0) {
  Write-Error "Import FAILED (exit $code)"
  exit 1
}
Write-Host "Import OK - all workflow files loaded cleanly."
exit 0
