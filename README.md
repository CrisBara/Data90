# Data90

Aplicación de escritorio local para consultar y analizar partidos, resultados, competiciones, equipos, tablas y métricas objetivas de clubes. La versión de escritorio usa PySide6 y PostgreSQL local. También incluye una vista web de prueba, adaptable y de solo lectura, para recorrer los mismos datos desde un navegador.

No es un sitio de noticias, livescore profesional ni una aplicación de apuestas. No incluye noticias, vídeo, cuotas, pronósticos, selecciones nacionales, anuncios ni scraping.

## Estado actual

Base funcional en desarrollo: catálogos, fixtures, resultados, tablas, planteles y fichas de jugador. La sincronización guarda estadísticas de Highlightly por partido, reconstruye totales por temporada y va completando perfiles dentro del límite diario de la cuenta. La tarea de sincronización de Windows está configurada diariamente a las 21:00. El ejecutable también permite iniciar una actualización completa desde su encabezado y seguir sus fuentes y pasos.

Consulta el último estado medido por competición en [la auditoría del 7 de octubre de 2026](docs/audit-2026-10-07.md).

## Arquitectura

Los providers convierten respuestas externas a DTOs normalizados. La sincronización validará y resolverá esos DTOs antes de persistirlos por medio de repositorios. Servicios y analytics leerán de PostgreSQL; la UI nunca llamará APIs. NULL representa un dato desconocido o no disponible.

Consulta [docs/architecture.md](docs/architecture.md), [docs/database.md](docs/database.md) y [docs/providers.md](docs/providers.md).

## Requisitos

- Windows 11 x64
- Python 3.14 x64
- PostgreSQL 18 local, escuchando solo en localhost
- Un token de football-data.org en `FOOTBALL_DATA_TOKEN` para sincronizar su cobertura
- Una clave gratuita de Highlightly en `HIGHLIGHTLY_API_KEY` para estadísticas por partido y perfiles de jugadores
- Una clave gratuita de API-Football en `API_FOOTBALL_TOKEN` (opcional; su plan actual no habilita temporada 2026/27)
- Una clave de PitchAPI en `PITCHAPI_API_KEY` (candidata en evaluación; todavía no activa la sincronización)

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

`python scripts/bootstrap.py` aplica el esquema, siembra catálogos y carga temporadas activas, equipos, calendario, resultados y tablas. `python scripts/sync_daily.py` actualiza la ventana diaria y los domingos corre además una reconciliación completa. También sincroniza estadísticas de jugadores de Promiedos y Highlightly; este último distribuye calendario, box scores y perfiles entre días para respetar la cuota gratuita. Para iniciar manualmente la importación de Highlightly: `python scripts/import_highlightly_match_statistics.py`. Los jugadores y sus datos de Eredivisie se obtienen del feed de ESPN Fantasy Voetbal. Los campos no cubiertos por las fuentes permanecen como desconocidos.

Para importar el archivo histórico público disponible de las seis ligas nacionales desde 2010: `python scripts/import_openfootball_history.py --from-year 2010 --to-year 2025`. El proceso es idempotente, registra cada temporada y deja fuera los registros que no incluyen una fecha y hora utilizables. La pantalla Jugadores muestra los goleadores que publican las competiciones cubiertas por OpenLigaDB; los campos que la fuente no publica quedan vacíos.

Para ampliar los goleadores históricos que OpenLigaDB publique: `python scripts/import_openligadb_scorer_history.py --from-year 2010 --to-year 2025`. El importador exige coincidencias únicas de competición y registra los años sin cobertura, sin asignar jugadores a clubes por conjetura.

Para importar los eventos de gol actuales de OpenLigaDB que se puedan asociar sin ambigüedad a partidos locales: `python scripts/import_openligadb_match_events.py --season-year 2026`. La pantalla Jugadores incluye la tabla de goles por partido cuando la fuente publica esos eventos.

Para abrir la aplicación local: `python -m data90`. Todas las pantallas consultan los datos locales; las llamadas a proveedores ocurren durante la actualización completa manual, la tarea diaria o bootstrap. La actualización manual ejecuta el mismo proceso que la tarea diaria, vuelve a consultar estadísticas individuales y guarda su registro en `logs/manual_sync.log`.

Para abrir la vista web en esta PC, ejecuta `python -m data90.web` y visita `http://127.0.0.1:8765`. No requiere Node.js ni modifica ni sincroniza la base. La vista web y sus requisitos para preparar un entorno de testers están en [docs/web-preview.md](docs/web-preview.md). El servidor de desarrollo queda limitado a la propia PC; no se publicó una URL.

Para generar el paquete de Windows, ejecuta `powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1`. El ejecutable queda en `dist\Data90\Data90.exe`; mantené `.env` en la carpeta raíz del proyecto para que la aplicación encuentre las credenciales sin incluirlas en el paquete. PostgreSQL debe estar instalado y ejecutándose localmente.

En una base recién creada, la aplicación muestra un asistente para preparar el esquema, sembrar catálogos y cargar datos iniciales. El proceso corre en segundo plano; si una fuente falla, las demás pueden dejar datos disponibles y la app muestra el resultado parcial.

## Calidad

```powershell
pytest
ruff check .
```

Los tests de integración usan PostgreSQL local; los tests unitarios deben usar fixtures sintéticos sin Internet.
