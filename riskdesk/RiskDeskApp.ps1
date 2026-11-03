# Opens Kelly Terminal like an app: starts the server quietly if it is not running, then shows it in its own window.
# -ServerOnly starts just the server (used at sign-in so prices and the hourly AI read keep running).
param([switch]$ServerOnly)
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$url = "http://127.0.0.1:8765/"
function Up { try { (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false } }
if (-not (Up)) {
  $py = (Get-Command py.exe).Source
  if (-not $py) { $py = (Get-Command python.exe).Source }
  Start-Process -FilePath $py -ArgumentList "riskdesk\dashboard.py", "--no-browser" -WorkingDirectory $root -WindowStyle Hidden
  for ($i = 0; $i -lt 40 -and -not (Up); $i++) { Start-Sleep -Milliseconds 750 }
}
if ($ServerOnly) { exit }
# Always opens the copy running on this PC, so what you see is the version that was just updated. (A Chrome-installed app can be
# pointed at kellyterminal.com instead, and shows whatever the server has.)
$browser = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($browser) {
  Start-Process -FilePath $browser -ArgumentList "--app=$url", "--window-size=1500,950"
  # own taskbar identity and the Kelly icon, so the button and a pinned icon are not Chrome's
  . (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "kellyid.ps1")
  $here = Split-Path -Parent $MyInvocation.MyCommand.Path
  $launch = '"' + (Join-Path $env:WINDIR "System32\wscript.exe") + '" "' + (Join-Path $here "RiskDeskApp.vbs") + '"'
  Set-KellyWindowId -Command $launch -Icon ((Join-Path $here "assets\riskdesk.ico") + ",0") | Out-Null
} else { Start-Process $url }
