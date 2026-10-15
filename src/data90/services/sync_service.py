"""Application entry points for provider-backed synchronization."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from data90.config.settings import get_settings
from data90.database.session import make_engine, make_session_factory
from data90.domain.enums import SyncMode, SyncStatus
from data90.providers.espn import ESPNProvider
from data90.providers.football_data import FootballDataProvider
from data90.providers.openligadb import OpenLigaDBProvider
from data90.services.api_football_player_sync import sync_api_football_players
from data90.services.eredivisie_fantasy_sync import sync_eredivisie_fantasy_players
from data90.services.espn_match_statistics_sync import sync_espn_match_statistics
from data90.services.espn_player_roster_sync import sync_espn_player_rosters
from data90.services.football_data_squad_sync import sync_current_football_data_squads
from data90.services.highlightly_match_sync import sync_highlightly_match_statistics
from data90.services.match_event_sync import sync_match_goal_events
from data90.services.pitchapi_match_sync import sync_pitchapi_match_statistics
from data90.services.player_sync import sync_player_statistics
from data90.services.promiedos_match_sync import sync_promiedos_match_fixtures
from data90.services.promiedos_sync import sync_promiedos_player_statistics
from data90.services.promiedos_team_statistics_sync import (
    sync_promiedos_team_statistics,
)
from data90.sync.orchestrator import SyncSummary, run_sync


def run_daily_sync() -> SyncSummary:
    engine = make_engine()
    try:
        factory = make_session_factory(engine)
        summary = run_sync(
            factory,
            {"football-data": FootballDataProvider(), "openligadb": OpenLigaDBProvider()},
            SyncMode.DAILY,
        )
        players = sync_player_statistics(factory)
        events = sync_match_goal_events(factory, mode=SyncMode.DAILY)
        api_players = sync_api_football_players(factory)
        promiedos = sync_promiedos_player_statistics(factory)
        promiedos_team_stats = sync_promiedos_team_statistics(factory)
        promiedos_matches = sync_promiedos_match_fixtures(factory, full_season=False)
        eredivisie = sync_eredivisie_fantasy_players(factory)
        squads = sync_current_football_data_squads(factory)
        espn_rosters = sync_espn_player_rosters(factory)
        highlightly = (
            sync_highlightly_match_statistics(factory)
            if get_settings().highlightly_api_key.get_secret_value()
            else None
        )
        pitchapi = (
            sync_pitchapi_match_statistics(factory)
            if get_settings().pitchapi_api_key.get_secret_value()
            else None
        )
        local_today = datetime.now(UTC).astimezone(ZoneInfo(get_settings().app_timezone)).date()
        match_stats = sync_espn_match_statistics(
            factory,
            provider=ESPNProvider(),
            date_from=local_today - timedelta(days=2),
            date_to=local_today,
        )
        statuses = (
            summary.status,
            players.status,
            events.status,
            api_players.status,
            promiedos.status,
            promiedos_team_stats.status,
            eredivisie["status"],
            squads["status"],
            espn_rosters.status,
            SyncStatus(match_stats.status),
            promiedos_matches.status,
            *((highlightly.status,) if highlightly is not None else ()),
            *((pitchapi.status,) if pitchapi is not None else ()),
        )
        status = (
            SyncStatus.FAILED
            if all(item == SyncStatus.FAILED for item in statuses)
            else SyncStatus.PARTIAL
            if any(item != SyncStatus.SUCCESS for item in statuses)
            else SyncStatus.SUCCESS
        )
        return replace(
            summary,
            status=status,
            requests_count=(
                summary.requests_count
                + players.requests_count
                + events.requests_count
                + api_players.requests_count
                + promiedos.requests_count
                + promiedos_team_stats.requests_count
                + eredivisie["requests_count"]
                + squads["requests_count"]
                + espn_rosters.requests_count
                + match_stats.requests_count
                + promiedos_matches.requests_count
                + (highlightly.requests_count if highlightly else 0)
                + (pitchapi.requests_count if pitchapi else 0)
            ),
            inserted_count=(
                summary.inserted_count
                + players.inserted_count
                + events.inserted_count
                + api_players.inserted_count
                + promiedos.inserted_count
                + promiedos_team_stats.inserted_count
                + eredivisie["inserted_count"]
                + squads["inserted_count"]
                + espn_rosters.identities_inserted
                + match_stats.inserted_count
                + promiedos_matches.inserted_count
                + (highlightly.inserted_count if highlightly else 0)
                + (pitchapi.inserted_count if pitchapi else 0)
            ),
            updated_count=(
                summary.updated_count
                + players.updated_count
                + events.updated_count
                + api_players.updated_count
                + promiedos.updated_count
                + promiedos_team_stats.updated_count
                + eredivisie["updated_count"]
                + squads["updated_count"]
                + espn_rosters.nationality_filled
                + espn_rosters.position_filled
                + espn_rosters.birth_dates_filled
                + match_stats.updated_count
                + promiedos_matches.updated_count
                + (highlightly.updated_count if highlightly else 0)
                + (pitchapi.updated_count if pitchapi else 0)
            ),
            skipped_count=(
                summary.skipped_count
                + players.skipped_count
                + events.skipped_count
                + api_players.skipped_count
                + promiedos.skipped_count
                + promiedos_team_stats.skipped_count
                + eredivisie["skipped_count"]
                + squads["skipped_count"]
                + espn_rosters.skipped_count
                + match_stats.skipped_count
                + promiedos_matches.skipped_count
                + (highlightly.skipped_count if highlightly else 0)
                + (pitchapi.skipped_count if pitchapi else 0)
            ),
            error_count=(
                summary.error_count
                + players.error_count
                + events.error_count
                + api_players.error_count
                + promiedos.error_count
                + promiedos_team_stats.error_count
                + eredivisie["error_count"]
                + squads["error_count"]
                + espn_rosters.error_count
                + match_stats.error_count
                + promiedos_matches.error_count
                + (highlightly.error_count if highlightly else 0)
                + (pitchapi.error_count if pitchapi else 0)
            ),
            message=(
                f"{summary.message}; {players.message}; {events.message}; "
                f"{api_players.message}; {promiedos.message}; {eredivisie['message']}; "
                f"{promiedos_team_stats.message}; "
                f"{squads['message']}; {match_stats.message}"
                f"; {espn_rosters.message}"
                f"; {promiedos_matches.message}"
                f"; {highlightly.message if highlightly else 'Highlightly: sin clave configurada'}"
                f"; {pitchapi.message if pitchapi else 'PitchAPI: sin clave configurada'}"
            ),
        )
    finally:
        engine.dispose()
