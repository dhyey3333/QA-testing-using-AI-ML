<#
  Nightshift QA on Windows. Three ways in:
    (no switch)   in this window: starts the app and its public link, opens the app window, and runs
                  until the window closes. "Start Nightshift.bat" does this.
    -Background   the desktop icon: if the app is running, opens its window at once; if not, starts it
                  with no window (-Server) and then opens the window. Never asks anything.
    -Server       Windows startup: runs the app and its public link with no window, all the time.
                  Messages go to runs\nightshift.log.
  The app window is Edge (or Chrome) in app mode with its own profile: no tabs, no address bar, its own
  taskbar icon, and its own login. Closing it leaves the app running.

  Works in Windows PowerShell 5.1 (every Windows 10/11) and PowerShell 7.
    -Port 8080       where the app listens
    -NoTunnel        no public link: only this computer can open the app
    -NoBrowser       don't open the app window
    -Data <folder>   the app's data (default: hosted-data next to this folder)
#>
param([int]$Port = 8080, [switch]$NoTunnel, [switch]$NoBrowser, [switch]$Background, [switch]$Server, [string]$Data = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not $Data) { $Data = Join-Path $Root "hosted-data" }
$Runs = Join-Path $Root "runs"
New-Item -ItemType Directory -Force $Runs | Out-Null
$TunnelLog = Join-Path $Runs "tunnel.log"
$PidFile = Join-Path $Runs "tunnel.pid"
$LogFile = Join-Path $Runs "nightshift.log"
$Local = "http://127.0.0.1:$Port/"
$Hidden = $Background -or $Server

# With no window, messages go to the log, and anything the person must see is a small dialog.
function Say([string]$Text, [string]$Color = "Gray") {
    if ($Hidden) { Add-Content $LogFile $Text -Encoding UTF8 } else { Write-Host $Text -ForegroundColor $Color }
}
function Show-Message([string]$Text) {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show($Text, "Nightshift QA", "OK", "Warning")
}
function Stop-Here([string]$Text) {
    Say $Text "Red"
    if ($Hidden) { Show-Message $Text } else { Read-Host "Press Enter to close" }
    exit 1
}
# Is the app answering? A plain TCP connect: much quicker than a web request or Get-NetTCPConnection.
function Test-Up {
    $client = New-Object System.Net.Sockets.TcpClient
    try { return $client.ConnectAsync("127.0.0.1", $Port).Wait(400) } catch { return $false } finally { $client.Close() }
}
# The app window is Edge (or Chrome) in app mode, with a profile of its own.
$AppProfile = Join-Path $env:LOCALAPPDATA "NightshiftQA\window"
function Get-Browser {
    @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
      "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
# The app in its own window. If that window is already open, bring it to the front instead.
function Open-AppWindow {
    # Found by its title: an app-mode window is titled exactly as the page, while a browser tab's
    # window adds " - Microsoft Edge". (Searching process command lines instead takes seconds.)
    $open = Get-Process msedge, chrome -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -eq "Nightshift QA" }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($p in $open) { if ($shell.AppActivate([int]$p.Id)) { return } }
    $browser = Get-Browser
    if ($browser) {
        Start-Process $browser -ArgumentList "--app=$Local", "--user-data-dir=`"$AppProfile`"", "--no-first-run",
                                             "--no-default-browser-check", "--window-size=1320,880"
    } else {
        Start-Process $Local  # no Edge or Chrome: an ordinary browser tab
    }
}

# 1. The desktop icon. Running: open the window, done. Not running: start the app with no window, wait
#    for it, open the window. This is the path people click, so it touches nothing slow.
if ($Background) {
    if (-not (Test-Up)) {
        $serverArgs = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Server -Port $Port"
        if ($PSBoundParameters.ContainsKey("Data")) { $serverArgs += " -Data `"$Data`"" }
        if ($NoTunnel) { $serverArgs += " -NoTunnel" }
        Start-Process "$env:SystemRoot\System32\conhost.exe" -ArgumentList $serverArgs
        $up = $false
        for ($i = 0; $i -lt 120 -and -not $up; $i++) { Start-Sleep -Milliseconds 500; $up = Test-Up }
        if (-not $up) { Stop-Here "Nightshift QA didn't start within a minute. What happened is in $LogFile" }
    }
    if (-not $NoBrowser) { Open-AppWindow }
    exit 0
}

