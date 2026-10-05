import pytest
from pydantic import ValidationError

from football_analytics.config.settings import Settings


def test_database_host_must_remain_local() -> None:
    settings = Settings(database_host="LOCALHOST")
    assert settings.database_host == "localhost"
    with pytest.raises(ValidationError, match="equipo local"):
        Settings(database_host="db.example.org")


def test_sync_time_and_timezone_are_validated() -> None:
    assert Settings(sync_time="8:00").sync_time == "08:00"
    with pytest.raises(ValidationError):
        Settings(sync_time="25:00")
    with pytest.raises(ValidationError):
        Settings(app_timezone="Unknown/Place")
