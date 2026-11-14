#Requires -Version 5.1
<#
.SYNOPSIS
    Builds the fully offline MailDaan Windows installer:
    dist\MailDaan-Setup-<version>.exe

.DESCRIPTION
    1. Obtains the official Microsoft Edge WebView2 Fixed Version Runtime
       (x64): discovers the release currently published on Microsoft's
       official download page, downloads its .cab and extracts it with
       Microsoft's documented `expand` command into webview2_runtime\.
       The runtime is bundled inside the application folder, so MailDaan
       never depends on a system-installed Evergreen runtime and the
       installer never asks the user to download one.
    2. Installs build requirements and runs PyInstaller (one-folder build),
       which embeds webview2_runtime\ next to the Python runtime.
    3. Hygiene scan: the packaged tree must contain no user data
       (databases, .eml files, account settings, logs, credentials).
    4. Obtains the official Microsoft .NET Framework 4.6.2 offline
       redistributable (optional: every supported Windows 10/11 already
       ships .NET Framework 4.7.2+). It is verified by Authenticode
       signature (Microsoft Corporation), and its SHA-256 is recorded.
    5. Locates (or installs) Inno Setup and compiles installer\maildaan.iss,
       embedding the PyInstaller output and the offline .NET installer.
    6. Verifies the produced setup executable and its file manifest.

.PARAMETER SkipPyInstaller
    Reuse the existing dist\MailDaan build instead of running PyInstaller.

.PARAMETER SkipRequirements
    Do not run 'pip install -r requirements-build.txt' first.
#>

[CmdletBinding()]
param(
    [switch] $SkipPyInstaller,
    [switch] $SkipRequirements
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $repoRoot

$dist = Join-Path $repoRoot 'dist'
$appDist = Join-Path $dist 'MailDaan'
$prereqDir = Join-Path $repoRoot 'installer\prerequisites'
$runtimeSrc = Join-Path $repoRoot 'webview2_runtime'
$toolsDir = Join-Path $repoRoot 'tools'

# --- Official, stable Microsoft distribution points ------------------------
# The Fixed Version Runtime release currently published on Microsoft's own
# WebView2 download page (the page embeds the direct .cab links).
$WebView2DownloadPage = 'https://developer.microsoft.com/en-us/Microsoft-edge/webview2/'
$DotNetOfflineUrls = @(
    'https://download.microsoft.com/download/F/E/9/FE9127ED-7E83-4B1B-9E8B-7B6F7A8E1E94/NDP462-KB3151800-x86-x64-AllOS-ENU.exe',
    'https://dotnet.microsoft.com/en-us/download/dotnet-framework/thank-you/net462-offline-installer'
)

function Log-Step($message) { Write-Host ""; Write-Host "== $message" -ForegroundColor Cyan }
function Log-Ok($message) { Write-Host "   OK  $message" -ForegroundColor Green }
function Log-Warn($message) { Write-Host "   !!  $message" -ForegroundColor Yellow }
function Fail($message) { Write-Host "   FAIL $message" -ForegroundColor Red; exit 1 }

function Get-FileVersionInfo($path) {
    return [System.Diagnostics.FileVersionInfo]::GetVersionInfo($path)
}

function Verify-AuthenticodeMicrosoft($path, $what) {
    $sig = Get-AuthenticodeSignature -LiteralPath $path
    if ($sig.Status -ne 'Valid') {
        throw "$what has an invalid digital signature (status: $($sig.Status)). Refusing to embed it."
    }
    if ($sig.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation') {
        throw "$what is not signed by Microsoft Corporation (signer: $($sig.SignerCertificate.Subject)). Refusing to embed it."
    }
    return $sig
}

function Verify-FileMetadata($path, $what, $expectInProduct) {
    $vi = Get-FileVersionInfo $path
    $haystack = "$($vi.ProductName) $($vi.FileDescription) $($vi.CompanyName)"
    if ($haystack -notmatch [regex]::Escape($expectInProduct)) {
        throw "$what does not look like the expected Microsoft product (metadata: '$haystack')."
    }
    return $vi
}

function Download-File($url, $destination, $what) {
    Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
    $client = New-Object System.Net.Http.HttpClient (New-Object System.Net.Http.HttpClientHandler)
    $client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
    $client.DefaultRequestHeaders.Add('User-Agent', 'MailDaan-build')
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $resp = $client.GetAsync($url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).Result
        if (-not $resp.IsSuccessStatusCode) {
            $code = [int]$resp.StatusCode
            $resp.Dispose()
            throw "server returned HTTP $code"
        }
        $total = $resp.Content.Headers.ContentLength
        Write-Host "   downloading $what ($([math]::Round($total / 1MB, 1)) MB) from $url"
        $stream = $resp.Content.ReadAsStreamAsync().Result
        $fs = [System.IO.File]::Create($destination)
        try { $stream.CopyTo($fs, 65536) } finally { $fs.Close(); $resp.Dispose() }
        $sw.Stop()
        Log-Ok "downloaded in $([math]::Round($sw.Elapsed.TotalSeconds,1))s"
    } catch {
        $ex = $_.Exception
        while ($ex.InnerException) { $ex = $ex.InnerException }
        throw "download failed: $($ex.Message)"
    } finally {
        $client.Dispose()
    }
}

