# Keeps the Kelly Terminal server running. If it ever stops or crashes, it is started again a few seconds later, and each
# restart is noted in trader\server-restarts.log. Started hidden by RiskDeskApp.ps1; only one copy ever runs.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$made = $false
$lock = New-Object System.Threading.Mutex($true, "Local\KellyTerminalKeepAlive", [ref]$made)
if (-not $made) { exit }
$py = (Get-Command py.exe).Source
if (-not $py) { $py = (Get-Command python.exe).Source }
$logDir = Join-Path $root "trader"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "server-restarts.log"
# the watcher sends a phone alert if the server or kellyterminal.com stops answering (see watch.ps1)
Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ('"' + (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "watch.ps1") + '"') -WorkingDirectory $root -WindowStyle Hidden
while ($true) {
  $p = Start-Process -FilePath $py -ArgumentList "riskdesk\dashboard.py", "--no-browser" -WorkingDirectory $root -WindowStyle Hidden -PassThru
  $p.WaitForExit()
  Add-Content -Path $log -Value ("{0}  server stopped (exit {1}), starting it again" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $p.ExitCode)
  Start-Sleep -Seconds 3
}