if ($Server) {
    "--- Nightshift QA started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Set-Content $LogFile -Encoding UTF8
} else {
    $Host.UI.RawUI.WindowTitle = "Nightshift QA (close this window to stop it)"
}
Say "Nightshift QA" "Cyan"

# 2. Already running (Windows started it, or another window did)? Then just open it.
if (Test-Up) {
    if ($Server) { Say "Already running."; exit 0 }
    Say "Nightshift QA is already running." "Yellow"
    if (-not $NoBrowser) { Open-AppWindow }
    Start-Sleep 2
    exit 0
}

# 3. How to run Nightshift: the project's own environment directly when it exists (quicker than uv
#    checking the environment first on every start), otherwise through uv.
$exe = Join-Path $Root ".venv\Scripts\nightshift.exe"
if (Test-Path $exe) {
    $NsCommand = $exe; $NsPrefix = @()
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    $NsCommand = "uv"; $NsPrefix = @("run", "--quiet", "nightshift")
} else {
    Stop-Here "uv isn't installed. Install it from https://docs.astral.sh/uv/ and try again."
}

# 4. The model: the free cloud model through the Ollama app, unless MODEL_NAME says otherwise. The app
#    doesn't need the model to start, so a stopped Ollama is started without waiting for it.
if (-not $env:MODEL_NAME) { $env:MODEL_NAME = "gemma4:31b-cloud" }
if (-not $env:MODEL_TIMEOUT) { $env:MODEL_TIMEOUT = "120" }
$env:PYTHONIOENCODING = "utf-8"
if ($env:MODEL_BASE_URL) {
    Say "Model: $env:MODEL_NAME at $env:MODEL_BASE_URL" "Green"
} else {
    $ollamaUp = $false
    try { Invoke-WebRequest "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ollamaUp = $true } catch { }
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaUp) { Say "Model: $env:MODEL_NAME (through Ollama)" "Green" }
    elseif ($ollama) { Start-Process $ollama.Source -ArgumentList "serve" -WindowStyle Hidden; Say "Starting Ollama for $env:MODEL_NAME." }
    else { Say "Ollama isn't installed. AI runs need it; saved replays work without it." "Yellow" }
}

# 5. The first time: create the admin login. That needs typing, so it always happens in a window.
$users = & $NsCommand @NsPrefix hosted users --data "$Data"
if (-not $users) {
    if ($Server) {
        Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Port", "$Port"
        exit 0
    }
    Say "`nFirst start: create your admin login." "Cyan"
    $email = Read-Host "Your email"
    & $NsCommand @NsPrefix hosted add-user --data "$Data" --email $email --admin
    if ($LASTEXITCODE -ne 0) { Stop-Here "The login wasn't created. Run this again to retry." }
}

# 6. A public link. Tailscale Funnel gives a fixed https address that never changes (a free account,
#    no card), so it can go on the website; its setting survives restarts, so usually there is nothing
#    to do. Without Tailscale, a Cloudflare quick tunnel gives a new random address on every start.
if (Test-Path $PidFile) {
    $old = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($old) { Stop-Process -Id ([int]$old) -ErrorAction SilentlyContinue }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}
