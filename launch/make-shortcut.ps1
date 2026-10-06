# Puts a "Nightshift QA" shortcut on the desktop that runs "Start Nightshift.bat", with the logo as its icon.
#   powershell -ExecutionPolicy Bypass -File launch\make-shortcut.ps1
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "Nightshift QA.lnk"))
$shortcut.TargetPath = Join-Path $PSScriptRoot "Start Nightshift.bat"
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $PSScriptRoot "nightshift.ico"
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" } else { $shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,184" }
$shortcut.Description = "Start Nightshift QA (close its window to stop it)"
$shortcut.Save()
# The shortcut this script made before the rename, so there aren't two icons doing the same thing.
$old = Join-Path $desktop "Nightshift.lnk"
if (Test-Path $old) { Remove-Item $old }
Write-Host "Shortcut created: $(Join-Path $desktop 'Nightshift QA.lnk')"
