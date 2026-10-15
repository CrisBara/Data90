# Vista web de prueba

Football Analytics Pro conserva su ejecutable de escritorio. La vista web es un cliente adicional y de solo lectura: usa los servicios y consultas existentes para leer PostgreSQL, no llama a proveedores desde el navegador y no ejecuta sincronizaciones.

## Ejecutar en esta PC

1. Iniciá PostgreSQL y confirmá que existe la base local habitual.
2. Desde la carpeta del repositorio, activá el entorno Python del proyecto.
3. Iniciá el servidor de prueba:

   ```powershell
   python -m football_analytics.web
   ```

4. Abrí `http://127.0.0.1:8765`.

El servidor de desarrollo escucha solo en `127.0.0.1`; otros dispositivos no pueden entrar por esa dirección. Para detenerlo, usá `Ctrl+C` en la terminal.

## Qué se puede recorrer

- Inicio con calendario de seis días, partido destacado por las prioridades existentes y fixture del día agrupado por competición.
- Fichas de competiciones con selector de temporada, tabla, estadísticas de equipos, partidos, próximos partidos, goleadores y eliminatorias de Champions/Europa League.
- Fichas de equipos con plantel, partidos, calendario, rendimiento, estadísticas de partido y clásicos cuando existen en los datos.
- Búsqueda global de equipos y jugadores, con accesos entre las fichas.
- Ficha individual del jugador con datos y estadísticas publicados por competición y temporada.

La interfaz es adaptable a pantallas pequeñas. Los escudos usan las URL asociadas a los registros actuales; si un recurso no carga, aparece un marcador neutral en lugar de otro escudo.

## Preparar un entorno compartido

No publiques la base local ni expongas PostgreSQL a Internet. Para un grupo de testers, desplegá la aplicación en un servidor de prueba con **una copia controlada de la base** y un usuario PostgreSQL exclusivo con permiso `SELECT` únicamente sobre las tablas necesarias. La URL de conexión queda en `FAP_WEB_DATABASE_URL`, configurada en el entorno privado del servidor; no se escribe en HTML, JavaScript, repositorio público ni URL compartida.

La aplicación fuerza transacciones PostgreSQL de solo lectura y solo atiende rutas `GET`. Eso añade protección en la aplicación; el usuario de base de datos también debe ser de solo lectura. No conectes este servicio a la base productiva con el rol escritor de escritorio.

Para servirla fuera de la computadora local:

1. Instalá el paquete del proyecto y un servidor WSGI de producción compatible con Windows/Linux, por ejemplo Waitress (`pip install waitress`).
2. Configurá en el servidor de prueba `FAP_WEB_DATABASE_URL` con la base copiada y el usuario de solo lectura. Definí `FAP_WEB_ALLOWED_HOSTS` con el dominio de prueba exacto.
3. Ejecutá el objeto WSGI `football_analytics.web:application` ligado solo a loopback, por ejemplo:

   ```powershell
   waitress-serve --host=127.0.0.1 --port=8765 football_analytics.web:application
   ```

4. Poné delante un proxy HTTPS con autenticación o acceso restringido a los testers. El proxy debe terminar TLS; no publiques el puerto WSGI ni el puerto PostgreSQL. Mantené las claves de API fuera de esa instancia si no son necesarias para la lectura.

El servidor integrado con `python -m football_analytics.web` es solo para desarrollo local; no está pensado para recibir conexiones públicas. No se desplegó ni publicó esta vista: para compartir un enlace real todavía hace falta configurar el host de prueba, la copia de datos, el dominio y el acceso de testers.

## Controles de seguridad

- No hay endpoints de escritura ni de sincronización.
- Las consultas API abren transacciones de solo lectura.
- El acceso desde el servidor integrado se limita a loopback, salvo configuración remota explícita.
- Se rechazan métodos distintos de `GET` y hosts no incluidos en `FAP_WEB_ALLOWED_HOSTS`.
- La interfaz usa recursos JavaScript/CSS locales y una política CSP que bloquea scripts externos e iframes.
- Los errores API no devuelven detalles de base de datos ni credenciales.
- Los datos de muestra de los mockups no se incorporan a la base ni a las vistas.
