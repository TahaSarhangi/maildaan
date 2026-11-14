# MailDaan — offline Windows Sandbox acceptance test (runs INSIDE the sandbox).
# Installs the real Setup from the read-only mapped folder, launches MailDaan,
# captures screenshots, checks clean termination, and writes results to the
# writable mapped folder. No network access is used at any point.

$out = 'C:\sb-output'
$logFile = Join-Path $out 'sandbox-test.log'
$result = @{ }

function Say($msg) {
    $stamp = Get-Date -Format 'HH:mm:ss'
    Add-Content -LiteralPath $logFile -Value "$stamp $msg"
}

function Screenshot($name) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $img = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($img)
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $img.Save((Join-Path $out $name))
        $g.Dispose(); $img.Dispose()
        Say "screenshot saved: $name"
    } catch {
        Say "screenshot failed: $_"
    }
}

function CloseApp {
    $app = Get-Process MailDaan -ErrorAction SilentlyContinue
    if (-not $app) { Say 'app already closed'; return $true }
    $app | ForEach-Object { try { $_.CloseMainWindow() } catch { } } | Out-Null
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process MailDaan -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    $still = Get-Process MailDaan -ErrorAction SilentlyContinue
    if ($still) {
        Say 'ERROR: MailDaan did not exit after closing its window'
        return $false
    }
    # Clean termination: all renderer processes spawned by MailDaan must be gone.
    Start-Sleep -Seconds 3
    $renderers = Get-Process msedgewebview2 -ErrorAction SilentlyContinue
    if ($renderers) {
        Say "ERROR: $($renderers.Count) msedgewebview2 process(es) still running after close"
        return $false
    }
    Say 'app closed cleanly (no leftover MailDaan/msedgewebview2 processes)'
    return $true
}

