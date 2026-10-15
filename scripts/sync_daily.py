"""Daily sync and Sunday season reconciliation entry point."""

import argparse
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from football_analytics.config.constants import COMPETITIONS
from football_analytics.config.paths import project_root
from football_analytics.config.settings import get_settings
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.domain.enums import SyncMode
from football_analytics.providers.espn import ESPNProvider
from football_analytics.providers.football_data import FootballDataProvider
from football_analytics.providers.openligadb import OpenLigaDBProvider
from football_analytics.services.api_football_player_sync import sync_api_football_players
from football_analytics.services.eredivisie_fantasy_sync import sync_eredivisie_fantasy_players
from football_analytics.services.espn_match_statistics_sync import sync_espn_match_statistics
from football_analytics.services.espn_player_roster_sync import sync_espn_player_rosters
from football_analytics.services.football_data_squad_sync import sync_current_football_data_squads
from football_analytics.services.football_data_uk_sync import sync_football_data_uk_statistics
from football_analytics.services.highlightly_match_sync import sync_highlightly_match_statistics
from football_analytics.services.match_event_sync import sync_match_goal_events
from football_analytics.services.pitchapi_match_sync import sync_pitchapi_match_statistics
from football_analytics.services.player_sync import sync_player_statistics
from football_analytics.services.promiedos_match_sync import sync_promiedos_match_fixtures
from football_analytics.services.promiedos_sync import (
    PROMIEDOS_LEAGUE_IDS,
    sync_promiedos_player_statistics,
)
from football_analytics.services.promiedos_team_statistics_sync import (
    PROMIEDOS_TEAM_STATS_ROUTES,
    sync_promiedos_team_statistics,
)
from football_analytics.services.resilient_sync import run_resilient_phase
from football_analytics.services.sync_process_lock import SyncProcessLock
from football_analytics.services.sync_scope import competitions_with_matches_on_date
from football_analytics.services.thesportsdb_profile_sync import sync_thesportsdb_profiles
from football_analytics.sync.orchestrator import run_sync


