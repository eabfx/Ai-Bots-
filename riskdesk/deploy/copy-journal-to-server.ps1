# Copies your journal (trades and screenshots) from this PC to the server. Run it in PowerShell on your PC:
#     .\riskdesk\deploy\copy-journal-to-server.ps1 -Server 95.1.2.3
# It asks for the server password (or uses your ssh key) itself: nothing is stored. It replaces the journal on the
# server, so use it for the first copy, not every day. The server keeps a nightly backup of what is there.
param([Parameter(Mandatory = $true)][string]$Server, [string]$User = "root")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$journal = Join-Path $root "trader\journal"
if (-not (Test-Path (Join-Path $journal "trades.json"))) { throw "No journal found at $journal" }
$tgz = Join-Path $env:TEMP "riskdesk-journal.tgz"
tar -czf $tgz -C (Join-Path $root "trader") journal
if ($LASTEXITCODE -ne 0) { throw "Could not pack the journal" }
$mb = [math]::Round((Get-Item $tgz).Length / 1MB, 1)
Write-Host "Packed your journal: $mb MB"
scp $tgz "${User}@${Server}:/tmp/riskdesk-journal.tgz"
if ($LASTEXITCODE -ne 0) { throw "The copy to the server failed" }
ssh "${User}@${Server}" "systemctl stop riskdesk; rm -rf /opt/riskdesk/trader/journal.old; mv /opt/riskdesk/trader/journal /opt/riskdesk/trader/journal.old 2>/dev/null; tar -xzf /tmp/riskdesk-journal.tgz -C /opt/riskdesk/trader && chown -R riskdesk:riskdesk /opt/riskdesk/trader/journal && rm /tmp/riskdesk-journal.tgz && systemctl start riskdesk && ls /opt/riskdesk/trader/journal/images | wc -l"
Remove-Item $tgz
Write-Host "Done. The number above is how many screenshots arrived."