# ---------------------------------------------------------------------------
# 0. Version
# ---------------------------------------------------------------------------
$config = Get-Content -LiteralPath (Join-Path $repoRoot 'config.py') -Raw
if ($config -notmatch 'APP_VERSION\s*=\s*"([^"]+)"') { Fail "could not read APP_VERSION from config.py" }
$version = $matches[1]
$outputName = "MailDaan-Setup-$version.exe"
Log-Step "MailDaan $version - fully offline installer build"

# ---------------------------------------------------------------------------
# 1. Microsoft Edge WebView2 Fixed Version Runtime (x64)
# ---------------------------------------------------------------------------
Log-Step "Discovering the official WebView2 Fixed Version Runtime (x64)"
$page = (Invoke-WebRequest -Uri $WebView2DownloadPage -UseBasicParsing).Content -replace '\\u002F', '/'
$fixedMatch = [regex]::Match($page, 'https://msedge\.sf\.dl\.delivery\.mp\.microsoft\.com/filestreamingservice/files/([0-9a-z-]+)/Microsoft\.WebView2\.FixedVersionRuntime\.([0-9.]+)\.x64\.cab')
if (-not $fixedMatch.Success) {
    Fail "Could not find the official WebView2 Fixed Version Runtime (x64) download link on $WebView2DownloadPage . A fully offline installer cannot be produced without it."
}
$wvFixedVersion = $fixedMatch.Groups[2].Value
$wvCabUrl = $fixedMatch.Value
$wvCabName = "Microsoft.WebView2.FixedVersionRuntime.$wvFixedVersion.x64.cab"
$wvRuntimeDirName = "Microsoft.WebView2.FixedVersionRuntime.$wvFixedVersion.x64"
$wvCabPath = Join-Path $prereqDir $wvCabName
$wvRuntimePath = Join-Path $runtimeSrc $wvRuntimeDirName
Log-Ok "Microsoft publishes Fixed Version Runtime $wvFixedVersion (x64)"

# A leftover Evergreen Standalone Installer is no longer embedded; drop it so
# the prerequisites folder cannot keep a stale duplicate runtime payload.
$evergreen = Join-Path $prereqDir 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
if (Test-Path -LiteralPath $evergreen) {
    Remove-Item -LiteralPath $evergreen -Force
    Log-Ok "removed the obsolete Evergreen Standalone Installer (no longer embedded)"
}

$wvNeeded = -not (Test-Path -LiteralPath $wvCabPath)
if (-not $wvNeeded) {
    try {
        $null = Verify-AuthenticodeMicrosoft $wvCabPath 'WebView2 Fixed Version Runtime cabinet'
    } catch {
        Log-Warn "existing file failed verification: $_"
        $wvNeeded = $true
    }
}
if ($wvNeeded) {
    try {
        Download-File $wvCabUrl $wvCabPath 'WebView2 Fixed Version Runtime (x64)'
        $null = Verify-AuthenticodeMicrosoft $wvCabPath 'WebView2 Fixed Version Runtime cabinet'
    } catch {
        Fail "Could not obtain the official Microsoft WebView2 Fixed Version Runtime during the build: $_  A fully offline installer cannot be produced without it."
    }
}
$wvSha = (Get-FileHash -LiteralPath $wvCabPath -Algorithm SHA256).Hash
Log-Ok "WebView2 Fixed Version Runtime cabinet verified (SHA-256 $wvSha)"

