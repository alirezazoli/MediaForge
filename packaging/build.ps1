<#
.SYNOPSIS
    Builds MediaForge.exe and the MediaForge Studio installer.

.DESCRIPTION
    1. Draws the icon and checkbox assets.
    2. Freezes the app with PyInstaller (one folder, windowed).
    3. Copies ffmpeg, ffprobe and the licence texts in beside MediaForge.exe.
    4. Runs the frozen build's own self-test from the build folder.
    5. Compiles the installer with Inno Setup.

    Everything is written under -BuildRoot, which defaults to a folder OUTSIDE the source
    tree: the source lives in Dropbox, and a build is ~1 GB of files nobody wants synced.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Python C:\path\to\python.exe
#>
param(
    [string]$Python = "",
    [string]$BuildRoot = (Join-Path $env:USERPROFILE "MediaForge-build"),
    [string]$FfmpegDir = "",
    [switch]$SkipSelfTest,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

# -- tools ---------------------------------------------------------------------------------
if (-not $Python) {
    $candidates = @((Join-Path $Root ".venv\Scripts\python.exe"))
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    $Python = $candidates | Where-Object { $_ -and (Test-Path $_) -and ($_ -notlike "*WindowsApps*") } |
        Select-Object -First 1
}
if (-not $Python) { throw "No Python found. Pass -Python C:\path\to\python.exe" }
& $Python -c "import PyInstaller, PySide6, numpy, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "$Python is missing build dependencies. Run: $Python -m pip install -r requirements.txt pyinstaller"
}

if (-not $FfmpegDir) {
    $FfmpegDir = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Directory -Filter "Gyan.FFmpeg*" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem $_.FullName -Directory } |
        Where-Object { Test-Path (Join-Path $_.FullName "bin\ffmpeg.exe") } |
        Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $FfmpegDir -or -not (Test-Path (Join-Path $FfmpegDir "bin\ffprobe.exe"))) {
    throw "ffmpeg not found. Install it (winget install Gyan.FFmpeg) or pass -FfmpegDir."
}

$Iscc = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $SkipInstaller -and -not $Iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)." }

Push-Location $Root
try {
    $Version = (& $Python -c "from mediaforge.paths import __version__; print(__version__)").Trim()
    Step "MediaForge Studio $Version"
    Write-Host "    python : $Python"
    Write-Host "    ffmpeg : $FfmpegDir"
    Write-Host "    output : $BuildRoot"

    # -- assets ----------------------------------------------------------------------------
    Step "Drawing icon assets"
    & $Python packaging\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "make_icon.py failed" }

    # -- freeze ----------------------------------------------------------------------------
    Step "Freezing with PyInstaller"
    $Work = Join-Path $BuildRoot "work"
    $Dist = Join-Path $BuildRoot "dist"
    & $Python -m PyInstaller packaging\MediaForge.spec --noconfirm --clean --log-level WARN `
        --workpath $Work --distpath $Dist
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    $App = Join-Path $Dist "MediaForge"

    # -- ffmpeg and licences ---------------------------------------------------------------
    Step "Adding ffmpeg and licence texts"
    $FfmpegOut = New-Item -ItemType Directory -Force (Join-Path $App "ffmpeg")
    Copy-Item (Join-Path $FfmpegDir "bin\ffmpeg.exe"), (Join-Path $FfmpegDir "bin\ffprobe.exe") $FfmpegOut
    $Licences = New-Item -ItemType Directory -Force (Join-Path $App "licenses")
    Copy-Item (Join-Path $Root "THIRD_PARTY_NOTICES.md") $Licences
    Copy-Item (Join-Path $Root "LICENSE") (Join-Path $Licences "MediaForge-LICENSE.txt")
    $FfmpegLicences = New-Item -ItemType Directory -Force (Join-Path $Licences "ffmpeg")
    foreach ($name in "LICENSE", "README.txt") {
        $source = Join-Path $FfmpegDir $name
        if (Test-Path $source) { Copy-Item $source $FfmpegLicences }
    }

    # -- self-test -------------------------------------------------------------------------
    if (-not $SkipSelfTest) {
        Step "Running the frozen self-test"
        $Report = Join-Path $BuildRoot "selftest-build.json"
        Remove-Item $Report -ErrorAction SilentlyContinue
        $process = Start-Process (Join-Path $App "MediaForge.exe") -ArgumentList "--selftest", "--report", "`"$Report`"" `
            -Wait -PassThru -WindowStyle Hidden
        if (-not (Test-Path $Report)) { throw "The frozen self-test wrote no report (exit $($process.ExitCode))" }
        $result = Get-Content $Report -Raw | ConvertFrom-Json
        foreach ($check in $result.checks) {
            $mark = if ($check.ok) { "ok  " } else { "FAIL" }
            Write-Host ("    {0}  {1} -- {2}" -f $mark, $check.check, $check.detail)
        }
        if ($result.failed -gt 0) { throw "$($result.failed) self-test check(s) failed" }
    }

    # -- installer -------------------------------------------------------------------------
    if (-not $SkipInstaller) {
        Step "Compiling the installer"
        $Output = Join-Path $BuildRoot "installer"
        & $Iscc /Q "/DAppVersion=$Version" "/DDistDir=$App" "/DOutputDir=$Output" packaging\installer.iss
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        $Setup = Get-Item (Join-Path $Output "MediaForge-Studio-$Version-Setup.exe")
        $Hash = (Get-FileHash $Setup -Algorithm SHA256).Hash
        Step "Done"
        Write-Host ("    {0}  ({1:N0} MB)" -f $Setup.FullName, ($Setup.Length / 1MB))
        Write-Host "    SHA-256 $Hash"
    }
}
finally {
    Pop-Location
}
