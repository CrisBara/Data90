$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pysideDeploy = Join-Path $projectRoot ".venv\Scripts\pyside6-deploy.exe"
$distRoot = Join-Path $projectRoot "dist"
$buildRoot = Join-Path $projectRoot "build\pyinstaller"
$deployConfig = Join-Path $projectRoot "build\pyside\pysidedeploy.spec"

if (-not (Test-Path -LiteralPath $python)) {
    throw "No se encontró el Python del entorno del proyecto: $python"
}
if (-not (Test-Path -LiteralPath $pysideDeploy)) {
    throw "No se encontró pyside6-deploy en el entorno del proyecto: $pysideDeploy"
}

Push-Location $projectRoot
try {
    & $python -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias de empaquetado." }

    $appDir = Join-Path $distRoot "FootballAnalyticsPro"
    $nuitkaOutput = Join-Path $distRoot "FootballAnalyticsPro.dist"
    foreach ($target in @($appDir, $nuitkaOutput)) {
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

    New-Item -ItemType Directory -Path (Split-Path -Parent $deployConfig) -Force | Out-Null
    $deploySpec = @"
[app]
title = FootballAnalyticsPro
project_dir = ../..
input_file = ../../scripts/gui_entry.py
exec_directory = dist

[python]
packages = Nuitka==4.2.2

[nuitka]
mode = standalone
extra_args = --quiet --noinclude-qt-translations --include-package-data=football_analytics --windows-console-mode=disable --assume-yes-for-downloads
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($deployConfig, $deploySpec, $utf8NoBom)

    & $pysideDeploy --force --config-file $deployConfig --mode standalone `
        --name FootballAnalyticsPro (Join-Path $projectRoot "scripts\gui_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "Falló el empaquetado de la interfaz con pyside6-deploy." }

    New-Item -ItemType Directory -Path $appDir -Force | Out-Null
    Copy-Item -Path (Join-Path $nuitkaOutput '*') -Destination $appDir -Recurse -Force
    $guiExecutable = Join-Path $appDir "gui_entry.exe"
    if (-not (Test-Path -LiteralPath $guiExecutable)) {
        throw "pyside6-deploy no generó el ejecutable esperado: $guiExecutable"
    }
    Move-Item -LiteralPath $guiExecutable -Destination (Join-Path $appDir "FootballAnalyticsPro.exe") -Force
    Remove-Item -LiteralPath $nuitkaOutput -Recurse -Force

    $common = @(
        "--noconfirm",
        "--clean",
        "--paths", "src",
        "--collect-data", "football_analytics",
        "--collect-submodules", "alembic",
        "--hidden-import", "logging.config",
        "--hidden-import", "psycopg",
        "--hidden-import", "psycopg_binary",
        "--hidden-import", "psycopg_c",
        "--distpath", $distRoot,
        "--workpath", $buildRoot
    )

    & $python -m PyInstaller @common --onefile --console --name FootballAnalyticsBootstrap `
        --add-data "alembic.ini;." --add-data "alembic;alembic" scripts\bootstrap_entry.py
    if ($LASTEXITCODE -ne 0) { throw "Falló la generación del asistente de inicialización." }

    $bootstrapPath = Join-Path $distRoot "FootballAnalyticsBootstrap.exe"
    Move-Item -LiteralPath $bootstrapPath `
        -Destination (Join-Path $distRoot "FootballAnalyticsPro\FootballAnalyticsBootstrap.exe") -Force

    Write-Host "Aplicación lista: $(Join-Path $distRoot 'FootballAnalyticsPro\FootballAnalyticsPro.exe')"
    Write-Host "El archivo .env no se incluye en el paquete. Se lee desde la carpeta del proyecto."
}
finally {
    Pop-Location
}
