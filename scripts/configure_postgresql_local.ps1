#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$dataDirectory = "C:\Program Files\PostgreSQL\18\data"
$hbaPath = Join-Path $dataDirectory "pg_hba.conf"
$backupPath = Join-Path $PSScriptRoot "..\.pg_hba.conf.codex-backup"
$pgCtl = "C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe"
$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$configureScript = Join-Path $PSScriptRoot "configure_local_db.py"
$projectRoot = Split-Path -Parent $PSScriptRoot
$service = Get-Service -Name "postgresql-x64-18"

if ($service.Status -ne "Running") {
    throw "El servicio postgresql-x64-18 no está iniciado."
}
if (-not (Test-Path -LiteralPath $hbaPath) -or -not (Test-Path -LiteralPath $python)) {
    throw "No se encuentra pg_hba.conf o el Python del entorno virtual del proyecto."
}
if (Test-Path -LiteralPath $backupPath) {
    throw "Ya existe una copia de pg_hba.conf; revisa $backupPath antes de continuar."
}

$original = [System.IO.File]::ReadAllText($hbaPath)
$temporary = $original
$rules = @(
    "(?m)^([ \t]*host[ \t]+all[ \t]+all[ \t]+127\.0\.0\.1/32[ \t]+)scram-sha-256\b",
    "(?m)^([ \t]*host[ \t]+all[ \t]+all[ \t]+::1/128[ \t]+)scram-sha-256\b"
)

foreach ($rule in $rules) {
    if ([regex]::Matches($temporary, $rule).Count -ne 1) {
        throw "No se encontró exactamente una regla SCRAM de localhost esperada; no se cambió la configuración."
    }
    $temporary = [regex]::Replace($temporary, $rule, '$1trust')
}

Copy-Item -LiteralPath $hbaPath -Destination $backupPath
$configurationChanged = $false
try {
    $configurationChanged = $true
    [System.IO.File]::WriteAllText($hbaPath, $temporary, [System.Text.UTF8Encoding]::new($false))
    & $pgCtl reload -D $dataDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo recargar PostgreSQL con la regla local temporal."
    }

    Push-Location -LiteralPath $projectRoot
    try {
        & $python $configureScript
        $configureExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($configureExitCode -ne 0) {
        throw "No se pudo crear el rol y la base de datos de la aplicación."
    }
}
finally {
    if ($configurationChanged) {
        Copy-Item -LiteralPath $backupPath -Destination $hbaPath -Force
        & $pgCtl reload -D $dataDirectory
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo recargar la configuración SCRAM restaurada. La copia original sigue en $backupPath."
        }
        Remove-Item -LiteralPath $backupPath -Force
        Write-Output "Autenticación SCRAM original restaurada."
    }
}
