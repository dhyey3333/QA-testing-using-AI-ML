# Puts a "Nightshift QA" shortcut on the desktop, with the logo as its icon. It starts the app with no
# window (start.ps1 -Background) and opens it in the browser; while it runs, the same icon asks Open / Stop.
#   powershell -ExecutionPolicy Bypass -File launch\make-shortcut.ps1
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "Nightshift QA.lnk"))
# conhost --headless runs PowerShell with no console window at all (a plain "-WindowStyle Hidden" still
# flashes one, and opens a Windows Terminal tab where that is the default console).
$shortcut.TargetPath = "$env:SystemRoot\System32\conhost.exe"
$shortcut.Arguments = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'start.ps1')`" -Background"
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $PSScriptRoot "nightshift.ico"
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" } else { $shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,184" }
$shortcut.Description = "Start or stop Nightshift QA"
$shortcut.Save()
# The shortcut this script made before the rename, so there aren't two icons doing the same thing.
$old = Join-Path $desktop "Nightshift.lnk"
if (Test-Path $old) { Remove-Item $old }
Write-Host "Shortcut created: $(Join-Path $desktop 'Nightshift QA.lnk')"
