<#
  TokenCost - Windows setup / start / stop script
  PowerShell equivalent of onbording.sh (macOS).

  Run:   powershell -ExecutionPolicy Bypass -File onbording.ps1
  or:    double-click tokencost.bat

  NOTE: This file is intentionally pure ASCII. PowerShell can misread non-ASCII
  bytes (em-dashes, box-drawing chars) as smart-quote string delimiters when the
  file has no UTF-8 BOM, which breaks parsing. Keep it ASCII-only.

  What it does (Start):
    1. Creates a Python venv and installs fastapi / uvicorn / httpx
    2. Imports your local Claude / VS Code history into tracker.db
    3. Sets ANTHROPIC_BASE_URL=http://localhost:8082 as a User env var
    4. Registers a scheduled task (or a Startup-folder launcher if not elevated)
       so the proxy autostarts at logon, plus a 5-minute log-sync task
    5. Starts the proxy in the background and opens the dashboard

  Run with -Update for a non-interactive pull-and-restart (used by the
  dashboard's "Update" command); otherwise it shows the interactive menu.
#>

param([switch]$Update)

$ErrorActionPreference = 'Stop'
$PORT       = 8082
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

$BaseUrl    = "http://localhost:$PORT"
$SmartFile  = Join-Path $ScriptDir ".smart_routing"
$VenvPy     = Join-Path $ScriptDir "venv\Scripts\python.exe"
$ProxyLog   = Join-Path $ScriptDir "proxy.log"
$ProxyErr   = Join-Path $ScriptDir "proxy-error.log"
$TaskProxy  = "TokenCostProxy"
$TaskSync   = "TokenCostSync"
$StartupVbs = Join-Path ([Environment]::GetFolderPath('Startup')) "TokenCost.vbs"

# --- Helpers ------------------------------------------------------------------
function Write-Step($n, $msg) { Write-Host "  [$n] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)       { Write-Host "  [ok] $msg"  -ForegroundColor Green }
function Write-Warn2($msg)    { Write-Host "  [!]  $msg"  -ForegroundColor Yellow }

function Get-ProxyPids {
    try {
        return (Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction Stop |
                Select-Object -ExpandProperty OwningProcess -Unique)
    } catch { return @() }
}
function Test-ProxyRunning { return (@(Get-ProxyPids).Count -gt 0) }

function Stop-Proxy {
    # Kill the uvicorn supervisor+worker pair by port AND by command line,
    # so no orphaned proxy.py process is left behind.
    foreach ($procId in (Get-ProxyPids)) {
        try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch {}
    }
    $self = $ScriptDir.Replace('\', '\\')
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'proxy\.py' -and $_.CommandLine -match $self } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
    Start-Sleep -Milliseconds 800
}

function Test-SmartRoutingOn {
    return ((Test-Path $SmartFile) -and ((Get-Content $SmartFile -Raw).Trim() -eq "1"))
}

function Find-Python {
    foreach ($cand in @("py -3", "python", "python3")) {
        $exe = $cand.Split(" ")[0]
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            try {
                $rest = @($cand.Split(" ")[1..10] | Where-Object { $_ })
                $v = & $exe @rest --version 2>&1
                if ($LASTEXITCODE -eq 0) { return $cand }
            } catch {}
        }
    }
    return $null
}

# --- Autostart: scheduled tasks (need elevation) ------------------------------
function Register-Tasks {
    try {
        $action = New-ScheduledTaskAction -Execute $VenvPy `
                    -Argument "-B `"$ScriptDir\proxy.py`"" -WorkingDirectory $ScriptDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName $TaskProxy -Action $action -Trigger $trigger `
            -Settings $settings -Force -ErrorAction Stop | Out-Null

        $syncAction = New-ScheduledTaskAction -Execute $VenvPy `
                    -Argument "`"$ScriptDir\import_history.py`" --silent" -WorkingDirectory $ScriptDir
        $syncTrigger = New-ScheduledTaskTrigger -AtLogOn
        $syncTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
                    -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
        Register-ScheduledTask -TaskName $TaskSync -Action $syncAction -Trigger $syncTrigger `
            -Settings $settings -Force -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Unregister-Tasks {
    foreach ($t in @($TaskProxy, $TaskSync)) {
        try { Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction Stop } catch {}
    }
}

# --- Autostart fallback: hidden launcher in the Startup folder (no admin) ------
function Set-StartupLauncher {
    $tpl = @'
' TokenCost - start proxy hidden at logon (no console window)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "__DIR__"
sh.Run "cmd /c """"__PY__"" -B ""__DIR__\proxy.py"" >> ""__LOG__"" 2>&1""", 0, False
'@
    $vbs = $tpl.Replace('__DIR__', $ScriptDir).Replace('__PY__', $VenvPy).Replace('__LOG__', $ProxyLog)
    Set-Content -Path $StartupVbs -Value $vbs -Encoding ASCII
}

function Remove-StartupLauncher {
    if (Test-Path $StartupVbs) { Remove-Item $StartupVbs -Force -ErrorAction SilentlyContinue }
}

# --- Start the proxy in the background -----------------------------------------
function Start-ProxyBackground {
    if (Test-SmartRoutingOn) { $env:SMART_ROUTING = "1" } else { $env:SMART_ROUTING = "0" }
    Start-Process -FilePath $VenvPy `
        -ArgumentList "-B", "`"$ScriptDir\proxy.py`"" `
        -WorkingDirectory $ScriptDir `
        -RedirectStandardOutput $ProxyLog `
        -RedirectStandardError  $ProxyErr `
        -WindowStyle Hidden | Out-Null
}

# --- Action: Start -------------------------------------------------------------
function Action-Start {
    Clear-Host
    Write-Host ""
    Write-Host "  TokenCost - Windows Setup" -ForegroundColor White
    Write-Host ""

    # Smart routing prompt
    if (Test-SmartRoutingOn) { $cur = "enabled" } else { $cur = "disabled" }
    Write-Host "  Smart Model Routing (currently: $cur)"
    Write-Host "  Switches Opus/Sonnet -> Haiku for simple requests. Saves ~60% on short tasks."
    $choice = Read-Host "  Enable optimizer? [y/N]"
    if ($choice -match '^(y|yes)$') { "1" | Set-Content $SmartFile -NoNewline; Write-Ok "Optimizer enabled" }
    else { "0" | Set-Content $SmartFile -NoNewline; Write-Host "  Optimizer disabled" }

    # 1. Python
    Write-Host ""
    Write-Step "1/7" "Checking Python..."
    $py = Find-Python
    if (-not $py) {
        Write-Warn2 "Python not found. Install Python 3.9+ from https://www.python.org/downloads/ (check 'Add to PATH')."
        return
    }
    $pyExe  = $py.Split(" ")[0]
    $pyArgs = @($py.Split(" ")[1..10] | Where-Object { $_ })
    Write-Ok ((& $pyExe @pyArgs --version 2>&1) | Out-String).Trim()

    # 2. venv + dependencies
    Write-Host ""
    Write-Step "2/7" "Setting up virtual environment + dependencies..."
    if (-not (Test-Path $VenvPy)) {
        & $pyExe @pyArgs -m venv (Join-Path $ScriptDir "venv")
    }
    $check = & $VenvPy -c "import fastapi, uvicorn, httpx" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Installing packages (~30s)..."
        $req = Join-Path $ScriptDir "requirements.txt"
        if (Test-Path $req) { & $VenvPy -m pip install -r $req -q }
        else { & $VenvPy -m pip install fastapi uvicorn httpx -q }
        if ($LASTEXITCODE -ne 0) { Write-Warn2 "pip install failed"; return }
    }
    Write-Ok "Dependencies ready"

    # 3. Import history
    Write-Host ""
    Write-Step "3/7" "Importing history from local logs..."
    & $VenvPy (Join-Path $ScriptDir "import_history.py") --silent 2>&1 | Out-Null
    Write-Ok "History imported into tracker.db"

    # 4. Env var (Claude Code / VS Code / Claude Desktop / new terminals)
    Write-Host ""
    Write-Step "4/7" "Setting ANTHROPIC_BASE_URL (User environment)..."
    [Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", $BaseUrl, "User")
    $env:ANTHROPIC_BASE_URL = $BaseUrl
    Write-Ok "ANTHROPIC_BASE_URL = $BaseUrl"

    # 5. Autostart + sync
    Write-Host ""
    Write-Step "5/7" "Registering autostart + sync..."
    if (Register-Tasks) {
        Remove-StartupLauncher   # avoid double autostart if a task already exists
        Write-Ok "Scheduled tasks registered (proxy autostart, sync every 5 min)"
    } else {
        Set-StartupLauncher
        Write-Ok "Proxy autostart added to Startup folder (no admin required)"
        Write-Warn2 "Auto-sync needs admin - use the 'Sync now' button in the dashboard, or re-run as Administrator"
    }

    # 6. Start proxy
    Write-Host ""
    Write-Step "6/7" "Starting proxy..."
    Stop-Proxy
    Start-ProxyBackground
    $ready = $false
    foreach ($i in 1..15) {
        Start-Sleep -Seconds 1
        if (Test-ProxyRunning) { $ready = $true; break }
    }
    if ($ready) { Write-Ok "Proxy running on $BaseUrl" }
    else { Write-Warn2 "Proxy did not report ready - check proxy-error.log" }

    # 7. Open dashboard
    Write-Host ""
    Write-Step "7/7" "Opening dashboard..."
    Start-Process "$BaseUrl/dashboard" | Out-Null

    Write-Host ""
    Write-Host "  ===================================================" -ForegroundColor Green
    Write-Host "   Setup complete!  ->  $BaseUrl/dashboard"            -ForegroundColor Green
    Write-Host "  ===================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Restart VS Code / Cursor / Claude Desktop once so they pick up"
    Write-Host "  the new ANTHROPIC_BASE_URL. New terminals get it automatically."
    Write-Host ""
}

# --- Action: Disable -----------------------------------------------------------
function Action-Disable {
    Clear-Host
    Write-Host ""
    Write-Host "  TokenCost - Disable" -ForegroundColor White
    Write-Host ""

    if (Test-ProxyRunning) { Stop-Proxy; Write-Ok "Proxy stopped" }
    else { Write-Host "  Proxy was not running" }

    Unregister-Tasks
    Remove-StartupLauncher
    Write-Ok "Removed autostart (scheduled tasks + Startup launcher)"

    [Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", $null, "User")
    Remove-Item Env:\ANTHROPIC_BASE_URL -ErrorAction SilentlyContinue
    Write-Ok "Removed ANTHROPIC_BASE_URL env var"

    Write-Host ""
    Write-Host "  Done. TokenCost fully disabled." -ForegroundColor Green
    Write-Host "  Claude Code and VS Code now connect directly to Anthropic."
    Write-Host "  (Restart open apps to drop the proxy setting.)"
    Write-Host ""
}

# --- Action: Update (non-interactive: re-import + restart with new code) -------
function Action-Update {
    Write-Host ""
    Write-Host "  TokenCost - Update" -ForegroundColor White
    Write-Host ""
    if (-not (Test-Path $VenvPy)) { Write-Warn2 "venv missing - run setup first (tokencost.bat)"; return }
    Write-Step "1/2" "Importing latest history..."
    & $VenvPy (Join-Path $ScriptDir "import_history.py") --silent 2>&1 | Out-Null
    Write-Ok "History imported"
    Write-Step "2/2" "Restarting proxy with updated code..."
    Stop-Proxy
    Start-ProxyBackground
    $ready = $false
    foreach ($i in 1..15) { Start-Sleep -Seconds 1; if (Test-ProxyRunning) { $ready = $true; break } }
    if ($ready) { Write-Ok "Proxy restarted on $BaseUrl" }
    else { Write-Warn2 "Proxy did not report ready - check proxy-error.log" }
    Write-Host ""
    Write-Host "  Update complete. Dashboard: $BaseUrl/dashboard" -ForegroundColor Green
    Write-Host ""
}

# Non-interactive update path (invoked by the dashboard's update command)
if ($Update) { Action-Update; exit 0 }

# --- Menu ----------------------------------------------------------------------
Clear-Host
Write-Host ""
Write-Host "  ==============================" -ForegroundColor White
Write-Host "       TokenCost  (Windows)"       -ForegroundColor White
Write-Host "  ==============================" -ForegroundColor White
Write-Host ""
if (Test-ProxyRunning) { Write-Host "  Proxy:     running on port $PORT" -ForegroundColor Green }
else                   { Write-Host "  Proxy:     stopped"               -ForegroundColor Yellow }
if ([Environment]::GetEnvironmentVariable("ANTHROPIC_BASE_URL", "User")) {
    Write-Host "  Routing:   configured (ANTHROPIC_BASE_URL set)" -ForegroundColor Green
} else {
    Write-Host "  Routing:   not configured" -ForegroundColor Yellow
}
if (Test-SmartRoutingOn) { Write-Host "  Optimizer: enabled"  -ForegroundColor Green }
else                     { Write-Host "  Optimizer: disabled" -ForegroundColor Yellow }
Write-Host ""
Write-Host "  1  Start proxy + open dashboard"
Write-Host "  2  Disable proxy completely"
Write-Host "  3  Exit"
Write-Host ""
$sel = Read-Host "  Choose [1/2/3]"

switch ($sel) {
    "1" { Action-Start }
    "2" { Action-Disable }
    "3" { exit 0 }
    default { Write-Warn2 "Invalid choice" }
}
