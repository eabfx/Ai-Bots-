' Runs the launcher with no flashing console window.
Set sh = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
args = ""
For Each a In WScript.Arguments: args = args & " " & a: Next
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "RiskDeskApp.ps1""" & args, 0, False
