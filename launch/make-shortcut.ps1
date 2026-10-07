# Sets up Nightshift QA like an installed app:
#   - a "Nightshift QA" desktop icon that opens the app in its own window (start.ps1 -Background), and
#   - a Windows startup entry that runs the app with no window whenever Windows starts (start.ps1 -Server),
#     so the icon opens it quickly and nightly runs and the public link keep working. Turn that off in
#     Task Manager > Startup apps, or run this with -NoStartup.
#   powershell -ExecutionPolicy Bypass -File launch\make-shortcut.ps1 [-NoStartup]
# (A small compiled launcher was tried for speed; antivirus flags new unsigned programs that start
# PowerShell, so the icon stays a plain shortcut.)
param([switch]$NoStartup)
$shell = New-Object -ComObject WScript.Shell
$start = Join-Path $PSScriptRoot "start.ps1"
$icon = Join-Path $PSScriptRoot "nightshift.ico"
$iconLocation = if (Test-Path $icon) { "$icon,0" } else { "$env:SystemRoot\System32\imageres.dll,184" }

# conhost --headless runs PowerShell with no console window at all (a plain "-WindowStyle Hidden" still
# flashes one, and opens a Windows Terminal tab where that is the default console).
function New-Shortcut([string]$Path, [string]$Mode, [string]$Description) {
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = "$env:SystemRoot\System32\conhost.exe"
    $link.Arguments = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$start`" $Mode"
    $link.WorkingDirectory = Split-Path -Parent $PSScriptRoot
    $link.IconLocation = $iconLocation
    $link.Description = $Description
    $link.Save()
    Write-Host "Created: $Path"
}

$desktop = [Environment]::GetFolderPath("Desktop")
New-Shortcut (Join-Path $desktop "Nightshift QA.lnk") "-Background" "Open Nightshift QA"
# The shortcut an earlier version made, so there aren't two icons doing the same thing.
$old = Join-Path $desktop "Nightshift.lnk"
if (Test-Path $old) { Remove-Item $old }

$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "Nightshift QA.lnk"
if ($NoStartup) {
    if (Test-Path $startup) { Remove-Item $startup; Write-Host "Removed from Windows startup." }
} else {
    New-Shortcut $startup "-Server" "Runs Nightshift QA in the background when Windows starts"
}
