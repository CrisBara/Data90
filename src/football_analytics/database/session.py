from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from football_analytics.config.settings import get_settings


def make_engine(*, url: str | None = None, echo: bool = False) -> Engine:
    database_url = url or get_settings().database_url
    return create_engine(database_url, echo=echo, pool_pre_ping=True)


def make_session_factory(engine: Engine | None = None) -> sessionmaker:
    return sessionmaker(bind=engine or make_engine(), expire_on_commit=False)
