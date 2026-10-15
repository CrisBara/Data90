"""Create the local application role and database using PostgreSQL admin credentials."""

import os
from getpass import getpass

import psycopg
from psycopg import sql

from data90.config.settings import get_settings


def main() -> int:
    settings = get_settings()
    admin_user = os.environ.get("POSTGRES_ADMIN_USER", "postgres")
    app_password = settings.database_password.get_secret_value()
    if not app_password:
        print("DATABASE_PASSWORD falta en .env.")
        return 1

    connection_options = {
        "host": settings.database_host,
        "port": settings.database_port,
        "dbname": "postgres",
        "user": admin_user,
        "autocommit": True,
    }
    try:
        try:
            connection = psycopg.connect(**connection_options)
        except psycopg.OperationalError:
            connection = psycopg.connect(
                **connection_options,
                password=getpass("PostgreSQL administrator password: "),
            )
        with connection:
            role_exists = connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (settings.database_user,)
            ).fetchone()
            role_statement = sql.SQL(
                "ALTER ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                if role_exists
                else "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(
                sql.Identifier(settings.database_user),
                sql.Literal(app_password),
            )
            connection.execute(role_statement)

            database_exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (settings.database_name,)
            ).fetchone()
            if database_exists:
                connection.execute(
                    sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                        sql.Identifier(settings.database_name),
                        sql.Identifier(settings.database_user),
                    )
                )
            else:
                connection.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(settings.database_name),
                        sql.Identifier(settings.database_user),
                    )
                )
        with psycopg.connect(
            **(connection_options | {"dbname": settings.database_name})
        ) as application_database:
            objects = application_database.execute(
                """
                SELECT relation.relkind, namespace.nspname, relation.relname
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND pg_get_userbyid(relation.relowner) = %s
                ORDER BY relation.relkind, relation.relname
                """,
                (admin_user,),
            ).fetchall()
            alter_command = {
                "r": "ALTER TABLE {}.{} OWNER TO {}",
                "p": "ALTER TABLE {}.{} OWNER TO {}",
                "v": "ALTER VIEW {}.{} OWNER TO {}",
                "m": "ALTER MATERIALIZED VIEW {}.{} OWNER TO {}",
                "f": "ALTER FOREIGN TABLE {}.{} OWNER TO {}",
            }
            for relation_kind, schema_name, relation_name in objects:
                application_database.execute(
                    sql.SQL(alter_command[relation_kind]).format(
                        sql.Identifier(schema_name),
                        sql.Identifier(relation_name),
                        sql.Identifier(settings.database_user),
                    )
                )
            application_database.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                    sql.Identifier(settings.database_user)
                )
            )
        print(
            f"Rol {settings.database_user} y base {settings.database_name} "
            "configurados en PostgreSQL local."
        )
        return 0
    except psycopg.OperationalError as error:
        print(f"No se pudo autenticar o conectar a PostgreSQL: {error.__class__.__name__}.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
