# Auditoría de escudos — 8 de octubre de 2026

## Alcance revisado

Se inspeccionaron los **186 registros de equipos** usados en la temporada activa de las ocho competiciones cargadas: Premier League, Bundesliga, Eredivisie, LaLiga, Ligue 1, Serie A, Champions League y Europa League. En ese conjunto la base local no tiene URLs vacías, pero sí había URLs HTTP que la app rechazaba, enlaces de imagen que fallaban, miniaturas y dos escudos incrustados como `data:` que el cargador no acepta.

## Correcciones aplicadas

- Athletic Club: reemplazo del enlace HTTP por el escudo de football-data.org; también se reconocen variantes de nombre como «Athletic Bilbao» y el error «Atletic Club de España».
- Bundesliga: Bayer 04 Leverkusen (URL que fallaba), Borussia Mönchengladbach, Eintracht Frankfurt, FC Augsburg, FC Bayern München (escudo anterior a 2017), RB Leipzig, SC Freiburg, VfB Stuttgart y TSG Hoffenheim.
- LaLiga: CA Osasuna, Elche CF, FC Barcelona, RC Celta de Vigo, Real Sociedad y Levante UD.
- Europa League: Benfica Lissabon y Sparta Prag dejaron de depender de imágenes `data:` que no se podían cargar.
- Revisión adicional solicitada: Deportivo Alavés y su registro alternativo CD Alavés; FC Schalke 04 (también corrige «Shalke 04»); AZ Alkmaar (también corrige «AZ Alkmar»); y NK Celje con el archivo actualizado de su escudo.
- Premier League: se mantienen y aplican globalmente los overrides ya existentes para Chelsea, Crystal Palace, Newcastle United y Tottenham Hotspur.

Los escudos se resuelven en un único cargador y se usan tanto en botones de clubes como en las marcas de agua del calendario. El resto de URLs de proveedores se conserva cuando no hay una señal comprobable de error; la app muestra un marcador neutral ante una imagen que no pueda cargar, sin sustituirla por un escudo inventado.

## Pendiente fuera de la temporada activa

El catálogo histórico contiene clubes de otras temporadas. Una consulta de solo lectura encontró **137 equipos históricos** sin URL de escudo en la base; no se les asignó una imagen por nombre aproximado. Esos registros no forman parte de los 186 equipos revisados en las temporadas activas y requieren una pasada separada para asociarlos a identificadores de equipo inequívocos.

## Verificación realizada

- Se contrastaron los identificadores de football-data.org en el catálogo local antes de construir sus URLs de escudo. La documentación oficial muestra el campo `crest` como URL de imagen del equipo.
- Se comprobó que el módulo compila y que nueve nombres/variantes problemáticos resuelven a URLs HTTPS.
- Se reconstruyó `dist/FootballAnalyticsPro/FootballAnalyticsPro.exe` con Python 3.14.7.
- La verificación del aspecto final de cada imagen depende de que el proveedor de imágenes esté accesible desde el equipo cuando la app las solicita.
