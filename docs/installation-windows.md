# Instalación en Windows 11

1. Instala Python 3.14 x64 y PostgreSQL 18 localmente.
2. Crea el entorno virtual, instala `pip install -e ".[dev]"` y copia `.env.example` a `.env` si todavía no existe. La base de la app se llama `football_analytics_pro` para no colisionar con una base antigua llamada `football_analytics`.
3. Configura las variables locales de `.env`. Ejecuta `scripts/configure_postgresql_local.ps1` desde PowerShell como Administrador. El script crea o actualiza el rol `football_app` con permisos limitados y la base configurada en `DATABASE_NAME`; usa autenticación temporal solo en localhost durante la operación y restaura SCRAM al terminar. La contraseña de aplicación sale de `.env`.
4. Comprueba PostgreSQL con `python scripts/healthcheck.py` y aplica el bootstrap con `python scripts/bootstrap.py`.

Al abrir la aplicación con la base vacía o sin datos iniciales, se muestra un asistente. Comprueba PostgreSQL, informa si el token está configurado sin mostrarlo y ejecuta migraciones, catálogos y bootstrap en segundo plano. Si el bootstrap queda parcial por una fuente externa, la aplicación puede abrirse para consultar los datos disponibles y revisar Herramientas.

`scripts/register_daily_task.ps1` crea o actualiza la tarea diaria de Windows según `SYNC_TIME` en `.env`; los resultados se guardan en `logs/scheduled_sync.log`. La tarea corre en la sesión de Bara, se despierta a la hora programada si Windows lo permite y recupera una ejecución omitida cuando la PC vuelve a estar disponible. Windows debe estar encendido o volver a encenderse y la sesión del usuario debe estar iniciada; sin iniciar sesión, Windows no puede usar las credenciales de las fuentes. La tarea `FootballAnalyticsPro-DailySync` está configurada para las 21:00, hora local de Buenos Aires. El servicio PostgreSQL 18 está configurado para inicio automático. No se abre ningún puerto público.
