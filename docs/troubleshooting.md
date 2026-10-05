# Solución de problemas

## No se encuentra Python

Instala Python 3.14 x64 y verifica `py -3.14 --version` desde PowerShell.

## PostgreSQL no responde

Comprueba que el servicio local está activo y que `.env` apunta a `localhost`, al puerto configurado, y al rol `football_app`. `scripts/healthcheck.py` no imprime contraseñas.

Al abrir la aplicación aparece una ventana con `Reintentar`, `Ver configuración` y `Salir`. Inicia el servicio PostgreSQL; si cambiaste `.env`, guardá el archivo y elegí `Reintentar`.

## Primera ejecución

Con PostgreSQL disponible, si faltan las migraciones, catálogos o la primera sincronización, se abre el asistente inicial. Elegí `Preparar base y cargar datos` y esperá a que termine. Una ejecución parcial por una fuente externa permite abrir la app; revisá las fuentes y errores en Herramientas.

## Falta o se rechaza el token

Configura `FOOTBALL_DATA_TOKEN` en `.env`. El token no debe copiarse a código, logs, issues ni commits. OpenLigaDB no necesita token.

## Sin Internet

Las pantallas consultan los últimos datos guardados en PostgreSQL y no llaman a proveedores. La sincronización necesita Internet; sin conexión, los datos locales siguen disponibles.
