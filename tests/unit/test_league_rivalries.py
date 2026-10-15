from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from football_analytics.config.league_rivalries import rivalries_for_competition
from football_analytics.database.base import Base
from football_analytics.database.models import Competition, CompetitionSeason, Match, Season, Team
from football_analytics.services.team_service import TeamService


def test_each_supported_league_limits_results_to_one_derby_and_two_classics() -> None:
    league_teams = {
        "LALIGA": ("FC Barcelona", "Real Madrid CF"),
        "BUNDESLIGA": ("FC Bayern München", "Borussia Dortmund"),
        "EREDIVISIE": ("AFC Ajax", "Feyenoord Rotterdam"),
        "LIGUE_1": ("Paris Saint-Germain FC", "Olympique de Marseille"),
        "SERIE_A": ("FC Internazionale Milano", "Juventus FC"),
    }
    for code, clubs in league_teams.items():
        for club in clubs:
            selected = rivalries_for_competition(code, club)
            assert len([item for item in selected if item.priority == 0]) <= 1
            assert len([item for item in selected if item.priority != 0]) <= 2
            assert len(selected) <= 3


def test_rivalry_history_uses_only_finished_matches_from_selected_competition() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Competition(id=1, code="LALIGA", name="LaLiga"),
                Competition(id=2, code="UCL", name="UEFA Champions League"),
                Season(id=1, label="2026/27", start_year=2026, end_year=2027),
                CompetitionSeason(id=1, competition_id=1, season_id=1, status="ACTIVE"),
                CompetitionSeason(id=2, competition_id=2, season_id=1, status="ACTIVE"),
                Team(id=1, canonical_name="FC Barcelona", crest_url="barca.png"),
                Team(id=2, canonical_name="Real Madrid CF", crest_url="madrid.png"),
                Team(id=3, canonical_name="Club Atlético de Madrid", crest_url="atleti.png"),
            ]
        )
        kickoff = datetime(2026, 8, 1, tzinfo=UTC)
        for index in range(6):
            session.add(
                Match(
                    id=index + 1,
                    competition_season_id=1,
                    home_team_id=1 if index % 2 == 0 else 2,
                    away_team_id=2 if index % 2 == 0 else 1,
                    kickoff_utc=kickoff + timedelta(days=index * 7),
                    status="FINISHED",
                    home_score_ft=1,
                    away_score_ft=0,
                )
            )
        session.add(
            Match(
                id=7,
                competition_season_id=2,
                home_team_id=1,
                away_team_id=2,
                kickoff_utc=kickoff + timedelta(days=100),
                status="FINISHED",
                home_score_ft=2,
                away_score_ft=0,
            )
        )
        session.commit()

        sections = TeamService(session).get_rivalry_sections(1, "LALIGA")

    engine.dispose()
    assert len(sections) == 1
    el_clasico = next(section for section in sections if section.rival_name == "Real Madrid CF")
    assert el_clasico.category == "El Clásico"
    assert el_clasico.rival_crest_url == "madrid.png"
    assert len(el_clasico.matches) == 5
    assert [match.kickoff_utc for match in el_clasico.matches] == sorted(
        (match.kickoff_utc for match in el_clasico.matches), reverse=True
    )
    assert all(match.competition_name == "LaLiga" for match in el_clasico.matches)


def test_rivalry_history_hides_pairs_outside_loaded_participants() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Competition(id=1, code="LIGUE_1", name="Ligue 1"),
                Season(id=1, label="2026/27", start_year=2026, end_year=2027),
                CompetitionSeason(id=1, competition_id=1, season_id=1, status="ACTIVE"),
                Team(id=1, canonical_name="Paris Saint-Germain FC"),
                Team(id=2, canonical_name="Olympique de Marseille"),
                Team(id=3, canonical_name="Olympique Lyonnais"),
                Team(id=4, canonical_name="Paris FC"),
                Match(
                    id=1,
                    competition_season_id=1,
                    home_team_id=1,
                    away_team_id=2,
                    kickoff_utc=datetime(2026, 8, 1, tzinfo=UTC),
                    status="FINISHED",
                    home_score_ft=1,
                    away_score_ft=1,
                ),
            ]
        )
        session.commit()

        sections = TeamService(session).get_rivalry_sections(1, "LIGUE_1")

    engine.dispose()
    assert [section.rival_name for section in sections] == ["Olympique de Marseille"]
