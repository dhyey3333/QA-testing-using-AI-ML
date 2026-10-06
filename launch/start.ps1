<#
  Nightshift QA, one click. Checks the model, starts the web app, opens a free public link (a fixed
  one with Tailscale Funnel, else a Cloudflare quick tunnel), and opens the browser.

  Works in Windows PowerShell 5.1 (every Windows 10/11) and PowerShell 7.
    -Background      no window (the desktop icon uses this): messages go to runs\nightshift.log, and
                     starting it again while it runs asks Open / Stop instead
    -Port 8080       where the app listens
    -NoTunnel        no public link: only this computer can open the app
    -NoBrowser       don't open the browser
    -Data <folder>   the app's data (default: hosted-data next to this folder)
  Without -Background it runs in this window: close the window, or press Ctrl+C, to stop it.
#>
param([int]$Port = 8080, [switch]$NoTunnel, [switch]$NoBrowser, [switch]$Background, [string]$Data = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not $Data) { $Data = Join-Path $Root "hosted-data" }
$Runs = Join-Path $Root "runs"
New-Item -ItemType Directory -Force $Runs | Out-Null
$TunnelLog = Join-Path $Runs "tunnel.log"
$PidFile = Join-Path $Runs "tunnel.pid"
$Local = "http://127.0.0.1:$Port/"
$LogFile = Join-Path $Runs "nightshift.log"
$UrlFile = Join-Path $Runs "nightshift.url"  # the address to open, for "Open" while it runs

# With no window, messages go to the log, and anything the person must see is a small dialog.
function Say([string]$Text, [string]$Color = "Gray") {
    if ($Background) { Add-Content $LogFile $Text } else { Write-Host $Text -ForegroundColor $Color }
}
function Show-Message([string]$Text) {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show($Text, "Nightshift QA", "OK", "Warning")
}
function Stop-Here([string]$Text) {
    Say $Text "Red"
    if ($Background) { Show-Message $Text } else { Read-Host "Press Enter to close" }
    exit 1
}
# "Nightshift QA is running at <link>": Open, Stop or Cancel. Returns "open", "stop" or "".
function Show-Running([string]$Url) {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "Nightshift QA"; $form.StartPosition = "CenterScreen"; $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.TopMost = $true
    $form.Font = New-Object System.Drawing.Font("Segoe UI", 10)
    $form.ClientSize = New-Object System.Drawing.Size(392, 132)
    $icon = Join-Path $PSScriptRoot "nightshift.ico"
    if (Test-Path $icon) { $form.Icon = New-Object System.Drawing.Icon($icon) }
    $label = New-Object System.Windows.Forms.Label
    $label.Text = "Nightshift QA is running at`n$Url"
    $label.Location = New-Object System.Drawing.Point(18, 16); $label.Size = New-Object System.Drawing.Size(360, 48)
    $form.Controls.Add($label)
    $buttons = @(("Open", [System.Windows.Forms.DialogResult]::Yes), ("Stop", [System.Windows.Forms.DialogResult]::No),
                 ("Cancel", [System.Windows.Forms.DialogResult]::Cancel))
    $x = 18
    foreach ($b in $buttons) {
        $button = New-Object System.Windows.Forms.Button
        $button.Text = $b[0]; $button.DialogResult = $b[1]
        $button.Location = New-Object System.Drawing.Point($x, 80); $button.Size = New-Object System.Drawing.Size(112, 34)
        $form.Controls.Add($button); $x += 124
        if ($b[0] -eq "Open") { $form.AcceptButton = $button }
        if ($b[0] -eq "Cancel") { $form.CancelButton = $button }
    }
    switch ($form.ShowDialog()) { "Yes" { "open" } "No" { "stop" } default { "" } }
}

if ($Background) {
    "--- Nightshift QA started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Set-Content $LogFile
} else {
    $Host.UI.RawUI.WindowTitle = "Nightshift QA (close this window to stop it)"
}
Say "Nightshift QA" "Cyan"

# 1. uv runs Nightshift.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Stop-Here "uv isn't installed. Install it from https://docs.astral.sh/uv/ and try again."
}

