"""Persistent entities. Nullable provider statistics deliberately remain NULL when unknown."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from football_analytics.database.base import Base

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DataSource(TimestampMixin, Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    requires_api_key: Mapped[bool] = mapped_column(Boolean, nullable=False)
    license_name: Mapped[str | None] = mapped_column(String(100))
    attribution_text: Mapped[str | None] = mapped_column(Text)
    terms_url: Mapped[str | None] = mapped_column(Text)


class Competition(TimestampMixin, Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country_name: Mapped[str | None] = mapped_column(String(100))
    region_name: Mapped[str | None] = mapped_column(String(100))
    competition_type: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    display_order: Mapped[int | None] = mapped_column(SmallInteger)


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("start_year", "end_year"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    start_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    end_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class CompetitionSeason(Base):
    __tablename__ = "competition_seasons"
    __table_args__ = (UniqueConstraint("competition_id", "season_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), nullable=False)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    current_matchday: Mapped[int | None] = mapped_column(SmallInteger)
    current_stage: Mapped[str | None] = mapped_column(String(100))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Team(TimestampMixin, Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(150), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(100))
    short_code: Mapped[str | None] = mapped_column(String(20))
    country_name: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    venue_name: Mapped[str | None] = mapped_column(String(150))
    founded_year: Mapped[int | None] = mapped_column(SmallInteger)
    crest_url: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("source_id", "normalized_alias"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    alias: Mapped[str] = mapped_column(String(150), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(150), nullable=False)


class TeamCoach(Base):
    __tablename__ = "team_coaches"
    __table_args__ = (UniqueConstraint("team_id", "source_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(100))
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Player(TimestampMixin, Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    nationality: Mapped[str | None] = mapped_column(String(100))
    birth_date: Mapped[date | None] = mapped_column(Date)
    birth_place: Mapped[str | None] = mapped_column(String(100))
    height: Mapped[str | None] = mapped_column(String(20))
    weight: Mapped[str | None] = mapped_column(String(20))
    position: Mapped[str | None] = mapped_column(String(50))
    preferred_foot: Mapped[str | None] = mapped_column(String(16))
    photo_url: Mapped[str | None] = mapped_column(Text)
    profile_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourcePlayer(Base):
    __tablename__ = "source_players"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)


class PlayerTeamSeason(Base):
    __tablename__ = "player_team_seasons"
    __table_args__ = (
        UniqueConstraint("player_id", "team_id", "competition_season_id", "source_id"),
        Index("ix_player_team_seasons_season_team", "competition_season_id", "team_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    competition_season_id: Mapped[int] = mapped_column(
        ForeignKey("competition_seasons.id"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    shirt_number: Mapped[int | None] = mapped_column(SmallInteger)
    position: Mapped[str | None] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlayerCompetitionSeasonStatistic(Base):
    __tablename__ = "player_competition_season_statistics"
    __table_args__ = (
        UniqueConstraint("player_id", "competition_season_id", "source_id"),
        Index("ix_player_season_goals", "competition_season_id", "goals"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    competition_season_id: Mapped[int] = mapped_column(
        ForeignKey("competition_seasons.id"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    appearances: Mapped[int | None] = mapped_column(SmallInteger)
    starts: Mapped[int | None] = mapped_column(SmallInteger)
    substitute_appearances: Mapped[int | None] = mapped_column(SmallInteger)
    minutes_played: Mapped[int | None] = mapped_column(SmallInteger)
    goals: Mapped[int | None] = mapped_column(SmallInteger)
    assists: Mapped[int | None] = mapped_column(SmallInteger)
    penalties_scored: Mapped[int | None] = mapped_column(SmallInteger)
    yellow_cards: Mapped[int | None] = mapped_column(SmallInteger)
    red_cards: Mapped[int | None] = mapped_column(SmallInteger)
    tackles_won: Mapped[int | None] = mapped_column(SmallInteger)
    shots_total: Mapped[int | None] = mapped_column(SmallInteger)
    shots_on_target: Mapped[int | None] = mapped_column(SmallInteger)
    goals_conceded: Mapped[int | None] = mapped_column(SmallInteger)
    clean_sheets: Mapped[int | None] = mapped_column(SmallInteger)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceCompetition(Base):
    __tablename__ = "source_competitions"
    __table_args__ = (UniqueConstraint("source_id", "competition_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(100))
    external_code: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_VALUE)


class SourceCompetitionSeason(Base):
    __tablename__ = "source_competition_seasons"
    __table_args__ = (UniqueConstraint("source_id", "competition_season_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    competition_season_id: Mapped[int] = mapped_column(
        ForeignKey("competition_seasons.id"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(100))
    external_code: Mapped[str | None] = mapped_column(String(100))
    external_season: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_VALUE)


class SourceTeam(Base):
    __tablename__ = "source_teams"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    external_code: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_VALUE)


class Match(TimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (
        Index("ix_matches_kickoff_utc", "kickoff_utc"),
        Index("ix_matches_competition_season_status", "competition_season_id", "status"),
        Index("ix_matches_home_team_kickoff", "home_team_id", "kickoff_utc"),
        Index("ix_matches_away_team_kickoff", "away_team_id", "kickoff_utc"),
        Index("ix_matches_status", "status"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    competition_season_id: Mapped[int] = mapped_column(
        ForeignKey("competition_seasons.id"), nullable=False
    )
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    matchday: Mapped[int | None] = mapped_column(SmallInteger)
    round_label: Mapped[str | None] = mapped_column(String(100))
    stage: Mapped[str | None] = mapped_column(String(100))
    group_name: Mapped[str | None] = mapped_column(String(100))
    leg: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    venue_name: Mapped[str | None] = mapped_column(String(150))
    home_score_ft: Mapped[int | None] = mapped_column(SmallInteger)
    away_score_ft: Mapped[int | None] = mapped_column(SmallInteger)
    home_score_ht: Mapped[int | None] = mapped_column(SmallInteger)
    away_score_ht: Mapped[int | None] = mapped_column(SmallInteger)
    home_score_et: Mapped[int | None] = mapped_column(SmallInteger)
    away_score_et: Mapped[int | None] = mapped_column(SmallInteger)
    home_score_pen: Mapped[int | None] = mapped_column(SmallInteger)
    away_score_pen: Mapped[int | None] = mapped_column(SmallInteger)
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceMatch(Base):
    __tablename__ = "source_matches"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_hash: Mapped[str | None] = mapped_column(String(64))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MatchTeamStatistic(Base):
    __tablename__ = "match_team_statistics"
    __table_args__ = (UniqueConstraint("match_id", "team_id", "source_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    possession: Mapped[float | None] = mapped_column(Numeric(5, 2))
    shots_total: Mapped[int | None] = mapped_column(SmallInteger)
    shots_on_target: Mapped[int | None] = mapped_column(SmallInteger)
    shots_off_target: Mapped[int | None] = mapped_column(SmallInteger)
    shots_blocked: Mapped[int | None] = mapped_column(SmallInteger)
    shots_inside_box: Mapped[int | None] = mapped_column(SmallInteger)
    shots_outside_box: Mapped[int | None] = mapped_column(SmallInteger)
    corners: Mapped[int | None] = mapped_column(SmallInteger)
    fouls: Mapped[int | None] = mapped_column(SmallInteger)
    offsides: Mapped[int | None] = mapped_column(SmallInteger)
    yellow_cards: Mapped[int | None] = mapped_column(SmallInteger)
    red_cards: Mapped[int | None] = mapped_column(SmallInteger)
    passes_total: Mapped[int | None] = mapped_column(SmallInteger)
    passes_accurate: Mapped[int | None] = mapped_column(SmallInteger)
    goalkeeper_saves: Mapped[int | None] = mapped_column(SmallInteger)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeamCompetitionSeasonStatistic(Base):
    """Provider-supplied team totals/averages for a competition season."""

    __tablename__ = "team_competition_season_statistics"
    __table_args__ = (
        UniqueConstraint("team_id", "competition_season_id", "source_id"),
        Index("ix_team_competition_season_stats_season", "competition_season_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    competition_season_id: Mapped[int] = mapped_column(
        ForeignKey("competition_seasons.id"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    goals_average: Mapped[float | None] = mapped_column(Numeric(6, 2))
    goals_conceded_average: Mapped[float | None] = mapped_column(Numeric(6, 2))
    clean_sheets: Mapped[int | None] = mapped_column(SmallInteger)
    possession_percentage: Mapped[float | None] = mapped_column(Numeric(5, 2))
    corners_average: Mapped[float | None] = mapped_column(Numeric(6, 2))
    penalties_scored: Mapped[int | None] = mapped_column(SmallInteger)
    penalties_attempted: Mapped[int | None] = mapped_column(SmallInteger)
    penalties_conceded: Mapped[int | None] = mapped_column(SmallInteger)
    penalties_conceded_attempted: Mapped[int | None] = mapped_column(SmallInteger)
    yellow_cards: Mapped[int | None] = mapped_column(SmallInteger)
    red_cards: Mapped[int | None] = mapped_column(SmallInteger)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlayerMatchStatistic(Base):
    """Player box-score values tied to a single match and provider."""

    __tablename__ = "player_match_statistics"
    __table_args__ = (
        UniqueConstraint("match_id", "player_id", "source_id"),
        Index("ix_player_match_statistics_player_match", "player_id", "match_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    minutes_played: Mapped[int | None] = mapped_column(SmallInteger)
    is_substitute: Mapped[bool | None] = mapped_column(Boolean)
    shirt_number: Mapped[int | None] = mapped_column(SmallInteger)
    position: Mapped[str | None] = mapped_column(String(50))
    goals: Mapped[int | None] = mapped_column(SmallInteger)
    assists: Mapped[int | None] = mapped_column(SmallInteger)
    yellow_cards: Mapped[int | None] = mapped_column(SmallInteger)
    red_cards: Mapped[int | None] = mapped_column(SmallInteger)
    shots_total: Mapped[int | None] = mapped_column(SmallInteger)
    shots_on_target: Mapped[int | None] = mapped_column(SmallInteger)
    goals_conceded: Mapped[int | None] = mapped_column(SmallInteger)
    clean_sheet: Mapped[bool | None] = mapped_column(Boolean)
    goalkeeper_saves: Mapped[int | None] = mapped_column(SmallInteger)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MatchEvent(Base):
    __tablename__ = "match_events"
    __table_args__ = (Index("ix_match_events_player_id", "player_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    minute: Mapped[int | None] = mapped_column(SmallInteger)
    added_time: Mapped[int | None] = mapped_column(SmallInteger)
    player_name: Mapped[str | None] = mapped_column(String(150))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    assist_name: Mapped[str | None] = mapped_column(String(150))
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StandingsSnapshot(Base):
    __tablename__ = "standings_snapshots"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    competition_season_id: Mapped[int] = mapped_column(
        ForeignKey("competition_seasons.id"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(100))
    matchday: Mapped[int | None] = mapped_column(SmallInteger)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StandingRow(Base):
    __tablename__ = "standing_rows"
    __table_args__ = (UniqueConstraint("snapshot_id", "team_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("standings_snapshots.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    played: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    won: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    drawn: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lost: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    goals_for: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    goals_against: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    goal_difference: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    points: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    form: Mapped[str | None] = mapped_column(String(30))


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    date_from: Mapped[date | None] = mapped_column(Date)
    date_to: Mapped[date | None] = mapped_column(Date)
    requests_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    summary: Mapped[str | None] = mapped_column(Text)


class SyncRunItem(Base):
    __tablename__ = "sync_run_items"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    sync_run_id: Mapped[UUID] = mapped_column(ForeignKey("sync_runs.id"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    competition_id: Mapped[int | None] = mapped_column(ForeignKey("competitions.id"))
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    requests_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str | None] = mapped_column(Text)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_a_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    source_b_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    value_a: Mapped[dict | None] = mapped_column(JSON_VALUE)
    value_b: Mapped[dict | None] = mapped_column(JSON_VALUE)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
