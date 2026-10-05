# Football Analytics Pro

Aplicación de escritorio local para consultar y analizar partidos, resultados, competiciones, equipos, tablas y métricas objetivas de clubes. La primera versión usa PostgreSQL local y conserva datos históricos. La interfaz será PySide6; FastAPI queda fuera de v1.

No es un sitio de noticias, livescore profesional ni una aplicación de apuestas. No incluye noticias, vídeo, cuotas, pronósticos, selecciones nacionales, anuncios ni scraping.

## Estado actual

Base funcional en desarrollo: configuración tipada, catálogos seed, DTOs neutrales, modelos SQLAlchemy, migración inicial, adaptadores de football-data.org, OpenLigaDB y OpenFootball, bootstrap por competición, resolución de equipos, UPSERT idempotente, snapshots de tablas y consultas locales. La interfaz incluye Inicio, Competiciones con detalle y tablas, Equipos con historial y métricas, Estadísticas por competición/temporada y Herramientas para estado, atribución, calidad e historial de sincronización. La tarea diaria de Windows está registrada a las 08:00 en el equipo de desarrollo. El empaquetado distribuible sigue pendiente.

## Arquitectura

Los providers convierten respuestas externas a DTOs normalizados. La sincronización validará y resolverá esos DTOs antes de persistirlos por medio de repositorios. Servicios y analytics leerán de PostgreSQL; la UI nunca llamará APIs. NULL representa un dato desconocido o no disponible.

Consulta [docs/architecture.md](docs/architecture.md), [docs/database.md](docs/database.md) y [docs/providers.md](docs/providers.md).

## Requisitos

- Windows 11 x64
- Python 3.14 x64
- PostgreSQL 18 local, escuchando solo en localhost
- Un token de football-data.org en `FOOTBALL_DATA_TOKEN` para sincronizar su cobertura

No se usa Docker ni Node.js.

## Instalación de desarrollo

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Configura la base aislada `football_analytics_pro` y el usuario local de mínimo privilegio `football_app` según [docs/installation-windows.md](docs/installation-windows.md). Completa `.env` con la contraseña local de la aplicación y el token del proveedor; nunca guardes esos valores en Git.

## Bootstrap y ejecución

`python scripts/bootstrap.py` aplica el esquema, seed de catálogos y luego intenta cargar temporadas activas, equipos, calendario, resultados y tablas. Cada competición se registra por separado; si falla una, las demás continúan y el comando termina con resultado parcial. `python scripts/sync_daily.py` actualiza la ventana diaria y los domingos corre además una reconciliación completa.

Para abrir la aplicación local: `python -m football_analytics`. Todas las pantallas consultan los datos locales; las llamadas a proveedores solo ocurren durante la sincronización manual o bootstrap.

## Calidad

```powershell
pytest
ruff check .
```

Los tests de integración usan PostgreSQL local; los tests unitarios deben usar fixtures sintéticos sin Internet.