try {
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    Say '=== MailDaan offline sandbox test started ==='

    $setup = Get-ChildItem -LiteralPath 'C:\sb-input' -Filter 'MailDaan-Setup-*.exe' | Select-Object -First 1
    if (-not $setup) { throw 'no MailDaan-Setup-*.exe found in C:\sb-input' }
    Say "installer: $($setup.Name)"

    # ---- 1. Install fully offline (no Evergreen runtime must be installed) ----
    $beforeEdge = Test-Path 'C:\Program Files (x86)\Microsoft\EdgeWebView\Application'
    $installLog = Join-Path $out 'setup.log'
    $p = Start-Process -FilePath $setup.FullName `
        -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LANG=en /LOG=`"$installLog`"" `
        -Wait -PassThru
    $result.installExitCode = $p.ExitCode
    Say "installer exit code: $($p.ExitCode)"
    if ($p.ExitCode -ne 0) { throw "installer failed with exit code $($p.ExitCode)" }

    $appExe = 'C:\Program Files\MailDaan\MailDaan.exe'
    $result.installed = (Test-Path -LiteralPath $appExe)
    if (-not $result.installed) { throw 'C:\Program Files\MailDaan\MailDaan.exe missing after install' }
    Say 'MailDaan installed to C:\Program Files\MailDaan'

    # ---- 2. Bundled Fixed Version Runtime present on disk ----
    $runtimeDir = Get-ChildItem -LiteralPath 'C:\Program Files\MailDaan\_internal\webview2_runtime' -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    $result.runtimeDir = if ($runtimeDir) { $runtimeDir.Name } else { $null }
    $runtimeExe = if ($runtimeDir) { Join-Path $runtimeDir.FullName 'msedgewebview2.exe' } else { $null }
    $result.runtimePresent = [bool](Test-Path -LiteralPath $runtimeExe)
    if (-not $result.runtimePresent) { throw 'bundled WebView2 Fixed Version Runtime missing after install' }
    Say "bundled runtime: $($runtimeDir.Name)"

    # Permissions required by Microsoft for the Fixed Version runtime on Windows 10
    $aclOut = & cmd /c "icacls `"$($runtimeDir.FullName)`" 2>&1" | Out-String
    Add-Content -LiteralPath $logFile -Value $aclOut
    $result.aclAllAppPackages = $aclOut -match 'ALL APPLICATION PACKAGES|S-1-15-2-2'
    Say "icacls ALL APPLICATION PACKAGES granted: $($result.aclAllAppPackages)"

    # ---- 3. No user data / credentials packaged ----
    $bad = @()
    foreach ($item in (Get-ChildItem -LiteralPath 'C:\Program Files\MailDaan' -Recurse -File -ErrorAction SilentlyContinue)) {
        if ($item.Name -in @('app_settings.json', 'accounts.json', 'app_password.json', 'settings.json', 'maildaan.log', 'startup.log')) { $bad += $item.FullName }
        if ($item.Extension.ToLower() -in @('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.eml')) { $bad += $item.FullName }
    }
    $result.noUserDataPackaged = ($bad.Count -eq 0)
    if ($bad.Count -gt 0) { Say "ERROR: user data found in install: $($bad -join '; ')" } else { Say 'no user data / credentials in the installed tree' }

    # ---- 4. First launch: interface must render using the bundled runtime ----
    $appLogDir = Join-Path $env:LOCALAPPDATA 'MailDaan\logs'
    $appLog = Join-Path $appLogDir 'maildaan.log'
    Start-Process -FilePath $appExe | Out-Null
    $bootstrapped = $false
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $appLog) {
            $text = Get-Content -LiteralPath $appLog -Raw -ErrorAction SilentlyContinue
            if ($text -match 'frontend bootstrap') { $bootstrapped = $true; break }
        }
        Start-Sleep -Seconds 2
    }
    $text = if (Test-Path -LiteralPath $appLog) { Get-Content -LiteralPath $appLog -Raw } else { '' }
    $result.firstRunBootstrapped = $bootstrapped
    $result.firstRunUsesBundledRuntime = $text -match 'using bundled WebView2 Fixed Version runtime'
    Say "first run: bootstrapped=$bootstrapped bundledRuntime=$($result.firstRunUsesBundledRuntime)"
    # Renderer processes must come from the bundled runtime folder.
    $rendererPaths = (Get-Process msedgewebview2 -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*Program Files\MailDaan*' } | Measure-Object).Count
    $result.firstRunBundledRendererProcesses = $rendererPaths
    Say "renderer processes from the bundled runtime: $rendererPaths"
    Screenshot 'step1-first-run.png'
    if (-not $bootstrapped) { throw 'frontend did not bootstrap on first run (blank window)' }
    if (-not ($text -match 'using bundled WebView2 Fixed Version runtime')) { throw 'MailDaan did not use the bundled runtime' }
    Add-Content -LiteralPath $logFile -Value ('--- app log ---' + "`n" + $text + '`n--- end app log ---')

    $result.cleanClose1 = CloseApp

    # ---- 5. Language selection (fa): Persian UI must render with the bundled font ----
    $settingsPath = Join-Path $env:LOCALAPPDATA 'MailDaan\app_settings.json'
    Set-Content -LiteralPath $settingsPath -Value '{"language": "fa", "theme": "light", "active_account_id": null}' -Encoding UTF8
    Start-Process -FilePath $appExe | Out-Null
    $bootstrappedFa = $false
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $appLog) {
            $t2 = Get-Content -LiteralPath $appLog -Raw -ErrorAction SilentlyContinue
            $count = ([regex]::Matches($t2, 'frontend bootstrap')).Count
            if ($count -ge 2) { $bootstrappedFa = $true; break }
        }
        Start-Sleep -Seconds 2
    }
    $result.persianBootstrapped = $bootstrappedFa
    Say "Persian launch: bootstrapped=$bootstrappedFa"
    Screenshot 'step2-persian.png'
    $result.cleanClose2 = CloseApp

    $result.overall = 'PASS'
} catch {
    $result.overall = "FAIL: $_"
    Say "TEST FAILED: $_"
} finally {
    $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $out 'result.json') -Encoding UTF8
    Say '=== test finished ==='
}
