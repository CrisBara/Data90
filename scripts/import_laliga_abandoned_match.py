"""Correct the abandoned Rayo Vallecano–Athletic Club match using LaLiga's official page."""

from datetime import UTC, datetime

from sqlalchemy import select

from football_analytics.config.constants import SOURCES
from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchTeamStatistic,
    Season,
    SourceMatch,
    Team,
)
from football_analytics.database.session import make_engine, make_session_factory

OFFICIAL_MATCH_ID = "1_8579"
OFFICIAL_MATCH_URL = (
    "https://www.laliga.com/en-US/match/temporada-2026-2027-laliga-ea-sports-"
    "rayo-vallecano-athletic-club-8"
)


def main() -> int:
    source_definition = next(row for row in SOURCES if row["code"] == "laliga-official")
    engine = make_engine()
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        source = session.scalar(
            select(DataSource).where(DataSource.code == "laliga-official")
        )
        if source is None:
            source = DataSource(**source_definition)
            session.add(source)
            session.flush()
        else:
            for field, value in source_definition.items():
                setattr(source, field, value)

        match = session.scalar(
            select(Match)
            .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
            .join(Competition, Competition.id == CompetitionSeason.competition_id)
            .join(Season, Season.id == CompetitionSeason.season_id)
            .join(Team, Team.id == Match.home_team_id)
            .where(
                Competition.code == "LALIGA",
                Season.start_year == 2026,
                Season.end_year == 2027,
                Match.matchday == 8,
                Match.kickoff_utc >= datetime(2026, 10, 10, tzinfo=UTC),
                Match.kickoff_utc < datetime(2026, 10, 11, tzinfo=UTC),
                Team.canonical_name.ilike("%Rayo Vallecano%"),
            )
        )
        if match is None:
            raise RuntimeError("No se encontró el partido esperado en LaLiga 2026/27.")
        home = session.get(Team, match.home_team_id)
        away = session.get(Team, match.away_team_id)
        if not home or not away or "Athletic" not in away.canonical_name:
            raise RuntimeError(
                "La identidad de local/visitante no coincide con Rayo Vallecano–Athletic Club."
            )
        if (match.home_score_ft, match.away_score_ft) != (0, 0):
            raise RuntimeError("El marcador guardado cambió; se detiene para evitar sobrescribirlo.")

        now = datetime.now(UTC)
        source_match = session.scalar(
            select(SourceMatch).where(
                SourceMatch.source_id == source.id,
                SourceMatch.external_id == OFFICIAL_MATCH_ID,
            )
        )
        if source_match is None:
            session.add(
                SourceMatch(
                    source_id=source.id,
                    match_id=match.id,
                    external_id=OFFICIAL_MATCH_ID,
                    source_updated_at=now,
                )
            )
        elif source_match.match_id != match.id:
            raise RuntimeError("El identificador oficial ya está vinculado a otro partido.")
        else:
            source_match.source_updated_at = now

        match.status = "SUSPENDED"
        match.source_updated_at = now
        stats = {
            match.home_team_id: {
                "possession": 55.1,
                "shots_total": 1,
                "shots_on_target": 1,
                "corners": 1,
                "fouls": 2,
                "offsides": 2,
                "yellow_cards": 0,
                "red_cards": 0,
            },
            match.away_team_id: {
                "possession": 44.9,
                "shots_total": 1,
                "shots_on_target": 0,
                "corners": 2,
                "fouls": 0,
                "offsides": 0,
                "yellow_cards": 0,
                "red_cards": 0,
            },
        }
        for team_id, values in stats.items():
            row = session.scalar(
                select(MatchTeamStatistic).where(
                    MatchTeamStatistic.match_id == match.id,
                    MatchTeamStatistic.team_id == team_id,
                    MatchTeamStatistic.source_id == source.id,
                )
            )
            if row is None:
                row = MatchTeamStatistic(
                    match_id=match.id,
                    team_id=team_id,
                    source_id=source.id,
                )
                session.add(row)
            for field, value in values.items():
                setattr(row, field, value)
            row.source_updated_at = now

        print(
            f"Corregido partido {match.id}: {home.canonical_name}–{away.canonical_name}, "
            f"SUSPENDED (marcador parcial 0–0); estadísticas oficiales parciales guardadas."
        )
        print(f"Fuente: {OFFICIAL_MATCH_URL}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