def _run_sync() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=tuple(definition.code for definition in COMPETITIONS),
        help="Limit sync to selected competition codes, e.g. --competitions UEL.",
    )
    parser.add_argument(
        "--refresh-player-statistics",
        action="store_true",
        help="Re-fetch player match statistics even when team statistics are already current.",
    )
    args = parser.parse_args()
    settings = get_settings()
    if not settings.sync_enabled:
        print("Sincronización deshabilitada por SYNC_ENABLED.")
        return 0
    engine = make_engine()
    try:
        factory = make_session_factory(engine)
        providers = {
            "football-data": FootballDataProvider(),
            "openligadb": OpenLigaDBProvider(),
        }
        selected = tuple(args.competitions) if args.competitions else None
        summary = run_resilient_phase(
            "Sincronización de ligas y partidos",
            lambda: run_sync(factory, providers, SyncMode.DAILY, competition_codes=selected),
        )
        print(f"Daily sync                 {summary.status.value}")
        print(f"Inserted: {summary.inserted_count}")
        print(f"Updated: {summary.updated_count}")
        print(f"Skipped: {summary.skipped_count}")
        print(f"Conflicts: {summary.conflict_count}")
        print(f"Errors: {summary.error_count}")

        local_today = datetime.now(UTC).astimezone(ZoneInfo(settings.app_timezone)).date()
        if selected is None:
            with factory() as session:
                daily_selected = competitions_with_matches_on_date(
                    session, local_today, settings.app_timezone
                )
            if daily_selected:
                print(
                    f"Competencias con partidos hoy ({local_today}): "
                    f"{', '.join(daily_selected)}"
                )
            else:
                print(
                    f"No hay partidos cargados para hoy ({local_today}); "
                    "se omiten consultas de estadísticas por competencia."
                )
        else:
            daily_selected = selected
            print(f"Actualización manual limitada a: {', '.join(daily_selected)}")

        def run_competition_phase(name, operation, *, dictionary=False):
            if daily_selected == ():
                print(f"{name}: omitida, no hay competencias con partidos hoy.")
                values = {
                    "status": SyncStatus.SUCCESS,
                    "requests_count": 0,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "skipped_count": 0,
                    "error_count": 0,
                    "teams_count": 0,
                    "players_matched": 0,
                    "players_inserted": 0,
                    "memberships_inserted": 0,
                    "identities_inserted": 0,
                    "nationality_filled": 0,
                    "position_filled": 0,
                    "birth_dates_filled": 0,
                    "message": "Omitida: no hay partidos de esta competencia hoy.",
                }
                return values if dictionary else SimpleNamespace(**values)
            return run_resilient_phase(name, operation)

        players = run_competition_phase(
            "Estadísticas de jugadores",
            lambda: sync_player_statistics(factory, competition_codes=daily_selected),
        )
        print(f"Player statistics          {players.status.value}")
        print(f"Player rows inserted       {players.inserted_count}")
        print(f"Player rows updated        {players.updated_count}")
        print(f"Player sync errors         {players.error_count}")

        events = run_competition_phase(
            "Eventos y goles de partidos",
            lambda: sync_match_goal_events(
                factory,
                competition_codes=daily_selected,
                mode=SyncMode.DAILY,
            ),
        )
        print(f"Match goal events          {events.status.value}")
        print(f"Goal events inserted       {events.inserted_count}")
        print(f"Goal event errors          {events.error_count}")

        api_players = run_competition_phase(
            "API-Football: perfiles y estadísticas actuales",
            lambda: sync_api_football_players(factory, competition_codes=daily_selected),
        )
        print(f"API-Football players       {api_players.status.value}")
        print(f"API-Football detail        {api_players.message}")
        print(f"Profiles/stats inserted    {api_players.inserted_count}")
        print(f"Profiles/stats updated     {api_players.updated_count}")
        print(f"API-Football errors        {api_players.error_count}")

        promiedos = run_competition_phase(
            "Promiedos: estadísticas de jugadores",
            lambda: sync_promiedos_player_statistics(factory, competition_codes=daily_selected),
        )
        print(f"Promiedos current stats    {promiedos.status.value}")
        print(f"Promiedos rows inserted    {promiedos.inserted_count}")
        print(f"Promiedos rows updated     {promiedos.updated_count}")
        print(f"Promiedos errors           {promiedos.error_count}")

        promiedos_team_codes = (
            tuple(code for code in daily_selected if code in PROMIEDOS_TEAM_STATS_ROUTES)
            if daily_selected is not None
            else None
        )
        promiedos_team_stats = None
        if promiedos_team_codes is None or promiedos_team_codes:
            promiedos_team_stats = run_competition_phase(
                "Promiedos: estadísticas de equipos",
                lambda: sync_promiedos_team_statistics(
                    factory, competition_codes=promiedos_team_codes
                ),
            )
            print(f"Promiedos team stats       {promiedos_team_stats.status.value}")
            print(f"Promiedos team rows added  {promiedos_team_stats.inserted_count}")
            print(f"Promiedos team rows updated {promiedos_team_stats.updated_count}")
            print(f"Promiedos team errors      {promiedos_team_stats.error_count}")

        eredivisie = (
            run_resilient_phase(
                "Eredivisie: planteles y perfiles",
                lambda: sync_eredivisie_fantasy_players(factory),
            )
            if daily_selected is None or "EREDIVISIE" in daily_selected
            else None
        )
        if eredivisie is not None:
            print(f"Eredivisie current roster  {eredivisie['status'].value}")
            print(f"Eredivisie players inserted {eredivisie['inserted_count']}")
            print(f"Eredivisie players updated {eredivisie['updated_count']}")
            print(f"Eredivisie sync errors     {eredivisie['error_count']}")

        squads = run_competition_phase(
            "Planteles y entrenadores de football-data.org",
            lambda: sync_current_football_data_squads(
                factory,
                provider=providers["football-data"],
                competition_codes=daily_selected,
            ),
            dictionary=True,
        )
        print(f"Current full squads        {squads['status'].value}")
        print(f"Clubs with squads          {squads['teams_count']}")
        print(f"Player profiles inserted   {squads['inserted_count']}")
        print(f"Player profiles updated    {squads['updated_count']}")
        print(f"Squad sync errors          {squads['error_count']}")

        espn_rosters = run_competition_phase(
            "ESPN: planteles y datos biográficos",
            lambda: sync_espn_player_rosters(factory, competition_codes=daily_selected),
        )
        print(f"ESPN player rosters        {espn_rosters.status.value}")
        print(f"ESPN profiles linked       {espn_rosters.players_matched}")
        print(f"ESPN players added         {espn_rosters.players_inserted}")
        print(f"ESPN roster memberships   {espn_rosters.memberships_inserted}")
        print(f"ESPN nationality fields    {espn_rosters.nationality_filled}")
        print(f"ESPN position fields       {espn_rosters.position_filled}")
        print(f"ESPN birth dates           {espn_rosters.birth_dates_filled}")
        print(f"ESPN roster errors         {espn_rosters.error_count}")

        match_stats = run_competition_phase(
            "ESPN: estadísticas recientes de partidos",
            lambda: sync_espn_match_statistics(
                factory,
                provider=ESPNProvider(),
                competition_codes=daily_selected,
                date_from=local_today - timedelta(days=2),
                date_to=local_today,
            ),
        )
        print(f"ESPN match statistics      {match_stats.status}")
        print(f"Match stats inserted       {match_stats.inserted_count}")
        print(f"Match stats updated        {match_stats.updated_count}")
        print(f"Match stats skipped        {match_stats.skipped_count}")
        print(f"Match stats errors         {match_stats.error_count}")

        highlightly = None
        if settings.highlightly_api_key.get_secret_value():
            highlightly = run_competition_phase(
                "Highlightly: estadísticas individuales de partidos",
                lambda: sync_highlightly_match_statistics(
                    factory,
                    competition_codes=daily_selected,
                ),
            )
            print(f"Highlightly player stats    {highlightly.status.value}")
            print(f"Player match rows inserted  {highlightly.inserted_count}")
            print(f"Player match rows updated   {highlightly.updated_count}")
            print(f"Highlightly requests       {highlightly.requests_count}")
            print(f"Highlightly skipped        {highlightly.skipped_count}")
            print(f"Highlightly errors         {highlightly.error_count}")
            print(highlightly.message)

        pitchapi = None
        if settings.pitchapi_api_key.get_secret_value():
            pitchapi = run_competition_phase(
                "PitchAPI: estadísticas de partidos y jugadores",
                lambda: sync_pitchapi_match_statistics(
                    factory,
                    competition_codes=daily_selected,
                    force_player_refresh=args.refresh_player_statistics,
                ),
            )
            print(f"PitchAPI match statistics {pitchapi.status.value}")
            print(f"PitchAPI rows inserted     {pitchapi.inserted_count}")
            print(f"PitchAPI rows updated      {pitchapi.updated_count}")
            print(f"PitchAPI requests          {pitchapi.requests_count}")
            print(f"PitchAPI skipped           {pitchapi.skipped_count}")
            print(f"PitchAPI errors            {pitchapi.error_count}")
            print(pitchapi.message)

        football_data_uk = run_competition_phase(
            "Football-Data.co.uk: estadísticas de temporada",
            lambda: sync_football_data_uk_statistics(
                factory, competition_codes=daily_selected
            ),
        )
        print(f"Football-Data.co.uk stats {football_data_uk.status.value}")
        print(f"CSV rows inserted        {football_data_uk.inserted_count}")
        print(f"CSV rows updated         {football_data_uk.updated_count}")
        print(f"CSV rows skipped         {football_data_uk.skipped_count}")
        print(f"CSV import errors        {football_data_uk.error_count}")
        print(football_data_uk.message)

        promiedos_match_codes = (
            tuple(code for code in daily_selected if code in PROMIEDOS_LEAGUE_IDS)
            if daily_selected is not None
            else None
        )
        promiedos_matches = (
            run_competition_phase(
                "Promiedos: calendario y resultados recientes",
                lambda: sync_promiedos_match_fixtures(
                    factory,
                    competition_codes=promiedos_match_codes,
                    full_season=False,
                ),
            )
            if promiedos_match_codes is None or promiedos_match_codes
            else None
        )
        if promiedos_matches is not None:
            print(f"Promiedos match results    {promiedos_matches.status.value}")
            print(f"Promiedos matches updated  {promiedos_matches.updated_count}")
            print(f"Promiedos match errors     {promiedos_matches.error_count}")

        # FPL's published terms prohibit automated extraction. Existing local
        # records remain intact; the scheduled sync uses the other player feeds.

        # Sources often assign separate IDs to the same person. Consolidate
        # verified identities after every complete import and keep all source
        # links pointing at the canonical player for the next scheduled sync.
        # When this file is executed as ``python scripts/sync_daily.py``,
        # Python puts the scripts directory (not the repository root) on
        # sys.path. Import the sibling modules directly so scheduled runs
        # reach both post-sync audits instead of failing after data import.
        from deduplicate_players import run as run_player_identity_deduplication

        run_player_identity_deduplication(apply=True)
        print("Player identity audit       completed")

        from reconcile_active_player_memberships import run as run_player_membership_audit

        run_player_membership_audit(apply=True)
        print("Player membership audit     completed")

        thesportsdb_errors = 0
        try:
            thesportsdb = run_competition_phase(
                "TheSportsDB: perfiles de jugadores",
                lambda: sync_thesportsdb_profiles(
                    factory,
                    competition_codes=daily_selected,
                ),
            )
            thesportsdb_errors = thesportsdb.error_count
            print(f"TheSportsDB profiles       {thesportsdb.players_matched} linked")
            print(f"TheSportsDB requests       {thesportsdb.requests_count}")
            print(f"Nationality fields filled  {thesportsdb.nationality_filled}")
            print(f"Position fields filled     {thesportsdb.position_filled}")
            print(f"Birth dates filled         {thesportsdb.birth_dates_filled}")
            print(f"TheSportsDB errors         {thesportsdb.error_count}")
        except Exception as error:
            thesportsdb_errors = 1
            print(f"TheSportsDB profile sync failed: {error.__class__.__name__}")

        # Wikidata is an additional free, CC0 source for biography fields and
        # coaches. It is intentionally separate from season-stat providers:
        # its historical statements must never be presented as current stats.
        wikidata_errors = 0
        wikidata_script = project_root() / "scripts" / "import_wikidata_missing_data.py"
        if wikidata_script.is_file() and daily_selected != ():
            try:
                wikidata_command = [sys.executable, str(wikidata_script)]
                if daily_selected is not None:
                    wikidata_command.extend(["--competitions", *daily_selected])
                wikidata = run_resilient_phase(
                    "Wikidata: perfiles y entrenadores",
                    lambda: subprocess.run(
                        wikidata_command,
                        cwd=project_root(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    ),
                    # This script reports process failure through its return code.
                    error_count=lambda result: int(result.returncode != 0),
                )
                if wikidata.stdout:
                    print(wikidata.stdout, end="" if wikidata.stdout.endswith("\n") else "\n")
                print(f"Wikidata profile sync exit {wikidata.returncode}")
                wikidata_errors = int(wikidata.returncode != 0)
            except Exception as error:
                wikidata_errors = 1
                print(f"Wikidata profile sync failed: {error.__class__.__name__}")
        else:
            wikidata_errors = int(not wikidata_script.is_file())
            if wikidata_script.is_file():
                print("Wikidata: omitida, no hay competencias con partidos hoy.")
            else:
                print("Wikidata profile sync failed: script not found")

        reconciliation = None
        local_now = datetime.now(UTC).astimezone(ZoneInfo(settings.app_timezone))
        if local_now.weekday() == 6:
            reconciliation = run_resilient_phase(
                "Reconciliación semanal de ligas y temporadas",
                lambda: run_sync(
                    factory, providers, SyncMode.RECONCILIATION, competition_codes=selected
                ),
            )
            print(f"Weekly reconciliation     {reconciliation.status.value}")
            if reconciliation.error_count:
                return 2
        return (
            0
            if summary.error_count == 0
            and summary.status == SyncStatus.SUCCESS
            and players.error_count == 0
            and players.status == SyncStatus.SUCCESS
            and events.error_count == 0
            and events.status == SyncStatus.SUCCESS
            and api_players.error_count == 0
            and api_players.status == SyncStatus.SUCCESS
            and promiedos.error_count == 0
            and promiedos.status == SyncStatus.SUCCESS
            and (promiedos_team_stats is None or promiedos_team_stats.error_count == 0)
            and (promiedos_team_stats is None or promiedos_team_stats.status == SyncStatus.SUCCESS)
            and (eredivisie is None or eredivisie["error_count"] == 0)
            and (eredivisie is None or eredivisie["status"] == SyncStatus.SUCCESS)
            and squads["error_count"] == 0
            and squads["status"] == SyncStatus.SUCCESS
            and espn_rosters.error_count == 0
            and espn_rosters.status == SyncStatus.SUCCESS
            and match_stats.error_count == 0
            and match_stats.status == SyncStatus.SUCCESS
            and (highlightly is None or highlightly.error_count == 0)
            and (highlightly is None or highlightly.status == SyncStatus.SUCCESS)
            and (pitchapi is None or pitchapi.error_count == 0)
            and (pitchapi is None or pitchapi.status == SyncStatus.SUCCESS)
            and football_data_uk.error_count == 0
            and football_data_uk.status == SyncStatus.SUCCESS
            and (promiedos_matches is None or promiedos_matches.error_count == 0)
            and (promiedos_matches is None or promiedos_matches.status == SyncStatus.SUCCESS)
            and thesportsdb_errors == 0
            and wikidata_errors == 0
            and (reconciliation is None or reconciliation.error_count == 0)
            and (reconciliation is None or reconciliation.status == SyncStatus.SUCCESS)
            else 2
        )
    finally:
        engine.dispose()


def main() -> int:
    lock = SyncProcessLock(project_root() / "logs" / "full_sync.lock")
    if not lock.acquire():
        print("Ya hay una sincronización completa en curso; no se inició otra.")
        return 3
    try:
        return _run_sync()
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