# 2. Already running? Open it, or (from the icon) offer Open / Stop. Stopping ends the server; the
#    copy of this script that started it then switches the public link off and exits.
$running = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($running) {
    $url = if (Test-Path $UrlFile) { (Get-Content $UrlFile -Raw).Trim() } else { $Local }
    if ($Background) {
        $choice = Show-Running $url
        if ($choice -eq "open") { Start-Process $url }
        if ($choice -eq "stop") { $running | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }
        exit 0
    }
    Say "Nightshift QA is already running: opening $url" "Yellow"
    if (-not $NoBrowser) { Start-Process $url }
    Start-Sleep 2
    exit 0
}

# 3. The model. The free cloud model through the Ollama app, unless MODEL_NAME says otherwise.
if (-not $env:MODEL_NAME) { $env:MODEL_NAME = "gemma4:31b-cloud" }
if (-not $env:MODEL_TIMEOUT) { $env:MODEL_TIMEOUT = "120" }
$env:PYTHONIOENCODING = "utf-8"
function Test-Ollama {
    try { Invoke-WebRequest "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true }
    catch { return $false }
}
if ($env:MODEL_BASE_URL) {
    Say "Model: $env:MODEL_NAME at $env:MODEL_BASE_URL" "Green"
} else {
    if (-not (Test-Ollama)) {
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
        if ($ollama) {
            Say "Starting Ollama..."
            Start-Process $ollama.Source -ArgumentList "serve" -WindowStyle Hidden
            for ($i = 0; $i -lt 20 -and -not (Test-Ollama); $i++) { Start-Sleep 1 }
        }
    }
    if (Test-Ollama) { Say "Model: $env:MODEL_NAME (through Ollama)" "Green" }
    else { Say "Ollama isn't running. Open the Ollama app for AI runs; saved replays work without it." "Yellow" }
}

# 4. The first time: create the admin login, here in this window.
$users = & uv run --quiet nightshift hosted users --data "$Data"
if (-not $users) {
    if ($Background) {
        # Creating the first login needs typing, so this one time it opens in a window.
        Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Port", "$Port"
        exit 0
    }
    Say "`nFirst start: create your admin login." "Cyan"
    $email = Read-Host "Your email"
    & uv run --quiet nightshift hosted add-user --data "$Data" --email $email --admin
    if ($LASTEXITCODE -ne 0) { Stop-Here "The login wasn't created. Run this again to retry." }
}

# 5. A public link. Tailscale Funnel gives a fixed https address that never changes (a free account,
#    no card), so it can go on the website. Without it, a Cloudflare quick tunnel gives a new random
#    address on every start. A tunnel left over from a window closed last time is stopped first.
if (Test-Path $PidFile) {
    $old = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($old) { Stop-Process -Id ([int]$old) -ErrorAction SilentlyContinue }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}
$PublicUrl = ""
$Tunnel = $null
$Funnel = $null
$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale -and (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe")) { $tailscale = Get-Command "$env:ProgramFiles\Tailscale\tailscale.exe" }
if ($tailscale -and -not $NoTunnel) {
    # Windows PowerShell turns a native program's error output into a stopping error; read it softly.
    $ErrorActionPreference = "Continue"
    $status = $null
    try { $status = (& $tailscale.Source status --json 2>$null | Out-String) | ConvertFrom-Json } catch { }
    if ($status -and $status.BackendState -eq "Running" -and $status.Self.DNSName) {
        Say "Opening your fixed link with Tailscale Funnel (the first time, it may ask you to allow Funnel in the browser)..."
        if ($Background) {
            # With no window nobody could follow an "allow Funnel" link, so give it 25 s and move on.
            $job = Start-Job { param($ts, $port) & $ts funnel --bg --https=443 "http://127.0.0.1:$port" 2>&1 | Out-String; $LASTEXITCODE } `
                -ArgumentList $tailscale.Source, $Port
            $ok = $false
            if (Wait-Job $job -Timeout 25) { $out = @(Receive-Job $job); Say ($out[0]); $ok = ($out[-1] -eq 0) } else { Stop-Job $job }
            Remove-Job $job -Force
            $global:LASTEXITCODE = if ($ok) { 0 } else { 1 }
        } else {
            & $tailscale.Source funnel --bg --https=443 "http://127.0.0.1:$Port"
        }
        if ($LASTEXITCODE -eq 0) {
            $PublicUrl = "https://" + $status.Self.DNSName.TrimEnd(".")
            $Funnel = $tailscale
            Set-Clipboard -Value $PublicUrl
            Say "Your fixed link (copied): $PublicUrl" "Green"
        } else {
            Say "Tailscale Funnel didn't start; using a Cloudflare link instead." "Yellow"
        }
    } else {
        Say "Tailscale is installed but not signed in. Open the Tailscale app and sign in for your fixed link." "Yellow"
    }
    $ErrorActionPreference = "Stop"
}
$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    $candidates = @("$env:ProgramFiles\cloudflared\cloudflared.exe", "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
                    "$env:LOCALAPPDATA\Microsoft\WinGet\Links\cloudflared.exe")
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) { $cloudflared = Get-Command $found }
}
if ($PublicUrl) {
    # Tailscale Funnel already gave the fixed link.
} elseif ($NoTunnel) {
    Say "No public link (-NoTunnel): only this computer can open the app."
} elseif (-not $cloudflared) {
    Say "No public link: install Tailscale (a fixed link) from https://tailscale.com/download, or cloudflared with  winget install Cloudflare.cloudflared" "Yellow"
} else {
    Remove-Item $TunnelLog -ErrorAction SilentlyContinue
    $Tunnel = Start-Process $cloudflared.Source -ArgumentList "tunnel", "--no-autoupdate", "--url", $Local.TrimEnd("/") `
        -RedirectStandardError $TunnelLog -NoNewWindow -PassThru
    $Tunnel.Id | Set-Content $PidFile
    Say "Opening a public link..."
    for ($i = 0; $i -lt 40 -and -not $PublicUrl; $i++) {
        Start-Sleep 1
        if (Test-Path $TunnelLog) {
            $match = Select-String -Path $TunnelLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($match) { $PublicUrl = $match.Matches[0].Value }
        }
    }
    if ($PublicUrl) {
        Set-Clipboard -Value $PublicUrl
        Say "Public link (copied, paste it to anyone): $PublicUrl" "Green"
    } else {
        Say "The tunnel gave no link within 40 s; only this computer can open the app." "Yellow"
    }
}

# 6. Open the browser once the app answers, and run the app here until the window closes. With the
#    fixed link, the browser opens that, the same address clients use, rather than 127.0.0.1.
$Open = if ($Funnel) { $PublicUrl } else { $Local }
if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($check, $open)
        for ($i = 0; $i -lt 90; $i++) {
            try { Invoke-WebRequest $check -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process $open; break }
            catch { Start-Sleep 1 }
        }
    } -ArgumentList $Local, $Open | Out-Null
}
if ($Background) { Say "Nightshift QA: $Open" } else { Say "`nNightshift QA: $Open   Close this window (or press Ctrl+C) to stop it.`n" "Cyan" }
$Open | Set-Content $UrlFile
$serve = @("run", "--quiet", "nightshift", "hosted", "serve", "--data", "$Data", "--port", "$Port")
if ($PublicUrl) { $serve += @("--public-url", $PublicUrl) }
try {
    if ($Background) {
        $ErrorActionPreference = "Continue"  # the server's own messages go to the log, not to a stopping error
        & uv @serve *>> $LogFile
    } else {
        & uv @serve
    }
} finally {
    Remove-Item $UrlFile -ErrorAction SilentlyContinue
    if ($Tunnel) {
        Stop-Process -Id $Tunnel.Id -ErrorAction SilentlyContinue
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }
    if ($Funnel) {
        # The fixed link stays reserved for you; it just stops pointing at a closed app.
        $ErrorActionPreference = "Continue"
        & $Funnel.Source funnel reset 2>$null | Out-Null
    }
}