$PublicUrl = ""
$Tunnel = $null
$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale -and (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe")) { $tailscale = Get-Command "$env:ProgramFiles\Tailscale\tailscale.exe" }
if ($tailscale -and -not $NoTunnel) {
    # Windows PowerShell turns a native program's error output into a stopping error; read it softly.
    $ErrorActionPreference = "Continue"
    $status = $null
    try { $status = (& $tailscale.Source status --json 2>$null | Out-String) | ConvertFrom-Json } catch { }
    if ($status -and $status.BackendState -eq "Running" -and $status.Self.DNSName) {
        $already = (& $tailscale.Source funnel status --json 2>$null | Out-String) -match [regex]::Escape("127.0.0.1:$Port")
        if ($already) {
            $ok = $true
        } elseif ($Server) {
            # With no window nobody could follow an "allow Funnel" link, so give it 25 s and move on.
            $job = Start-Job { param($ts, $port) & $ts funnel --bg --https=443 "http://127.0.0.1:$port" 2>&1 | Out-String; $LASTEXITCODE } `
                -ArgumentList $tailscale.Source, $Port
            $ok = $false
            if (Wait-Job $job -Timeout 25) { $out = @(Receive-Job $job); Say ($out[0]); $ok = ($out[-1] -eq 0) } else { Stop-Job $job }
            Remove-Job $job -Force
        } else {
            Say "Opening your fixed link with Tailscale Funnel (the first time, it may ask you to allow Funnel in the browser)..."
            & $tailscale.Source funnel --bg --https=443 "http://127.0.0.1:$Port"
            $ok = ($LASTEXITCODE -eq 0)
        }
        if ($ok) {
            $PublicUrl = "https://" + $status.Self.DNSName.TrimEnd(".")
            Say "Your fixed link: $PublicUrl" "Green"
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
    # Tailscale Funnel gave the fixed link.
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
    if ($PublicUrl) { Say "Public link (paste it to anyone): $PublicUrl" "Green" }
    else { Say "The tunnel gave no link within 40 s; only this computer can open the app." "Yellow" }
}

# 7. Run the app until the window closes (or, with -Server, until Windows shuts down). In a window,
#    the app window opens as soon as the app answers.
if (-not $Server -and -not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($script, $port)
        for ($i = 0; $i -lt 180; $i++) {
            $client = New-Object System.Net.Sockets.TcpClient
            $up = $false
            try { $up = $client.ConnectAsync("127.0.0.1", $port).Wait(400) } catch { } finally { $client.Close() }
            if ($up) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script -Background -Port $port; break }
            Start-Sleep -Milliseconds 500
        }
    } -ArgumentList $PSCommandPath, $Port | Out-Null
}
# The app window's browser is kept running with no window, so the icon opens the window in about a
# second instead of five (most of that is the browser starting). It quits when its window is closed, so
# this checks every 15 s and starts it again; the browser holds "lockfile" in its profile while it runs.
$browser = Get-Browser
if ($Server -and $browser -and -not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($browser, $profileDir)
        $lock = Join-Path $profileDir "lockfile"
        while ($true) {
            $running = $false
            if (Test-Path $lock) { try { [IO.File]::Open($lock, "Open", "ReadWrite", "None").Close() } catch { $running = $true } }
            if (-not $running) { Start-Process $browser -ArgumentList "--user-data-dir=`"$profileDir`"", "--no-startup-window", "--no-first-run" }
            Start-Sleep 15
        }
    } -ArgumentList $browser, $AppProfile | Out-Null
}
$where = if ($PublicUrl) { "$Local and $PublicUrl" } else { $Local }
if ($Server) { Say "Nightshift QA: $where" }
else { Say "`nNightshift QA: $where   Close this window (or press Ctrl+C) to stop it.`n" "Cyan" }
$serve = @($NsPrefix) + @("hosted", "serve", "--data", "$Data", "--port", "$Port")
if ($PublicUrl) { $serve += @("--public-url", $PublicUrl) }
try {
    if ($Server) {
        # The server's own messages go to the log as UTF-8 (">>" would write UTF-16 in Windows PowerShell),
        # and its error output is just more lines, not a stopping error.
        $ErrorActionPreference = "Continue"
        & $NsCommand @serve 2>&1 | ForEach-Object { Add-Content $LogFile "$_" -Encoding UTF8 }
    } else {
        & $NsCommand @serve
    }
} finally {
    if ($Tunnel) {
        Stop-Process -Id $Tunnel.Id -ErrorAction SilentlyContinue
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }
}
