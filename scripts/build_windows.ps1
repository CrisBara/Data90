$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distRoot = Join-Path $projectRoot "dist"
$buildRoot = Join-Path $projectRoot "build\pyinstaller"

if (-not (Test-Path -LiteralPath $python)) {
    throw "No se encontró el Python del entorno del proyecto: $python"
}

Push-Location $projectRoot
try {
    & $python -c "import PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        & $python -m pip install -e ".[build]"
        if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias de empaquetado." }
    }

    $appDir = Join-Path $distRoot "Data90"
    $nuitkaOutput = Join-Path $distRoot "Data90.dist"
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

    $common = @(
        "--noconfirm",
        "--clean",
        "--paths", "src",
        "--collect-data", "data90",
        "--collect-submodules", "alembic",
        "--hidden-import", "logging.config",
        "--hidden-import", "psycopg",
        "--hidden-import", "psycopg_binary",
        "--hidden-import", "psycopg_c",
        "--distpath", $distRoot,
        "--workpath", $buildRoot
    )

    & $python -m PyInstaller --noconfirm --clean --distpath $distRoot `
        --workpath $buildRoot (Join-Path $projectRoot "Data90.spec")
    if ($LASTEXITCODE -ne 0) { throw "Falló el empaquetado de Data90." }

    & $python -m PyInstaller @common --onefile --console --name FootballAnalyticsBootstrap `
        --add-data "alembic.ini;." --add-data "alembic;alembic" scripts\bootstrap_entry.py
    if ($LASTEXITCODE -ne 0) { throw "Falló la generación del asistente de inicialización." }

    $bootstrapPath = Join-Path $distRoot "FootballAnalyticsBootstrap.exe"
    Move-Item -LiteralPath $bootstrapPath `
        -Destination (Join-Path $distRoot "Data90\Data90Bootstrap.exe") -Force

    Write-Host "Aplicación lista: $(Join-Path $distRoot 'Data90\Data90.exe')"
    Write-Host "El archivo .env no se incluye en el paquete. Se lee desde la carpeta del proyecto."
}
finally {
    Pop-Location
}
