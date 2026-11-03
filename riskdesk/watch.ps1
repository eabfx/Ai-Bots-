# Watches Kelly Terminal and sends a phone alert (through ntfy, the same private topic as Phone alerts) when something stops.
#  - the server on this PC stops answering (about 8 minutes in a row), and again when it is back
#  - kellyterminal.com stops answering (about 6 minutes in a row), and again when it is back
#  - the PC has just started up (so you know after a crash or a reboot)
# It does nothing until Phone alerts is switched on (that creates trader\push_topic.txt). Started hidden by keepalive.ps1; one copy only.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$made = $false
$lock = New-Object System.Threading.Mutex($true, "Local\KellyTerminalWatch", [ref]$made)
if (-not $made) { exit }
$topicFile = Join-Path $root "trader\push_topic.txt"
$log = Join-Path $root "trader\server-restarts.log"
function Say($title, $text, $tags) {
  Add-Content -Path $log -Value ("{0}  alert: {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $title)
  $topic = if (Test-Path $topicFile) { (Get-Content $topicFile -Raw).Trim() } else { "" }
  if (-not $topic) { return }
  try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri ("https://ntfy.sh/" + $topic) -Body $text -Headers @{ Title = $title; Tags = $tags; Priority = "high" } -TimeoutSec 15 | Out-Null } catch {}
}
function Up($url) { try { (Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 20).StatusCode -eq 200 } catch { $false } }
$localDown = 0; $hostedDown = 0; $localSaid = $false; $hostedSaid = $false
$uptime = (Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
if ($uptime.TotalMinutes -lt 15) { Say "PC started" "The PC has just started. Kelly Terminal is starting with it." "computer" }
while ($true) {
  Start-Sleep -Seconds 120
  if (Up "http://127.0.0.1:8765/version.json") {
    if ($localSaid) { Say "Kelly Terminal is back" "The server on your PC is answering again." "white_check_mark"; $localSaid = $false }
    $localDown = 0
  } else {
    $localDown++
    if ($localDown -ge 4 -and -not $localSaid) { Say "Kelly Terminal is down" "The server on your PC has not answered for about 8 minutes." "rotating_light"; $localSaid = $true }
  }
  if (Up "https://kellyterminal.com/version.json") {
    if ($hostedSaid) { Say "kellyterminal.com is back" "The hosted site is answering again." "white_check_mark"; $hostedSaid = $false }
    $hostedDown = 0
  } else {
    $hostedDown++
    if ($hostedDown -ge 3 -and -not $hostedSaid) { Say "kellyterminal.com is down" "The hosted site has not answered for about 6 minutes. Phones and tablets cannot reach it." "rotating_light"; $hostedSaid = $true }
  }
}
