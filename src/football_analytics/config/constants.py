"""Seed data and provider configuration for the supported competitions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompetitionDefinition:
    code: str
    name: str
    country_name: str | None
    region_name: str | None
    competition_type: str
    display_order: int
    provider_codes: tuple[tuple[str, str], ...]


COMPETITIONS = (
    CompetitionDefinition(
        "PL",
        "Premier League",
        "England",
        None,
        "LEAGUE",
        1,
        (("football-data", "PL"), ("openfootball", "en.1.json")),
    ),
    CompetitionDefinition(
        "LALIGA",
        "LaLiga",
        "Spain",
        None,
        "LEAGUE",
        2,
        (("football-data", "PD"), ("openfootball", "es.1.json")),
    ),
    CompetitionDefinition(
        "SERIE_A",
        "Serie A",
        "Italy",
        None,
        "LEAGUE",
        3,
        (("football-data", "SA"), ("openfootball", "it.1.json")),
    ),
    CompetitionDefinition(
        "BUNDESLIGA",
        "Bundesliga",
        "Germany",
        None,
        "LEAGUE",
        4,
        (("football-data", "BL1"), ("openfootball", "de.1.json")),
    ),
    CompetitionDefinition(
        "LIGUE_1",
        "Ligue 1",
        "France",
        None,
        "LEAGUE",
        5,
        (("football-data", "FL1"), ("openfootball", "fr.1.json")),
    ),
    CompetitionDefinition(
        "EREDIVISIE",
        "Eredivisie",
        "Netherlands",
        None,
        "LEAGUE",
        6,
        (("football-data", "DED"), ("openfootball", "nl.1.json")),
    ),
    CompetitionDefinition(
        "UCL", "UEFA Champions League", None, "Europe", "CUP", 7, (("football-data", "CL"),)
    ),
    CompetitionDefinition(
        "UEL", "UEFA Europa League", None, "Europe", "CUP", 8, (("openligadb", ""),)
    ),
)

SOURCES = (
    {
        "code": "football-data",
        "name": "football-data.org",
        "base_url": "https://api.football-data.org/v4",
        "priority": 1,
        "requires_api_key": True,
        "license_name": "football-data.org Free Tier",
        "attribution_text": "Datos de fútbol proporcionados por football-data.org",
        "terms_url": "https://www.football-data.org/coverage",
    },
    {
        "code": "openligadb",
        "name": "OpenLigaDB",
        "base_url": "https://api.openligadb.de",
        "priority": 2,
        "requires_api_key": False,
        "license_name": "Open Database License (ODbL) 1.0",
        "attribution_text": "Datos de partidos de OpenLigaDB, mantenidos por la comunidad.",
        "terms_url": "https://www.openligadb.de/lizenz",
    },
    {
        "code": "openfootball",
        "name": "OpenFootball",
        "base_url": "https://raw.githubusercontent.com",
        "priority": 3,
        "requires_api_key": False,
        "license_name": "OpenFootball",
        "attribution_text": "Datos históricos de OpenFootball.",
        "terms_url": "https://github.com/openfootball",
    },
)
