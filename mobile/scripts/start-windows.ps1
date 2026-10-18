$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$mobileRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $mobileRoot
$exitCode = 1
$transcribing = $false
$logFile = Join-Path $projectRoot '.local\mobile-launch.log'

try {
    Set-Location -LiteralPath $mobileRoot
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logFile) | Out-Null
    Start-Transcript -Path $logFile -Force | Out-Null
    $transcribing = $true
    Write-Host 'AirPath Mobile - local demo preview'
    Write-Host 'Preview address: http://127.0.0.1:5180'
    Write-Host "Startup log: $logFile"
    $nodeVersion = '22.23.3'
    $nodeRoot = Join-Path $projectRoot ".local\node\node-v$nodeVersion-win-x64"
    $nodeExe = Join-Path $nodeRoot 'node.exe'
    $npmCli = Join-Path $nodeRoot 'node_modules\npm\bin\npm-cli.js'
    if (-not ((Test-Path -LiteralPath $nodeExe) -and (Test-Path -LiteralPath $npmCli))) {
        $installed = Get-Command node.exe -ErrorAction SilentlyContinue
        if ($installed) {
            $candidateNpm = Join-Path (Split-Path -Parent $installed.Source) 'node_modules\npm\bin\npm-cli.js'
            $version = & $installed.Source -p 'process.versions.node'
            if (([version]$version -ge [version]'22.12.0') -and (Test-Path -LiteralPath $candidateNpm)) {
                $nodeExe = $installed.Source
                $npmCli = $candidateNpm
            }
        }
    }
    if (-not ((Test-Path -LiteralPath $nodeExe) -and (Test-Path -LiteralPath $npmCli))) {
        if (-not [Environment]::Is64BitOperatingSystem) { throw 'AirPath Mobile requires 64-bit Windows.' }
        $downloadDir = Split-Path -Parent $nodeRoot
        New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
        $filename = "node-v$nodeVersion-win-x64.zip"
        $archive = Join-Path $downloadDir $filename
        $baseUrl = "https://nodejs.org/dist/v$nodeVersion"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host "Downloading Node.js $nodeVersion into the project..."
        Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$filename" -OutFile $archive
        $checksums = (Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/SHASUMS256.txt").Content
        if ($checksums -is [byte[]]) { $checksums = [Text.Encoding]::UTF8.GetString($checksums) }
        $matching = $checksums -split "`n" | Where-Object { $_.Trim().EndsWith(" $filename") }
        $expected = ($matching.Trim() -split '\s+')[0]
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'The Node.js download failed its checksum check. Please launch again.' }
        Expand-Archive -LiteralPath $archive -DestinationPath $downloadDir -Force
        Remove-Item -LiteralPath $archive
    }
    $env:AIRPATH_NPM_CLI = $npmCli
    & $nodeExe (Join-Path $PSScriptRoot 'launch.mjs') @args
    $exitCode = $LASTEXITCODE
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'If the preview is already running, open http://127.0.0.1:5180 in your browser.'
    $exitCode = 1
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
}
exit $exitCode
