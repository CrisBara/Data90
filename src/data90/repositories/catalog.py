from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Season,
    SourceCompetitionSeason,
    SourceTeam,
    Team,
    TeamAlias,
)
from data90.domain.dto import NormalizedSeason, NormalizedTeam
from data90.domain.exceptions import EntityResolutionError
from data90.sync.normalizer import normalize_name
from data90.sync.resolver import resolve_team


def upsert_competition_season(session: Session, dto: NormalizedSeason) -> CompetitionSeason:
    competition = session.scalar(
        select(Competition).where(Competition.code == dto.competition_code)
    )
    if competition is None:
        raise EntityResolutionError(f"Competición no configurada: {dto.competition_code}")
    season = session.scalar(
        select(Season).where(
            Season.start_year == dto.start_year,
            Season.end_year == dto.end_year,
        )
    )
    if season is None:
        label = (
            str(dto.start_year)
            if dto.start_year == dto.end_year
            else f"{dto.start_year}/{dto.end_year % 100:02d}"
        )
        season = Season(
            label=label,
            start_year=dto.start_year,
            end_year=dto.end_year,
            start_date=dto.start_date,
            end_date=dto.end_date,
            active=True,
        )
        session.add(season)
        session.flush()
    else:
        season.start_date = dto.start_date or season.start_date
        season.end_date = dto.end_date or season.end_date

    competition_season = session.scalar(
        select(CompetitionSeason).where(
            CompetitionSeason.competition_id == competition.id,
            CompetitionSeason.season_id == season.id,
        )
    )
    if competition_season is None:
        competition_season = CompetitionSeason(
            competition_id=competition.id,
            season_id=season.id,
            status="ACTIVE",
        )
        session.add(competition_season)
        session.flush()

    source = session.scalar(select(DataSource).where(DataSource.code == dto.source_code))
    if source is None:
        raise EntityResolutionError(f"Fuente desconocida: {dto.source_code}")
    mapping = session.scalar(
        select(SourceCompetitionSeason).where(
            SourceCompetitionSeason.source_id == source.id,
            SourceCompetitionSeason.competition_season_id == competition_season.id,
        )
    )
    if mapping is None:
        mapping = SourceCompetitionSeason(
            source_id=source.id,
            competition_season_id=competition_season.id,
            external_season=dto.external_season,
        )
    if dto.external_id is not None:
        mapping.external_id = dto.external_id
    if dto.external_code is not None:
        mapping.external_code = dto.external_code
    mapping.external_season = dto.external_season
    session.add(mapping)
    return competition_season


def upsert_team(session: Session, dto: NormalizedTeam) -> Team:
    source = session.scalar(select(DataSource).where(DataSource.code == dto.source_code))
    if source is None:
        raise EntityResolutionError(f"Fuente desconocida: {dto.source_code}")
    mapped = session.scalar(
        select(SourceTeam).where(
            SourceTeam.source_id == source.id,
            SourceTeam.external_id == dto.external_id,
        )
    )
    if mapped is not None:
        team = session.get(Team, mapped.team_id)
        if team is None:
            raise EntityResolutionError("El mapping de equipo apunta a un equipo inexistente")
    else:
        resolution = resolve_team(session, dto.source_code, dto.external_id, dto.canonical_name)
        if resolution.ambiguous:
            raise EntityResolutionError(f"Alias ambiguo, no se fusionó: {dto.canonical_name}")
        team = session.get(Team, resolution.team_id) if resolution.team_id is not None else None
        if team is None:
            team = Team(canonical_name=dto.canonical_name, active=True)
            session.add(team)
            session.flush()
        mapped = SourceTeam(
            source_id=source.id,
            team_id=team.id,
            external_id=dto.external_id,
        )
        session.add(mapped)

    for field_name in (
        "short_name",
        "short_code",
        "country_name",
        "city",
        "venue_name",
        "founded_year",
        "crest_url",
    ):
        value = getattr(dto, field_name)
        if value is not None:
            setattr(team, field_name, value)

    aliases = {dto.canonical_name, *dto.aliases}
    if dto.short_name:
        aliases.add(dto.short_name)
    for alias in aliases:
        normalized_alias = normalize_name(alias)
        if not normalized_alias:
            continue
        existing_alias = session.scalar(
            select(TeamAlias).where(
                TeamAlias.source_id == source.id,
                TeamAlias.normalized_alias == normalized_alias,
            )
        )
        if existing_alias is None:
            session.add(
                TeamAlias(
                    team_id=team.id,
                    source_id=source.id,
                    alias=alias,
                    normalized_alias=normalized_alias,
                )
            )
    return team
