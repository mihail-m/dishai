$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

try {
    Write-Host 'DishAI - Inversion Watch'
    Write-Host 'First launch downloads the tools and dependencies into this project.'
    $uvVersion = '0.13.1'
    $uvDir = Join-Path $projectRoot ".local\uv\$uvVersion"
    $uvExe = Join-Path $uvDir 'uv.exe'
    if (-not (Test-Path -LiteralPath $uvExe)) {
        # x64 also works under emulation on Windows ARM64 and has geospatial Python wheels.
        if (-not [Environment]::Is64BitOperatingSystem) { throw 'DishAI needs 64-bit Windows.' }
        New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
        $archive = Join-Path $uvDir 'uv.zip'
        $url = "https://releases.astral.sh/github/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host 'Downloading the Python setup tool...'
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
        $checksum = (Invoke-WebRequest -UseBasicParsing -Uri "$url.sha256").Content
        if ($checksum -is [byte[]]) { $checksum = [Text.Encoding]::UTF8.GetString($checksum) }
        $expected = ($checksum.Trim() -split '\s+')[0]
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
            throw 'The setup download failed its checksum check. Please launch again.'
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $uvDir -Force
        Remove-Item -LiteralPath $archive
    }
    $env:UV_CACHE_DIR = Join-Path $projectRoot '.local\cache\uv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.local\python'
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $env:DISHAI_UV = $uvExe
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
    & $uvExe run --isolated --no-project --no-config --managed-python --python '3.12' scripts/launch.py @args
    exit $LASTEXITCODE
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
