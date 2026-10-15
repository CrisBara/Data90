"""Team search and season-aware team profiles."""

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from football_analytics.analytics.team_metrics import (
    TeamMatchResult,
    TeamMetrics,
    calculate_team_metrics,
)
from football_analytics.config.league_rivalries import rivalries_for_competition
from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchTeamStatistic,
    Season,
    StandingRow,
    StandingsSnapshot,
    Team,
    TeamCoach,
)
from football_analytics.domain.exceptions import EntityResolutionError
from football_analytics.repositories.players import TeamSquadItem, team_squad_for_team
from football_analytics.repositories.teams import (
    TeamListItem,
    TeamMatchItem,
    search_teams,
    team_match_history,
)


@dataclass(frozen=True, slots=True)
class TeamCompetitionOption:
    competition_season_id: int
    competition_code: str
    competition_name: str
    season_label: str

    @property
    def label(self) -> str:
        return f"{self.competition_name} · {self.season_label}"


@dataclass(frozen=True, slots=True)
class TeamStatTotals:
    metric: str
    total: int | None
    home: int | None
    away: int | None
    matches_covered: int
    matches_played: int


@dataclass(frozen=True, slots=True)
class TeamProfile:
    team_id: int
    name: str
    crest_url: str | None
    country_name: str | None
    coach_name: str | None
    competitions: tuple[TeamCompetitionOption, ...]
    selected_competition_season_id: int | None
    standing_position: int | None
    standing_points: int | None
    metrics: TeamMetrics
    recent_matches: list[TeamMatchItem]
    upcoming_matches: list[TeamMatchItem]
    match_history: list[TeamMatchItem]
    match_stat_averages: list[tuple[str, float, int]]
    stat_totals: list[TeamStatTotals]
    squad: list[TeamSquadItem]


@dataclass(frozen=True, slots=True)
class TeamRivalrySection:
    category: str
    rival_id: int | None
    rival_name: str
    rival_crest_url: str | None
    matches: tuple[TeamMatchItem, ...]


