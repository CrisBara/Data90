$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectRoot 'logs'
$logPath = Join-Path $logDirectory 'scheduled_sync.log'
$startedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "No se encontró Python del entorno virtual: $pythonExecutable"
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $projectRoot
$output = & $pythonExecutable 'scripts\sync_daily.py' 2>&1
$exitCode = $LASTEXITCODE
foreach ($line in $output) {
    Add-Content -LiteralPath $logPath -Value "[$startedAt] $line" -Encoding utf8
}
exit $exitCode
