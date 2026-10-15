"""On-demand competition and team statistics from completed local matches."""

from collections import defaultdict
from dataclasses import dataclass, replace

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from data90.analytics.team_metrics import (
    TeamMatchResult,
    TeamMetrics,
    calculate_team_metrics,
)
from data90.database.models import (
    DataSource,
    Match,
    MatchTeamStatistic,
    Team,
    TeamCompetitionSeasonStatistic,
)


@dataclass(frozen=True, slots=True)
class TeamStatistics:
    team_id: int
    team_name: str
    crest_url: str | None
    metrics: TeamMetrics
    promiedos: TeamCompetitionSeasonStatistic | None = None


@dataclass(frozen=True, slots=True)
class MatchStatSummary:
    total: int | None
    home_total: int | None
    away_total: int | None
    average_per_match: float | None
    matches_with_data: int | None
    matches_with_event_percent: float | None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class TeamMatchStatSummaries:
    yellow_cards: MatchStatSummary
    red_cards: MatchStatSummary
    corners: MatchStatSummary


class StatisticsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def for_competition_season(self, competition_season_id: int) -> list[TeamStatistics]:
        team_aggregates = {
            row.team_id: row
            for row in self.session.scalars(
                select(TeamCompetitionSeasonStatistic).where(
                    TeamCompetitionSeasonStatistic.competition_season_id == competition_season_id
                )
            ).all()
        }
        teams = self.session.execute(
            select(Team.id, Team.canonical_name, Team.crest_url)
            .join(
                Match,
                or_(Match.home_team_id == Team.id, Match.away_team_id == Team.id),
            )
            .where(Match.competition_season_id == competition_season_id)
            .distinct()
            .order_by(Team.canonical_name)
        ).all()
        matches_by_team: dict[int, list[TeamMatchResult]] = defaultdict(list)
        matches = self.session.execute(
            select(
                Match.home_team_id,
                Match.away_team_id,
                Match.kickoff_utc,
                Match.home_score_ft,
                Match.away_score_ft,
            ).where(
                Match.competition_season_id == competition_season_id,
                Match.status == "FINISHED",
                Match.home_score_ft.is_not(None),
                Match.away_score_ft.is_not(None),
            )
        ).all()
        for home_id, away_id, kickoff, home_score, away_score in matches:
            matches_by_team[home_id].append(
                TeamMatchResult(kickoff, home_score, away_score, is_home=True)
            )
            matches_by_team[away_id].append(
                TeamMatchResult(kickoff, away_score, home_score, is_home=False)
            )

        result = []
        for team_id, team_name, crest_url in teams:
            metrics = calculate_team_metrics(matches_by_team[team_id])
            aggregate = team_aggregates.get(team_id)
            if aggregate is not None:
                overrides = {}
                if aggregate.goals_average is not None:
                    overrides["average_goals_for"] = float(aggregate.goals_average)
                if aggregate.goals_conceded_average is not None:
                    overrides["average_goals_against"] = float(aggregate.goals_conceded_average)
                if aggregate.clean_sheets is not None:
                    overrides["clean_sheets"] = aggregate.clean_sheets
                if overrides:
                    metrics = replace(metrics, **overrides)
            result.append(
                TeamStatistics(
                    team_id=team_id,
                    team_name=team_name,
                    crest_url=crest_url,
                    metrics=metrics,
                    promiedos=aggregate,
                )
            )
        return result

    def match_stat_summaries_for_competition_season(
        self, competition_season_id: int
    ) -> dict[int, TeamMatchStatSummaries]:
        rows = self.session.execute(
            select(
                MatchTeamStatistic.team_id,
                MatchTeamStatistic.match_id,
                Match.home_team_id,
                MatchTeamStatistic.yellow_cards,
                MatchTeamStatistic.red_cards,
                MatchTeamStatistic.corners,
            )
            .join(Match, Match.id == MatchTeamStatistic.match_id)
            .join(DataSource, DataSource.id == MatchTeamStatistic.source_id)
            .where(
                Match.competition_season_id == competition_season_id,
                Match.status == "FINISHED",
                Match.home_score_ft.is_not(None),
                Match.away_score_ft.is_not(None),
            )
            .order_by(DataSource.priority, MatchTeamStatistic.id)
        ).all()
        played_by_team: dict[int, int] = defaultdict(int)
        for home_id, away_id in self.session.execute(
            select(Match.home_team_id, Match.away_team_id).where(
                Match.competition_season_id == competition_season_id,
                Match.status == "FINISHED",
                Match.home_score_ft.is_not(None),
                Match.away_score_ft.is_not(None),
            )
        ):
            played_by_team[home_id] += 1
            played_by_team[away_id] += 1

        # Prefer the highest-priority source per match and statistic, while
        # allowing a lower-priority source to fill fields it did not publish.
        per_match: dict[int, dict[int, tuple[bool, dict[str, int]]]] = defaultdict(dict)
        fields = ("yellow_cards", "red_cards", "corners")
        for team_id, match_id, home_team_id, yellow_cards, red_cards, corners in rows:
            is_home, values = per_match[team_id].setdefault(match_id, (team_id == home_team_id, {}))
            for field, value in zip(fields, (yellow_cards, red_cards, corners), strict=False):
                if value is not None:
                    values.setdefault(field, value)

        summaries: dict[int, TeamMatchStatSummaries] = {}
        aggregate_by_team = {
            row.team_id: row
            for row in self.session.scalars(
                select(TeamCompetitionSeasonStatistic).where(
                    TeamCompetitionSeasonStatistic.competition_season_id == competition_season_id
                )
            ).all()
        }
        for team_id in per_match.keys() | aggregate_by_team.keys():
            matches = per_match.get(team_id, {})
            aggregate = aggregate_by_team.get(team_id)
            field_summaries = {}
            for field in fields:
                values = [
                    match_values[field]
                    for _is_home, match_values in matches.values()
                    if field in match_values
                ]
                home_values = [
                    match_values[field]
                    for is_home, match_values in matches.values()
                    if is_home and field in match_values
                ]
                away_values = [
                    match_values[field]
                    for is_home, match_values in matches.values()
                    if not is_home and field in match_values
                ]
                total = sum(values) if values else None
                played = played_by_team.get(team_id, 0)
                is_complete = played > 0 and len(values) >= played
                aggregate_value = (
                    getattr(aggregate, field)
                    if aggregate is not None and field in {"yellow_cards", "red_cards"}
                    else None
                )
                aggregate_corners_average = (
                    aggregate.corners_average
                    if aggregate is not None and field == "corners"
                    else None
                )
                use_aggregate = not is_complete and (
                    aggregate_value is not None or aggregate_corners_average is not None
                )
                field_summaries[field] = MatchStatSummary(
                    total=aggregate_value
                    if use_aggregate and aggregate_value is not None
                    else total,
                    home_total=None
                    if use_aggregate
                    else (sum(home_values) if home_values else None),
                    away_total=None
                    if use_aggregate
                    else (sum(away_values) if away_values else None),
                    average_per_match=(
                        aggregate_corners_average
                        if use_aggregate and aggregate_corners_average is not None
                        else total / len(values)
                        if values
                        else None
                    ),
                    matches_with_data=None if use_aggregate else len(values),
                    matches_with_event_percent=(
                        None
                        if use_aggregate
                        else sum(value > 0 for value in values) * 100 / len(values)
                        if values
                        else None
                    ),
                    source=(
                        "Promiedos (acumulado de temporada; sin desglose local/visitante)"
                        if use_aggregate
                        else None
                    ),
                )
            summaries[team_id] = TeamMatchStatSummaries(**field_summaries)
        return summaries
