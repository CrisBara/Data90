from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from data90.database.session import make_engine


def main() -> int:
    try:
        with make_engine().connect() as connection:
            version = connection.execute(text("SELECT version()")).scalar_one()
        print(f"PostgreSQL OK: {version}")
        return 0
    except SQLAlchemyError as error:
        print(f"PostgreSQL ERROR: {error.__class__.__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
