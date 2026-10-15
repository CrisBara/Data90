# Revisión de escudos y logos

Auditoría realizada el 8 de octubre de 2026 para el rediseño del Inicio y la vista de Premier League.

## Premier League

- La temporada activa tiene 20 equipos y los 20 registros tienen una URL de escudo en PostgreSQL.
- La aplicación usa esas URLs y conserva el formato de imagen sin deformarlo.
- Las URLs están presentes; no se hizo una inspección visual manual independiente de cada insignia.

## Catálogo más amplio

- La base contiene equipos históricos y de copas además de los clubes activos de liga. Muchos registros antiguos no tienen URL de escudo; la interfaz muestra un marcador neutral y no lo presenta como oficial.
- Las comprobaciones de carga de imágenes registran faltantes y errores en `%APPDATA%/Football Analytics Pro/team-crest-review.txt`.
- En la revisión de carga aparecieron URLs no válidas para Bayer 04 Leverkusen y NK Celje. No se reemplazaron por imágenes no verificadas.
- También faltaban logos de UEFA Europa League en el selector; se agregó el recurso `EL.png`, que es el código de logo documentado por football-data.org para esa competición.

## Alcance de la verificación

El conteo anterior comprueba presencia de URL, no la identidad visual de cada archivo. Los recursos con fallos permanecen identificables por el registro de ejecución y se ven con marcador neutral hasta contar con una fuente comprobada.
