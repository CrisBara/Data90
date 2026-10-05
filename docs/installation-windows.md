# Instalación en Windows 11

1. Instala Python 3.14 x64 y PostgreSQL 18 localmente.
2. Crea el entorno virtual, instala `pip install -e ".[dev]"` y copia `.env.example` a `.env` si todavía no existe. La base de la app se llama `football_analytics_pro` para no colisionar con una base antigua llamada `football_analytics`.
3. Configura las variables locales de `.env`. Ejecuta `scripts/configure_postgresql_local.ps1` desde PowerShell como Administrador. El script crea o actualiza el rol `football_app` con permisos limitados y la base configurada en `DATABASE_NAME`; usa autenticación temporal solo en localhost durante la operación y restaura SCRAM al terminar. La contraseña de aplicación sale de `.env`.
4. Comprueba PostgreSQL con `python scripts/healthcheck.py` y aplica el bootstrap con `python scripts/bootstrap.py`.

Al abrir la aplicación con la base vacía o sin datos iniciales, se muestra un asistente. Comprueba PostgreSQL, informa si el token está configurado sin mostrarlo y ejecuta migraciones, catálogos y bootstrap en segundo plano. Si el bootstrap queda parcial por una fuente externa, la aplicación puede abrirse para consultar los datos disponibles y revisar Herramientas.

`scripts/register_daily_task.ps1` prepara una tarea diaria en el Programador de tareas para la hora `SYNC_TIME` de `.env`; los resultados se guardan en `logs/scheduled_sync.log`. En la instalación de desarrollo, la tarea queda asociada a la sesión del usuario: Windows debe estar encendido y el usuario debe tener la sesión iniciada a la hora programada. Si la PC estaba apagada o la tarea se demoró, `StartWhenAvailable` permite que se ejecute cuando vuelva a estar disponible. La tarea `FootballAnalyticsPro-DailySync` ya está registrada para las 08:00, hora local de Windows. No se abre ningún puerto público.