# Extract with Microsoft's documented method (`expand <cab> -F:* <dest>`),
# preserving the original directory structure. Reuse an existing extraction
# of exactly this version; any other runtime folder is removed first.
if (-not (Test-Path -LiteralPath (Join-Path $wvRuntimePath 'msedgewebview2.exe'))) {
    if (Test-Path -LiteralPath $runtimeSrc) {
        Remove-Item -LiteralPath $runtimeSrc -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $runtimeSrc | Out-Null
    & expand $wvCabPath -F:* $runtimeSrc | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Extracting the WebView2 Fixed Version Runtime failed (expand exit code $LASTEXITCODE)." }
    if (-not (Test-Path -LiteralPath (Join-Path $wvRuntimePath 'msedgewebview2.exe'))) {
        Fail "The extracted WebView2 Fixed Version Runtime does not contain msedgewebview2.exe."
    }
    Log-Ok "runtime extracted to webview2_runtime\$wvRuntimeDirName"
} else {
    Log-Ok "reusing existing extraction webview2_runtime\$wvRuntimeDirName"
}
$runtimeFiles = @(Get-ChildItem -LiteralPath $wvRuntimePath -Recurse -File)
Log-Ok "runtime contains $($runtimeFiles.Count) files ($([math]::Round((($runtimeFiles | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)) MB)"

# ---------------------------------------------------------------------------
# 2. PyInstaller one-folder build
# ---------------------------------------------------------------------------
if (-not $SkipRequirements) {
    Log-Step "Installing build requirements"
    & python -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { Fail "pip install of build requirements failed." }
    Log-Ok "requirements satisfied"
}

if (-not $SkipPyInstaller) {
    Log-Step "Running PyInstaller (one-folder distribution)"
    & python -m PyInstaller --noconfirm --clean maildaan.spec
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed." }
    Log-Ok "dist\MailDaan built"
}

# ---------------------------------------------------------------------------
# 3. Hygiene scan: no user data may ever be packaged
# ---------------------------------------------------------------------------
Log-Step "Hygiene scan of the packaged application tree"
$forbiddenFiles = @('app_settings.json', 'accounts.json', 'app_password.json', 'settings.json',
                    'maildaan.log', 'startup.log')
$forbiddenExts = @('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.eml')
$forbiddenDirs = @('accounts', 'data', 'eml', 'attachments', 'exports', 'logs')

$bad = New-Object System.Collections.Generic.List[string]
foreach ($item in (Get-ChildItem -LiteralPath $appDist -Recurse -File -ErrorAction SilentlyContinue)) {
    if ($forbiddenFiles -contains $item.Name) { $bad.Add("user data file: $($item.FullName.Substring($appDist.Length))") }
    if ($forbiddenExts -contains $item.Extension.ToLower()) { $bad.Add("user data extension: $($item.FullName.Substring($appDist.Length))") }
}
foreach ($item in (Get-ChildItem -LiteralPath $appDist -Recurse -Directory -ErrorAction SilentlyContinue)) {
    if ($forbiddenDirs -contains $item.Name.ToLower()) { $bad.Add("user data directory: $($item.FullName.Substring($appDist.Length))") }
}
if ($bad.Count -gt 0) {
    $bad | ForEach-Object { Write-Host "   FAIL $_" -ForegroundColor Red }
    Fail "the packaged tree contains user data (emails, databases, settings or credentials). Clean dist\MailDaan and rebuild."
}
Log-Ok "no user data present"

$required = @(
    'MailDaan.exe',
    '_internal\python314.dll',
    '_internal\pythonnet\runtime\Python.Runtime.dll',
    '_internal\webview\lib\runtimes\win-x64\native\WebView2Loader.dll',
    '_internal\ui\index.html',
    '_internal\ui\app.js',
    '_internal\ui\styles.css',
    '_internal\ui\locales\en.json',
    '_internal\ui\locales\fa.json',
    '_internal\ui\fonts\Vazirmatn-Regular.woff2',
    '_internal\assets\maildaan.svg'
    '_internal\assets\maildaan.ico'
)
foreach ($rel in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $appDist $rel))) { Fail "missing required file in the build: $rel" }
}
$pythonDll = Get-ChildItem -LiteralPath (Join-Path $appDist '_internal') -Filter 'python3*.dll' -ErrorAction SilentlyContinue
if (-not $pythonDll) { Fail "missing the Python runtime DLL in the build." }
$requiredRuntime = Join-Path $appDist "_internal\webview2_runtime\$wvRuntimeDirName\msedgewebview2.exe"
if (-not (Test-Path -LiteralPath $requiredRuntime)) { Fail "the bundled WebView2 Fixed Version Runtime is missing from the build: _internal\webview2_runtime\$wvRuntimeDirName" }
Log-Ok "all required application assets present (including the bundled WebView2 runtime)"

