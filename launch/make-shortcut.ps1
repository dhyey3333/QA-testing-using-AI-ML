# Puts a "Nightshift" shortcut on the desktop that runs "Start Nightshift.bat".
#   powershell -ExecutionPolicy Bypass -File launch\make-shortcut.ps1
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "Nightshift.lnk"))
$shortcut.TargetPath = Join-Path $PSScriptRoot "Start Nightshift.bat"
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,184"
$shortcut.Description = "Start Nightshift (close its window to stop it)"
$shortcut.Save()
Write-Host "Shortcut created: $(Join-Path $desktop 'Nightshift.lnk')"
