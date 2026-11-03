# Stops Kelly Terminal's server and the keeper that restarts it. Run this when you really want it off.
$ErrorActionPreference = "SilentlyContinue"
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like "*keepalive.ps1*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -in "python.exe", "py.exe" -and $_.CommandLine -like "*dashboard.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
"Kelly Terminal server stopped."
