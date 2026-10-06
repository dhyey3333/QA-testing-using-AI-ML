@echo off
rem Double-click to start Nightshift: the model check, the web app, a public link, the browser.
rem Close this window to stop it. Options go to start.ps1, e.g.  "Start Nightshift.bat" -NoTunnel
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
