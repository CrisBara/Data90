# Fuentes y proveedores

La lista está limitada a football-data.org, OpenLigaDB y OpenFootball. Los dominios permitidos son `api.football-data.org`, `api.openligadb.de` y `raw.githubusercontent.com`.

Prioridad general: football-data.org, OpenLigaDB y OpenFootball. Para Europa League: OpenLigaDB y OpenFootball. Una fuente secundaria no sobrescribe un dato primario; el resolver debe registrar conflictos.

football-data.org se configura con `FOOTBALL_DATA_TOKEN` y el header `X-Auth-Token`. No se incluye el token en código. El adaptador implementa endpoints de competiciones, equipos, partidos y tablas para los códigos permitidos por la especificación. No consume estadísticas avanzadas ni promete datos en vivo.

OpenLigaDB no requiere clave. El bootstrap resuelve su shortcut por nombre exacto en el catálogo de la temporada y lo guarda en `source_competition_seasons`; las sincronizaciones posteriores leen ese mapping. El adapter no fija un shortcut de Europa League. Las sincronizaciones diarias consultan `getlastchangedate` y descargan solo las jornadas modificadas. OpenLigaDB publica los datos bajo ODbL 1.0; la atribución se muestra en Herramientas junto al estado de las fuentes.

OpenFootball está reservado para fallback e histórico, no para actualización diaria. El adapter lee datasets JSON permitidos desde `raw.githubusercontent.com`; el mapping de archivo por competición se guarda en `source_competitions`. La importación manual está limitada a Premier League, LaLiga, Serie A, Bundesliga, Ligue 1 y Eredivisie. Para evitar horas UTC falsas, cada competición usa una zona horaria explícita. Un array `score` sin estructura `ft` no se interpreta como resultado final.

Después de aplicar el esquema, `python scripts/bootstrap.py --catalog-only` siembra el catálogo y los mappings sin llamadas externas. Un usuario puede importar manualmente una temporada con `python scripts/import_openfootball.py --competition PL --season 2025`. La importación respeta la prioridad de fuentes, registra discrepancias en calidad de datos y deja el resultado en el historial de sincronización. OpenFootball no se ejecuta en las sincronizaciones diarias ni reemplaza datos de mayor prioridad.
