$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectRoot 'logs'
$logPath = Join-Path $logDirectory 'scheduled_sync.log'
$startedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
$fallbackLogPath = Join-Path $logDirectory ("scheduled_sync_{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$script:syncLogPath = $logPath
$script:fallbackLogPath = $fallbackLogPath

function Write-SyncLog {
    param([Parameter(Mandatory)][string]$Message)

    $entry = "[$startedAt] $Message"
    try {
        Add-Content -LiteralPath $script:syncLogPath -Value $entry -Encoding utf8 -ErrorAction Stop
    } catch {
        if ($script:syncLogPath -ne $script:fallbackLogPath) {
            $script:syncLogPath = $script:fallbackLogPath
            try {
                Add-Content -LiteralPath $script:syncLogPath `
                    -Value "[$startedAt] El registro principal estaba ocupado; los detalles siguen en este archivo." `
                    -Encoding utf8 -ErrorAction Stop
            } catch {
                return
            }
        }
        try {
            Add-Content -LiteralPath $script:syncLogPath -Value $entry -Encoding utf8 -ErrorAction Stop
        } catch {
            # A logging lock must not turn a successful data sync into a failed task.
        }
    }
}

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "No se encontró Python del entorno virtual: $pythonExecutable"
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $projectRoot
$output = @()
$syncExitCode = 1
Write-SyncLog 'Sincronización diaria iniciada.'
try {
    # Revisit historical player match lines during the scheduled run as well as
    # the normal daily fixtures/profiles. The importer keeps its source checks
    # and updates existing rows instead of creating duplicate statistics.
    $output = & $pythonExecutable 'scripts\sync_daily.py' '--refresh-player-statistics' 2>&1
    $syncExitCode = $LASTEXITCODE
} catch {
    Write-SyncLog "Error al ejecutar la sincronización: $($_.Exception.Message)"
    $syncExitCode = 1
}
foreach ($line in $output) {
    Write-SyncLog ([string]$line)
}
$finishedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
Write-SyncLog "Sincronización finalizada con código $syncExitCode."
exit $syncExitCode
