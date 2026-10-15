# Base de datos

PostgreSQL local, base `data90_local`, usuario runtime `football_app` sin privilegios administrativos. El esquema SQLAlchemy cubre fuentes, competiciones y temporadas, equipos y alias, mappings externos, partidos, estadísticas opcionales, eventos, snapshots de tablas, ejecuciones de sincronización y problemas de calidad. La instalación deja las bases preexistentes sin borrar.

Las estadísticas opcionales son nullable: `NULL` significa desconocido/no disponible y no debe transformarse en cero. Las restricciones únicas en los mappings por fuente evitan duplicar entidades importadas. La identidad del partido se conserva al corregir su horario.

Alembic usa `alembic/versions/0001_initial_schema.py` para la estructura inicial y `0002_match_query_indexes.py` para los índices de consultas por temporada y por equipo. La segunda migración sustituye índices simples redundantes por índices compuestos y es compatible tanto con la base existente como con instalaciones nuevas. No se deben borrar temporadas ni equipos históricos como parte de una sincronización normal.