class TeamService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def search(
        self, query: str | None = None, competition_code: str | None = None
    ) -> list[TeamListItem]:
        return search_teams(self.session, query, competition_code)

    def get_match_history(self, team_id: int) -> list[TeamMatchItem]:
        if self.session.get(Team, team_id) is None:
            raise EntityResolutionError(f"Equipo inexistente: {team_id}")
        return team_match_history(self.session, team_id)

    def get_rivalry_sections(
        self, team_id: int, competition_code: str
    ) -> tuple[TeamRivalrySection, ...]:
        """Assemble real head-to-head histories from one domestic league."""
        team = self.session.get(Team, team_id)
        if team is None:
            raise EntityResolutionError(f"Equipo inexistente: {team_id}")

        rivalries = rivalries_for_competition(competition_code, team.canonical_name)
        if not rivalries:
            return ()

        competition_name = self.session.scalar(
            select(Competition.name)
            .join(CompetitionSeason, CompetitionSeason.competition_id == Competition.id)
            .where(
                Competition.code == competition_code,
                CompetitionSeason.id.in_(
                    select(Match.competition_season_id).where(
                        or_(Match.home_team_id == team_id, Match.away_team_id == team_id)
                    )
                ),
            )
            .limit(1)
        )
        if competition_name is None:
            return ()

        competition_season_ids = select(CompetitionSeason.id).join(
            Competition, Competition.id == CompetitionSeason.competition_id
        ).where(Competition.code == competition_code)
        participating_team_ids = select(Match.home_team_id).where(
            Match.competition_season_id.in_(competition_season_ids)
        ).union(
            select(Match.away_team_id).where(
                Match.competition_season_id.in_(competition_season_ids)
            )
        )
        participating_names = set(
            self.session.scalars(
                select(Team.canonical_name).where(Team.id.in_(participating_team_ids))
            ).all()
        )
        rival_names = [
            rivalry.rival_name
            for rivalry in rivalries
            if rivalry.rival_name in participating_names
        ]
        if not rival_names:
            return ()

        identities = {
            name: (rival_id, crest_url)
            for rival_id, name, crest_url in self.session.execute(
                select(Team.id, Team.canonical_name, Team.crest_url).where(
                    Team.canonical_name.in_(rival_names)
                )
            ).all()
        }
        history = [
            match
            for match in team_match_history(self.session, team_id, status="FINISHED")
            if match.competition_name == competition_name
        ]

        sections: list[TeamRivalrySection] = []
        for rivalry in rivalries:
            if rivalry.rival_name not in participating_names:
                continue
            rival_id, crest_url = identities.get(rivalry.rival_name, (None, None))
            meetings = [match for match in history if match.opponent == rivalry.rival_name]
            sections.append(
                TeamRivalrySection(
                    category=rivalry.category,
                    rival_id=rival_id,
                    rival_name=rivalry.rival_name,
                    rival_crest_url=crest_url
                    or next(
                        (
                            match.opponent_crest_url
                            for match in meetings
                            if match.opponent_crest_url
                        ),
                        None,
                    ),
                    matches=tuple(meetings[:5]),
                )
            )
        return tuple(sections)

    def get_profile(self, team_id: int, competition_season_id: int | None = None) -> TeamProfile:
        team = self.session.get(Team, team_id)
        if team is None:
            raise EntityResolutionError(f"Equipo inexistente: {team_id}")
        options = self._competition_options(team_id)
        valid_ids = {item.competition_season_id for item in options}
        if competition_season_id not in valid_ids:
            competition_season_id = options[0].competition_season_id if options else None
        coach = self.session.scalar(
            select(TeamCoach.full_name)
            .join(DataSource, DataSource.id == TeamCoach.source_id)
            .where(TeamCoach.team_id == team_id)
            .order_by(DataSource.priority)
        )
        finished = (
            team_match_history(
                self.session,
                team_id,
                competition_season_id=competition_season_id,
                status="FINISHED",
            )
            if competition_season_id
            else []
        )
        upcoming = (
            team_match_history(
                self.session,
                team_id,
                competition_season_id=competition_season_id,
                status="SCHEDULED",
                limit=5,
                upcoming=True,
            )
            if competition_season_id
            else []
        )
        recent = finished[:5]
        history = (
            team_match_history(self.session, team_id, competition_season_id=competition_season_id)
            if competition_season_id
            else []
        )
        standing_position, standing_points = self._standing(team_id, competition_season_id)
        return TeamProfile(
            team_id=team.id,
            name=team.canonical_name,
            crest_url=team.crest_url,
            country_name=team.country_name,
            coach_name=coach,
            competitions=tuple(options),
            selected_competition_season_id=competition_season_id,
            standing_position=standing_position,
            standing_points=standing_points,
            metrics=self.get_metrics(team_id, competition_season_id),
            recent_matches=recent,
            upcoming_matches=upcoming,
            match_history=history,
            match_stat_averages=self.get_match_stat_averages(team_id, competition_season_id),
            stat_totals=self.get_stat_totals(team_id, competition_season_id, len(finished)),
            squad=team_squad_for_team(self.session, team_id),
        )

    def _competition_options(self, team_id: int) -> list[TeamCompetitionOption]:
        rows = self.session.execute(
            select(
                CompetitionSeason.id,
                Competition.code,
                Competition.name,
                Season.label,
                Season.start_year,
                Competition.display_order,
            )
            .join(Season, Season.id == CompetitionSeason.season_id)
            .join(Competition, Competition.id == CompetitionSeason.competition_id)
            .where(
                Season.active.is_(True),
                CompetitionSeason.id.in_(
                    select(Match.competition_season_id).where(
                        or_(Match.home_team_id == team_id, Match.away_team_id == team_id)
                    )
                ),
            )
            .order_by(
                Competition.display_order.nullslast(), Competition.name, Season.start_year.desc()
            )
        ).all()
        return [TeamCompetitionOption(row[0], row[1], row[2], row[3]) for row in rows]

    def _standing(
        self, team_id: int, competition_season_id: int | None
    ) -> tuple[int | None, int | None]:
        if competition_season_id is None:
            return None, None
        snapshot_id = self.session.scalar(
            select(StandingsSnapshot.id)
            .join(DataSource, DataSource.id == StandingsSnapshot.source_id)
            .where(
                StandingsSnapshot.competition_season_id == competition_season_id,
                StandingsSnapshot.scope == "TOTAL",
            )
            .order_by(DataSource.priority, StandingsSnapshot.snapshot_at.desc())
            .limit(1)
        )
        if snapshot_id is None:
            return None, None
        row = self.session.execute(
            select(StandingRow.position, StandingRow.points).where(
                StandingRow.snapshot_id == snapshot_id, StandingRow.team_id == team_id
            )
        ).one_or_none()
        return (row.position, row.points) if row else (None, None)

    def get_match_stat_averages(
        self, team_id: int, competition_season_id: int | None = None
    ) -> list[tuple[str, float, int]]:
        labels = {
            "possession": "Posesión (%)",
            "shots_total": "Tiros totales",
            "shots_on_target": "Tiros al arco",
            "shots_off_target": "Tiros fuera del arco",
            "shots_blocked": "Tiros bloqueados",
            "shots_inside_box": "Tiros dentro del área",
            "shots_outside_box": "Tiros fuera del área",
            "corners": "Córners",
            "fouls": "Faltas",
            "offsides": "Fuera de juego",
            "yellow_cards": "Tarjetas amarillas",
            "red_cards": "Tarjetas rojas",
            "passes_total": "Pases totales",
            "passes_accurate": "Pases precisos",
            "goalkeeper_saves": "Atajadas",
        }
        statement = (
            select(MatchTeamStatistic)
            .join(Match, MatchTeamStatistic.match_id == Match.id)
            .join(DataSource, DataSource.id == MatchTeamStatistic.source_id)
            .where(MatchTeamStatistic.team_id == team_id, Match.status == "FINISHED")
            .order_by(DataSource.priority)
        )
        if competition_season_id is not None:
            statement = statement.where(Match.competition_season_id == competition_season_id)
        by_match: dict[int, MatchTeamStatistic] = {}
        for row in self.session.scalars(statement):
            by_match.setdefault(row.match_id, row)
        result = []
        for field, label in labels.items():
            values = [
                float(value)
                for row in by_match.values()
                if (value := getattr(row, field)) is not None
            ]
            if values:
                result.append((label, sum(values) / len(values), len(values)))
        return result

    def get_stat_totals(
        self, team_id: int, competition_season_id: int | None, matches_played: int
    ) -> list[TeamStatTotals]:
        labels = (("yellow_cards", "Amarillas"), ("red_cards", "Rojas"), ("corners", "Córners"))
        if competition_season_id is None:
            return [
                TeamStatTotals(label, None, None, None, 0, matches_played) for _, label in labels
            ]
        statement = (
            select(MatchTeamStatistic, Match.home_team_id == team_id)
            .join(Match, Match.id == MatchTeamStatistic.match_id)
            .join(DataSource, DataSource.id == MatchTeamStatistic.source_id)
            .where(
                MatchTeamStatistic.team_id == team_id,
                Match.competition_season_id == competition_season_id,
                Match.status == "FINISHED",
            )
            .order_by(DataSource.priority)
        )
        per_match: dict[int, tuple[MatchTeamStatistic, bool]] = {}
        for row, is_home in self.session.execute(statement):
            per_match.setdefault(row.match_id, (row, is_home))
        result = []
        for field, label in labels:
            total = home = away = covered = 0
            for row, is_home in per_match.values():
                value = getattr(row, field)
                if value is None:
                    continue
                covered += 1
                total += value
                if is_home:
                    home += value
                else:
                    away += value
            result.append(
                TeamStatTotals(
                    label,
                    total if covered else None,
                    home if covered else None,
                    away if covered else None,
                    covered,
                    matches_played,
                )
            )
        return result

    def get_metrics(self, team_id: int, competition_season_id: int | None = None) -> TeamMetrics:
        if self.session.get(Team, team_id) is None:
            raise EntityResolutionError(f"Equipo inexistente: {team_id}")
        statement = select(Match).where(
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
            Match.status == "FINISHED",
            Match.home_score_ft.is_not(None),
            Match.away_score_ft.is_not(None),
        )
        if competition_season_id is not None:
            statement = statement.where(Match.competition_season_id == competition_season_id)
        records = []
        for match in self.session.scalars(statement).all():
            is_home = match.home_team_id == team_id
            records.append(
                TeamMatchResult(
                    match.kickoff_utc,
                    match.home_score_ft if is_home else match.away_score_ft,
                    match.away_score_ft if is_home else match.home_score_ft,
                    is_home,
                )
            )
        return calculate_team_metrics(records)
