$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$environmentFile = Join-Path $projectRoot '.env'
$runner = Join-Path $PSScriptRoot 'run_scheduled_sync.ps1'
$pythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
$taskName = 'FootballAnalyticsPro-DailySync'

if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    throw 'No se encontró .env. Configurá primero la base local y el token de football-data.org.'
}
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw 'No se encontró .venv. Instalá primero las dependencias del proyecto.'
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "La tarea $taskName ya existe; no se modificó."
}

$syncTime = '08:00'
$tokenConfigured = $false
foreach ($line in Get-Content -LiteralPath $environmentFile) {
    if ($line -match '^\s*SYNC_TIME\s*=\s*(\d{2}:\d{2})\s*$') {
        $syncTime = $Matches[1]
    }
    if ($line -match '^\s*FOOTBALL_DATA_TOKEN\s*=\s*(\S+)\s*$') {
        $tokenConfigured = $true
    }
}
if (-not $tokenConfigured) {
    throw 'FOOTBALL_DATA_TOKEN está vacío en .env; no se registró la tarea.'
}

$runAt = [DateTime]::Today.Add([TimeSpan]::Parse($syncTime))
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$null = Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Actualiza Football Analytics Pro una vez al día.' `
    -ErrorAction Stop

$registeredTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ($registeredTask.State -eq 'Disabled') {
    throw "La tarea $taskName quedó deshabilitada; no se considera registrada correctamente."
}

Write-Output "Tarea $taskName registrada para las $syncTime (hora local de Windows)."
Write-Output "Registro de salida: $(Join-Path $projectRoot 'logs\scheduled_sync.log')"