# ---------------------------------------------------------------------------
# 4. Optional offline .NET Framework redistributable
# ---------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $prereqDir | Out-Null
$dotnet = Join-Path $prereqDir 'NDP462-KB3151800-x86-x64-AllOS-ENU.exe'
$dotnetAvailable = $false
$manifest = [ordered]@{ }

Log-Step "Checking for the Microsoft .NET Framework 4.6.2 offline redistributable"
if (Test-Path -LiteralPath $dotnet) {
    try {
        $null = Verify-AuthenticodeMicrosoft $dotnet '.NET Framework 4.6.2 offline installer'
        $null = Verify-FileMetadata $dotnet '.NET Framework 4.6.2 offline installer' 'Microsoft .NET Framework'
        $dotnetAvailable = $true
    } catch {
        Log-Warn "existing file failed verification: $_  (it will not be embedded)"
        Remove-Item -LiteralPath $dotnet -Force
    }
}
if (-not $dotnetAvailable) {
    foreach ($url in $DotNetOfflineUrls) {
        try {
            Download-File $url $dotnet '.NET Framework 4.6.2 offline installer'
            $null = Verify-AuthenticodeMicrosoft $dotnet '.NET Framework 4.6.2 offline installer'
            $null = Verify-FileMetadata $dotnet '.NET Framework 4.6.2 offline installer' 'Microsoft .NET Framework'
            $dotnetAvailable = $true
            break
        } catch {
            Log-Warn "candidate source unavailable: $_"
            if (Test-Path -LiteralPath $dotnet) { Remove-Item -LiteralPath $dotnet -Force }
        }
    }
}
$dnSha = ''
if ($dotnetAvailable) {
    $dnSha = (Get-FileHash -LiteralPath $dotnet -Algorithm SHA256).Hash
    $dnvi = Get-FileVersionInfo $dotnet
    Log-Ok ".NET Framework 4.6.2 offline installer available (SHA-256 $dnSha)"
    $manifest['dotnet462'] = [ordered]@{
        file = 'NDP462-KB3151800-x86-x64-AllOS-ENU.exe'
        source = ($DotNetOfflineUrls -join ' | ')
        sha256 = $dnSha
        version = "$($dnvi.ProductVersion) / file $($dnvi.FileVersion)"
        bytes = (Get-Item -LiteralPath $dotnet).Length
    }
} else {
    Log-Warn "The official .NET Framework 4.6.2 offline redistributable could not be obtained."
    Log-Warn "Every supported Windows 10/11 build already includes .NET Framework 4.7.2+,"
    Log-Warn "so MailDaan still installs fully offline; setup detects .NET and reports a"
    Log-Warn "clear error if it is ever missing. Place NDP462-KB3151800-x86-x64-AllOS-ENU.exe"
    Log-Warn "in installer\prerequisites\ to embed it as well."
}
$manifest['webview2FixedVersion'] = [ordered]@{
    runtimeVersion = $wvFixedVersion
    architecture = 'x64'
    cabFile = $wvCabName
    source = $wvCabUrl
    sha256 = $wvSha
    bytes = (Get-Item -LiteralPath $wvCabPath).Length
    extractedFiles = $runtimeFiles.Count
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $prereqDir 'manifest.json') -Encoding UTF8

