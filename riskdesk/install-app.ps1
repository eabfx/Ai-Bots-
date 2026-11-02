# One-time setup: puts a RiskDesk icon on the Desktop and in the Start menu, and starts the server quietly when you sign in.
# Run:  powershell -ExecutionPolicy Bypass -File riskdesk\install-app.ps1      (remove with -Remove)
param([switch]$Remove)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sh = New-Object -ComObject WScript.Shell
$targets = @(
  @{ Path = Join-Path ([Environment]::GetFolderPath("Desktop")) "RiskDesk.lnk"; Args = "" },
  @{ Path = Join-Path ([Environment]::GetFolderPath("Programs")) "RiskDesk.lnk"; Args = "" },
  @{ Path = Join-Path ([Environment]::GetFolderPath("Startup")) "RiskDesk server.lnk"; Args = "-ServerOnly" }
)
foreach ($t in $targets) {
  if ($Remove) { Remove-Item $t.Path -ErrorAction SilentlyContinue; continue }
  $l = $sh.CreateShortcut($t.Path)
  $l.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
  $l.Arguments = ('"' + (Join-Path $here "RiskDeskApp.vbs") + '" ' + $t.Args).Trim()
  $l.WorkingDirectory = $here
  $l.IconLocation = (Join-Path $here "assets\riskdesk.ico")
  $l.Description = "RiskDesk.ai"
  $l.Save()
}
if ($Remove) { "Removed the RiskDesk shortcuts." } else { "Done. Open RiskDesk from the Desktop icon or the Start menu. The server also starts quietly when you sign in." }
