param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Wait-ForReadableFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [int]$Attempts = 20,
        [int]$DelayMilliseconds = 500
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
            $stream.Close()
            return
        }
        catch {
            if ($attempt -eq $Attempts) {
                throw "File is not ready for reading: $Path`n$($_.Exception.Message)"
            }
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
}

Write-Host "Installing runtime and build dependencies..."
& $PythonExe -m pip install -e ".[dev]" -c ".\constraints.txt"
& $PythonExe -m pip check

$version = & $PythonExe -c "from aruba_mm_cleanup import __version__; print(__version__)"
$guiExeName = "ArubaMMCleanupGUI"
$cliExeName = "ArubaMMCleanupCLI"
$distDir = Join-Path $PSScriptRoot "dist"
$buildRoot = Join-Path $PSScriptRoot ".pyinstaller_build"
$buildDir = Join-Path $buildRoot ([DateTime]::Now.ToString("yyyyMMdd_HHmmss"))
$specDir = Join-Path $buildDir "spec"
$releaseRoot = Join-Path $buildDir "release"
$distGuiExe = Join-Path $distDir "$guiExeName.exe"
$distCliExe = Join-Path $distDir "$cliExeName.exe"
$releaseZip = Join-Path $distDir "${guiExeName}_v${version}.zip"

New-Item -ItemType Directory -Force -Path $distDir | Out-Null
New-Item -ItemType Directory -Force -Path $specDir | Out-Null
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

if (Test-Path $distGuiExe) { Remove-Item -LiteralPath $distGuiExe -Force }
if (Test-Path $distCliExe) { Remove-Item -LiteralPath $distCliExe -Force }
if (Test-Path $releaseZip) { Remove-Item -LiteralPath $releaseZip -Force }

Write-Host "Building Windows GUI executable..."
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $guiExeName `
    --distpath $distDir `
    --workpath $buildDir `
    --specpath $specDir `
    --paths ".\src" `
    ".\gui_launcher.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller GUI build failed with exit code $LASTEXITCODE"
}

Write-Host "Building Windows CLI executable..."
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name $cliExeName `
    --distpath $distDir `
    --workpath $buildDir `
    --specpath $specDir `
    --paths ".\src" `
    ".\cli_launcher.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller CLI build failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath $distGuiExe -Destination $releaseRoot -Force
Copy-Item -LiteralPath $distCliExe -Destination $releaseRoot -Force
Copy-Item -LiteralPath ".\README.md" -Destination $releaseRoot -Force
Copy-Item -LiteralPath ".\docs\USER_GUIDE_KO.md" -Destination $releaseRoot -Force
New-Item -ItemType Directory -Force -Path (Join-Path $releaseRoot "config\mock_scenarios") | Out-Null
Copy-Item -LiteralPath ".\config\mock_scenarios\profiling_users.txt" -Destination (Join-Path $releaseRoot "config\mock_scenarios") -Force

Get-ChildItem -Path $releaseRoot -Recurse -File | ForEach-Object {
    Wait-ForReadableFile -Path $_.FullName
}

if (Test-Path $releaseZip) {
    Remove-Item -LiteralPath $releaseZip -Force
}
Compress-Archive -Path (Join-Path $releaseRoot "*") -DestinationPath $releaseZip -Force

Write-Host ""
Write-Host "Build completed."
Write-Host "GUI executable: $distGuiExe"
Write-Host "CLI executable: $distCliExe"
Write-Host "Release zip: $releaseZip"
