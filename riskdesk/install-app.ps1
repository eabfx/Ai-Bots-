# One-time setup: puts a Kelly Terminal icon on the Desktop and in the Start menu, and starts the server quietly when you sign in.
# Run:  powershell -ExecutionPolicy Bypass -File riskdesk\install-app.ps1      (remove with -Remove)
param([switch]$Remove)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sh = New-Object -ComObject WScript.Shell
$targets = @(
  @{ Path = Join-Path ([Environment]::GetFolderPath("Desktop")) "Kelly Terminal.lnk"; Args = "" },
  @{ Path = Join-Path ([Environment]::GetFolderPath("Programs")) "Kelly Terminal.lnk"; Args = "" },
  @{ Path = Join-Path ([Environment]::GetFolderPath("Startup")) "Kelly Terminal server.lnk"; Args = "-ServerOnly" }
)
# shortcuts from when this was called RiskDesk: remove them so there is only one icon
foreach ($old in @((Join-Path ([Environment]::GetFolderPath("Desktop")) "RiskDesk.lnk"), (Join-Path ([Environment]::GetFolderPath("Programs")) "RiskDesk.lnk"), (Join-Path ([Environment]::GetFolderPath("Startup")) "RiskDesk server.lnk"))) {
  Remove-Item $old -ErrorAction SilentlyContinue
}
foreach ($t in $targets) {
  if ($Remove) { Remove-Item $t.Path -ErrorAction SilentlyContinue; continue }
  $l = $sh.CreateShortcut($t.Path)
  $l.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
  $l.Arguments = ('"' + (Join-Path $here "RiskDeskApp.vbs") + '" ' + $t.Args).Trim()
  $l.WorkingDirectory = $here
  $l.IconLocation = (Join-Path $here "assets\riskdesk.ico")
  $l.Description = "Kelly Terminal"
  $l.Save()
}
if ($Remove) { "Removed the Kelly Terminal shortcuts." } else { "Done. Open Kelly Terminal from the Desktop icon or the Start menu. The server also starts quietly when you sign in." }