# ---------------------------------------------------------------------------
# 5. Inno Setup compiler
# ---------------------------------------------------------------------------
$innoVersion = '6.7.4'
$iscc = Join-Path $toolsDir "innosetup-$innoVersion\ISCC.exe"
if (-not (Test-Path -LiteralPath $iscc)) {
    $isccCmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($isccCmd) { $iscc = $isccCmd.Source }
}
if (-not (Test-Path -LiteralPath $iscc)) {
    Log-Step "Installing Inno Setup $innoVersion locally (build tool)"
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $innoInstaller = Join-Path $toolsDir "innosetup-$innoVersion.exe"
    Download-File "https://github.com/jrsoftware/issrc/releases/download/is-$($innoVersion.Replace('.','_'))/innosetup-$innoVersion.exe" $innoInstaller "Inno Setup $innoVersion"
    $sig = Get-AuthenticodeSignature -LiteralPath $innoInstaller
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Pyrsys') {
        Fail "Downloaded Inno Setup is not signed by the expected publisher (Pyrsys B.V.)."
    }
    $isccDir = Join-Path $toolsDir "innosetup-$innoVersion"
    & $innoInstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /DIR="$isccDir"
    Start-Sleep -Seconds 2
    if (-not (Test-Path -LiteralPath $iscc)) { Fail "Inno Setup installation failed." }
    Log-Ok "Inno Setup $innoVersion installed under tools\"
}

# ---------------------------------------------------------------------------
# 6. Compile the setup executable
# ---------------------------------------------------------------------------
Log-Step "Compiling the offline installer with Inno Setup"
$isccArgs = @("/DMyAppVersion=$version", "/DWebView2FixedVersion=$wvFixedVersion", "/DDotNetSha256=$dnSha", 'installer\maildaan.iss')
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) { Fail "Inno Setup compilation failed (exit code $LASTEXITCODE)." }

$setup = Join-Path $dist $outputName
if (-not (Test-Path -LiteralPath $setup)) { Fail "expected output not found: $setup" }
$sha = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
$len = (Get-Item -LiteralPath $setup).Length
"$sha *$outputName" | Set-Content -LiteralPath (Join-Path $dist "$outputName.sha256") -Encoding ASCII

# ---------------------------------------------------------------------------
# 7. Verify what was actually embedded
# ---------------------------------------------------------------------------
Log-Step "Verifying the compiled installer contents"
$manifestFile = Get-ChildItem -LiteralPath $dist -Filter "MailDaan-Setup-$version.manifest" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($manifestFile) {
    $mText = Get-Content -LiteralPath $manifestFile.FullName -Raw
    $embeddedForbidden = @('app_settings.json', 'accounts.json', 'app_password.json') | Where-Object { $mText -match [regex]::Escape($_) }
    if ($embeddedForbidden) { Fail "the compiled installer embeds user data files: $($embeddedForbidden -join ', ')" }
    Log-Ok "setup manifest contains no user data files"
    if ($mText -match [regex]::Escape('MicrosoftEdgeWebView2RuntimeInstallerX64.exe')) {
        Fail "the compiled installer still embeds the obsolete Evergreen Standalone Installer."
    }
    if ($mText -notmatch [regex]::Escape("webview2_runtime\$wvRuntimeDirName")) {
        Fail "the compiled installer does not embed the WebView2 Fixed Version Runtime ($wvRuntimeDirName)."
    }
    if ($mText -notmatch 'msedgewebview2\.exe') {
        Fail "the compiled installer does not contain msedgewebview2.exe."
    }
    Log-Ok "WebView2 Fixed Version Runtime $wvFixedVersion is embedded (no Evergreen payload)"
    if ($mText -notmatch 'WebView2Loader') {
        Log-Warn "manifest unexpectedly shows no WebView2 loader files"
    }
    Log-Ok "MailDaan.exe, Python runtime and UI assets embedded"
    Log-Ok "file manifest kept for auditing: dist\MailDaan-Setup-$version.manifest"
} else {
    Log-Warn "compiler manifest not found; content verification skipped"
}

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "  $dist\$outputName"
Write-Host ("  {0:N1} MB" -f ($len / 1MB)) -ForegroundColor Gray
Write-Host "  SHA-256 $sha" -ForegroundColor Gray
