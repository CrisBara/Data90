"""Provider-neutral records exchanged by adapters and the sync layer."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from data90.domain.enums import MatchStatus


@dataclass(frozen=True, slots=True)
class NormalizedCompetition:
    source_code: str
    external_id: str | None
    external_code: str | None
    competition_code: str
    name: str
    metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class NormalizedSeason:
    source_code: str
    competition_code: str
    external_id: str | None
    external_code: str | None
    external_season: str
    start_year: int
    end_year: int
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class NormalizedTeam:
    source_code: str
    external_id: str
    canonical_name: str
    short_name: str | None = None
    short_code: str | None = None
    country_name: str | None = None
    city: str | None = None
    venue_name: str | None = None
    founded_year: int | None = None
    crest_url: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedMatch:
    source_code: str
    external_id: str
    competition_code: str
    season_start_year: int
    kickoff_utc: datetime
    home_external_id: str
    away_external_id: str
    home_name: str
    away_name: str
    status: MatchStatus
    matchday: int | None = None
    round_label: str | None = None
    stage: str | None = None
    group_name: str | None = None
    leg: int | None = None
    home_score_ft: int | None = None
    away_score_ft: int | None = None
    home_score_ht: int | None = None
    away_score_ht: int | None = None
    home_score_et: int | None = None
    away_score_et: int | None = None
    home_score_pen: int | None = None
    away_score_pen: int | None = None
    source_updated_at: datetime | None = None
    venue_name: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedStanding:
    source_code: str
    competition_code: str
    season_start_year: int
    team_external_id: str
    position: int
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    scope: str = "TOTAL"
    group_name: str | None = None
    matchday: int | None = None
    form: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedPlayerSeasonStatistic:
    source_code: str
    external_id: str
    display_name: str
    competition_code: str
    season_start_year: int
    goals: int | None = None
    appearances: int | None = None
    starts: int | None = None
    substitute_appearances: int | None = None
    minutes_played: int | None = None
    assists: int | None = None
    penalties_scored: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    tackles_won: int | None = None
    shots_total: int | None = None
    shots_on_target: int | None = None
    goals_conceded: int | None = None
    clean_sheets: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizedTeamStatistic:
    source_code: str
    external_match_id: str
    team_external_id: str
    possession: Decimal | None = None
    shots_total: int | None = None
    shots_on_target: int | None = None
    shots_off_target: int | None = None
    shots_blocked: int | None = None
    shots_inside_box: int | None = None
    shots_outside_box: int | None = None
    corners: int | None = None
    fouls: int | None = None
    offsides: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    passes_total: int | None = None
    passes_accurate: int | None = None
    goalkeeper_saves: int | None = None
