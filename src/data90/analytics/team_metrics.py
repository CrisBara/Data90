from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TeamMatchResult:
    kickoff_utc: datetime
    goals_for: int
    goals_against: int
    is_home: bool


@dataclass(frozen=True, slots=True)
class TeamMetrics:
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    average_goals_for: float | None
    average_goals_against: float | None
    win_percent: float | None
    draw_percent: float | None
    loss_percent: float | None
    clean_sheets: int
    failed_to_score: int
    form_last_5: str
    form_last_10: str
    home_played: int
    home_won: int
    home_drawn: int
    home_lost: int
    home_points: int
    away_played: int
    away_won: int
    away_drawn: int
    away_lost: int
    away_points: int
    unbeaten_streak: int
    win_streak: int
    scoring_streak: int


def calculate_team_metrics(matches: Iterable[TeamMatchResult]) -> TeamMetrics:
    ordered = sorted(matches, key=lambda match: match.kickoff_utc)
    results: list[str] = []
    points_by_match: list[int] = []
    home_points = away_points = home_played = away_played = 0
    home_won = home_drawn = home_lost = away_won = away_drawn = away_lost = 0
    goals_for = goals_against = wins = draws = losses = clean_sheets = failed_to_score = 0

    for match in ordered:
        goals_for += match.goals_for
        goals_against += match.goals_against
        if match.goals_for > match.goals_against:
            outcome, points = "W", 3
            wins += 1
        elif match.goals_for == match.goals_against:
            outcome, points = "D", 1
            draws += 1
        else:
            outcome, points = "L", 0
            losses += 1
        results.append(outcome)
        points_by_match.append(points)
        if match.goals_against == 0:
            clean_sheets += 1
        if match.goals_for == 0:
            failed_to_score += 1
        if match.is_home:
            home_played += 1
            home_points += points
            home_won += outcome == "W"
            home_drawn += outcome == "D"
            home_lost += outcome == "L"
        else:
            away_played += 1
            away_points += points
            away_won += outcome == "W"
            away_drawn += outcome == "D"
            away_lost += outcome == "L"

    played = len(ordered)
    unbeaten_streak = _streak(results, lambda result: result != "L")
    win_streak = _streak(results, lambda result: result == "W")
    scoring_streak = _streak(
        ["S" if match.goals_for > 0 else "N" for match in ordered],
        lambda result: result == "S",
    )
    return TeamMetrics(
        played=played,
        won=wins,
        drawn=draws,
        lost=losses,
        goals_for=goals_for,
        goals_against=goals_against,
        goal_difference=goals_for - goals_against,
        points=sum(points_by_match),
        average_goals_for=goals_for / played if played else None,
        average_goals_against=goals_against / played if played else None,
        win_percent=wins * 100 / played if played else None,
        draw_percent=draws * 100 / played if played else None,
        loss_percent=losses * 100 / played if played else None,
        clean_sheets=clean_sheets,
        failed_to_score=failed_to_score,
        form_last_5="".join(results[-5:]),
        form_last_10="".join(results[-10:]),
        home_played=home_played,
        home_won=home_won,
        home_drawn=home_drawn,
        home_lost=home_lost,
        home_points=home_points,
        away_played=away_played,
        away_won=away_won,
        away_drawn=away_drawn,
        away_lost=away_lost,
        away_points=away_points,
        unbeaten_streak=unbeaten_streak,
        win_streak=win_streak,
        scoring_streak=scoring_streak,
    )


def _streak(values: list[str], predicate) -> int:
    streak = 0
    for value in reversed(values):
        if not predicate(value):
            break
        streak += 1
    return streak
