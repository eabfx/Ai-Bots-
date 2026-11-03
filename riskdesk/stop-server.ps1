# Stops Kelly Terminal's server, the keeper that restarts it and the watcher that sends phone alerts. Run this when you really want it off.
$ErrorActionPreference = "SilentlyContinue"
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match "keepalive.ps1|watch.ps1" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -in "python.exe", "py.exe" -and $_.CommandLine -like "*dashboard.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
"Kelly Terminal server stopped."
