# Fuentes y proveedores

La lista está limitada a football-data.org, OpenLigaDB y OpenFootball. Los dominios permitidos son `api.football-data.org`, `api.openligadb.de` y `raw.githubusercontent.com`.

Prioridad general: football-data.org, OpenLigaDB y OpenFootball. Para Europa League: OpenLigaDB y OpenFootball. Una fuente secundaria no sobrescribe un dato primario; el resolver debe registrar conflictos.

football-data.org se configura con `FOOTBALL_DATA_TOKEN` y el header `X-Auth-Token`. No se incluye el token en código. El adaptador implementa endpoints de competiciones, equipos, partidos y tablas para los códigos permitidos por la especificación. No consume estadísticas avanzadas ni promete datos en vivo.

OpenLigaDB no requiere clave. El bootstrap resuelve el shortcut desde el catálogo de la temporada y lo guarda en `source_competition_seasons`; el código no fija un shortcut de Europa League. Por ejemplo, el catálogo actual ofrece `uel2026` para «Europa League 2026/27». La resolución exige un nombre exacto (con sufijo de temporada opcional), para evitar confundir una fase eliminatoria histórica con una temporada completa. Los endpoints y formatos vigentes están descritos en el [OpenAPI oficial de OpenLigaDB](https://api.openligadb.de/swagger/v1/swagger.json).

Durante la sincronización incremental se consulta `getlastchangedate` por jornada. OpenLigaDB puede responder HTTP 404 cuando una jornada todavía no tiene marcador de modificación; el adaptador omite esa jornada y sigue con las demás. El 404 queda contabilizado como jornada omitida, no como fallo de la competición. Otros errores HTTP siguen registrándose como errores. El bootstrap y la reconciliación completa descargan todos los partidos de la temporada en vez de consultar marcadores por jornada. OpenLigaDB publica los datos bajo ODbL 1.0; la atribución se muestra en Herramientas junto al estado de las fuentes.

OpenFootball está reservado para fallback e histórico, no para actualización diaria. El adapter lee datasets JSON permitidos desde `raw.githubusercontent.com`; el mapping de archivo por competición se guarda en `source_competitions`. La importación manual está limitada a Premier League, LaLiga, Serie A, Bundesliga, Ligue 1 y Eredivisie. Para evitar horas UTC falsas, cada competición usa una zona horaria explícita. Un array `score` sin estructura `ft` no se interpreta como resultado final.

Después de aplicar el esquema, `python scripts/bootstrap.py --catalog-only` siembra el catálogo y los mappings sin llamadas externas. Un usuario puede importar manualmente una temporada con `python scripts/import_openfootball.py --competition PL --season 2025`. La importación respeta la prioridad de fuentes, registra discrepancias en calidad de datos y deja el resultado en el historial de sincronización. OpenFootball no se ejecuta en las sincronizaciones diarias ni reemplaza datos de mayor prioridad.
