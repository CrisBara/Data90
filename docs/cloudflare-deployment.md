# Publicación web de Data90

La aplicación de escritorio sigue usando PostgreSQL local. La vista pública usa una copia independiente de la base y una instancia de solo lectura. No se publica la base de esta PC ni se copian claves de proveedores.

## Arquitectura

- Cloudflare Workers ejecuta la vista web Python.
- Cloudflare Hyperdrive conecta el Worker con PostgreSQL. La integración de Hyperdrive con Python Workers todavía está en beta.
- Una base PostgreSQL alojada contiene solo las tablas de la aplicación y sus datos deportivos.
- La ruta pública no ofrece sincronización; solo atiende consultas `GET`.

## Preparar la copia de PostgreSQL

Usa una base nueva, como `data90_public`, y restaura allí una exportación de la base local. Excluye los datos operativos locales para no publicar cursores, intentos, registros ni problemas de sincronización:

```powershell
pg_dump --format=custom --no-owner --no-acl `
  --exclude-table-data=public.app_settings `
  --exclude-table-data=public.data_quality_issues `
  --exclude-table-data=public.data_sources `
  --exclude-table-data=public.source_competition_seasons `
  --exclude-table-data=public.source_competitions `
  --exclude-table-data=public.source_matches `
  --exclude-table-data=public.source_players `
  --exclude-table-data=public.source_teams `
  --exclude-table-data=public.sync_run_items `
  --exclude-table-data=public.sync_runs `
  --file=data90-public.dump
```

Pasa la conexión local con variables de entorno de proceso; no escribas la contraseña en el comando, en un archivo del proyecto ni en Git. Conserva el `.dump` fuera del repositorio y elimínalo de forma segura después de restaurarlo y validar la copia alojada.

En la base alojada, crea un usuario distinto para el sitio y dale únicamente lectura:

```sql
CREATE ROLE data90_public_ro LOGIN PASSWORD 'GENERATE_A_UNIQUE_PASSWORD';
GRANT CONNECT ON DATABASE data90_public TO data90_public_ro;
GRANT USAGE ON SCHEMA public TO data90_public_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO data90_public_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO data90_public_ro;
```

El usuario dueño de la importación no se usa desde el Worker. Configura Hyperdrive con la dirección de conexión de `data90_public_ro`; Hyperdrive guarda la conexión del lado de Cloudflare. Desactiva el almacenamiento en caché de consultas para que las actualizaciones futuras no queden ocultas por resultados viejos.

## Preparar Cloudflare

1. Copia `wrangler.toml.example` a `wrangler.toml`.
2. Crea una configuración Hyperdrive en Cloudflare con la conexión de solo lectura y pon su identificador en `wrangler.toml`.
3. Instala `uv` y Node.js, luego instala el grupo de herramientas Workers:

   ```powershell
   uv sync --group workers
   ```

4. Inicia una vista previa y confirma que la página y `/api/health` leen la copia alojada. Para desarrollo local, configura `localConnectionString` fuera del archivo seguido por Git; nunca uses el `.env` de escritorio como configuración del Worker.
5. Despliega con `uv run pywrangler deploy`.

El Worker serializa las consultas síncronas para ajustarse al entorno Python Workers. El entorno de producción debe usar la cadena de Hyperdrive y nunca la conexión PostgreSQL local.
