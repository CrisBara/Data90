"""Enrich missing active-squad biographies across active competitions, without touching stats."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Player,
    PlayerTeamSeason,
    Season,
    SourcePlayer,
    SyncRun,
    SyncRunItem,
)
from data90.domain.enums import SyncMode, SyncStatus
from data90.providers.thesportsdb import TheSportsDBProvider
from data90.repositories.players import team_squad_for_team
from data90.sync.normalizer import normalize_name

SessionFactory = Callable[[], Session]
TARGET_CODES = (
    "PL",
    "LALIGA",
    "SERIE_A",
    "BUNDESLIGA",
    "LIGUE_1",
    "EREDIVISIE",
    "UCL",
    "UEL",
)


@dataclass(frozen=True, slots=True)
class TheSportsDBProfileSummary:
    run_id: UUID
    requests_count: int
    players_checked: int
    players_matched: int
    nationality_filled: int
    position_filled: int
    birth_dates_filled: int
    height_filled: int
    weight_filled: int
    skipped_count: int
    error_count: int
    message: str


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _clean(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def sync_thesportsdb_profiles(
    session_factory: SessionFactory,
    provider: TheSportsDBProvider | None = None,
    *,
    competition_codes: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> TheSportsDBProfileSummary:
    """Fill only null profile fields for uniquely matched current roster players.

    Season statistics and memberships are deliberately not imported: the free
    provider's player-stat endpoint is largely historical and must not be
    presented as current-season data.
    """
    provider = provider or TheSportsDBProvider()
    now = now or datetime.now(UTC)
    selected = tuple(
        code for code in TARGET_CODES if competition_codes is None or code in competition_codes
    )
    with session_factory.begin() as session:
        source = session.scalar(select(DataSource).where(DataSource.code == provider.source_code))
        if source is None:
            source = DataSource(
                code=provider.source_code,
                name="TheSportsDB",
                base_url=provider.base_url,
                priority=3,
                enabled=True,
                requires_api_key=False,
                license_name="Free community API; metadata sourced from a crowd-sourced database",
                attribution_text="Player profile metadata: TheSportsDB",
                terms_url="https://api.thesportsdb.com/docs_terms_of_use.php",
            )
            session.add(source)
            session.flush()
        source_id = source.id
        run = SyncRun(mode=SyncMode.MANUAL.value, status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        run_id = run.id

    checked = matched = skipped = errors = 0
    filled = {"nationality": 0, "position": 0, "birth_date": 0, "height": 0, "weight": 0}
    item_rows: list[tuple[int | None, int, str, int, int, int, str | None]] = []
    for code in selected:
        requests_before = provider.request_count
        checked_before = checked
        matched_before = matched
        skipped_before = skipped
        errors_before = errors
        with session_factory() as session:
            competition = session.scalar(select(Competition).where(Competition.code == code))
            season_id = (
                session.scalar(
                    select(CompetitionSeason.id)
                    .join(Season, Season.id == CompetitionSeason.season_id)
                    .where(
                        CompetitionSeason.competition_id == competition.id, Season.active.is_(True)
                    )
                    .order_by(Season.start_year.desc())
                    .limit(1)
                )
                if competition is not None
                else None
            )
            competition_id = competition.id if competition is not None else None
            if season_id is None:
                item_rows.append(
                    (
                        competition_id,
                        0,
                        "SKIPPED",
                        0,
                        0,
                        1,
                        f"No hay temporada activa para {code}",
                    )
                )
                skipped += 1
                continue
            team_ids = set(
                session.scalars(
                    select(PlayerTeamSeason.team_id)
                    .where(
                        PlayerTeamSeason.competition_season_id == season_id,
                        PlayerTeamSeason.active.is_(True),
                    )
                    .distinct()
                ).all()
            )
            player_rows: dict[int, dict[str, object]] = {}
            for team_id in team_ids:
                for member in team_squad_for_team(session, team_id):
                    player = session.get(Player, member.player_id)
                    if player is None:
                        continue
                    player_rows.setdefault(
                        player.id,
                        {
                            "name": player.display_name,
                            "nationality": player.nationality or member.nationality,
                            "position": player.position or member.position,
                            "birth_date": player.birth_date or member.birth_date,
                            "height": player.height,
                            "weight": player.weight,
                        },
                    )
            # The UI already de-duplicates provider identities per club. Apply
            # the same view here and skip genuine same-name ambiguities.
            ids_by_name: dict[str, set[int]] = defaultdict(set)
            for player_id, entry in player_rows.items():
                ids_by_name[normalize_name(str(entry["name"]))].add(player_id)

        status = SyncStatus.SUCCESS.value
        messages: list[str] = []
        for normalized_name, candidate_ids in ids_by_name.items():
            if len(candidate_ids) != 1:
                skipped += 1
                continue
            player_id = next(iter(candidate_ids))
            entry = player_rows[player_id]
            # The scarce free-tier requests are reserved for actual biography gaps;
            # height/weight are filled opportunistically from those same responses.
            if all(entry[field] is not None for field in ("nationality", "position", "birth_date")):
                continue
            name = str(entry["name"])
            checked += 1
            try:
                matches = provider.search_player(name)
            except Exception as exc:
                errors += 1
                messages.append(f"{name}: {exc.__class__.__name__}")
                continue
            normalized_name = normalize_name(name)
            exact = [
                row
                for row in matches
                if normalize_name(_clean(row.get("strPlayer")) or "") == normalized_name
                and _clean(row.get("idPlayer"))
            ]
            if len(ids_by_name[normalized_name]) != 1 or len(exact) != 1:
                skipped += 1
                continue
            profile = exact[0]
            source_dob = _parse_date(profile.get("dateBorn"))
            if (
                entry["birth_date"] is not None
                and source_dob is not None
                and entry["birth_date"] != source_dob
            ):
                skipped += 1
                continue
            with session_factory.begin() as session:
                player = session.get(Player, player_id)
                if player is None:
                    skipped += 1
                    continue
                identity = session.scalar(
                    select(SourcePlayer).where(
                        SourcePlayer.source_id == source_id,
                        SourcePlayer.external_id == str(profile["idPlayer"]),
                    )
                )
                if identity is not None and identity.player_id != player_id:
                    skipped += 1
                    continue
                if identity is None:
                    session.add(
                        SourcePlayer(
                            source_id=source_id,
                            external_id=str(profile["idPlayer"]),
                            player_id=player_id,
                        )
                    )
                matched += 1
                values = {
                    "nationality": _clean(profile.get("strNationality")),
                    "position": _clean(profile.get("strPosition")),
                    "birth_date": source_dob,
                    "height": _clean(profile.get("strHeight")),
                    "weight": _clean(profile.get("strWeight")),
                }
                changed = False
                for field, value in values.items():
                    if getattr(player, field) is None and value is not None:
                        setattr(player, field, value)
                        filled[field] += 1
                        changed = True
                if changed:
                    player.profile_source_id = source_id
                    player.profile_updated_at = now

        if errors:
            status = SyncStatus.PARTIAL.value
        messages.append(f"{code}: {checked - checked_before} perfiles consultados")
        item_rows.append(
            (
                competition_id,
                provider.request_count - requests_before,
                status,
                (matched - matched_before),
                skipped - skipped_before,
                errors - errors_before,
                "; ".join(messages)[:900] or None,
            )
        )

    total_requests = provider.request_count
    final_status = SyncStatus.PARTIAL if errors else SyncStatus.SUCCESS
    with session_factory.begin() as session:
        for (
            competition_id,
            requests_count,
            status,
            updated_count,
            skipped_count,
            error_count,
            message,
        ) in item_rows:
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source_id,
                    competition_id=competition_id,
                    operation="THESPORTSDB_PLAYER_PROFILES",
                    status=status,
                    requests_count=requests_count,
                    inserted_count=0,
                    updated_count=updated_count,
                    skipped_count=skipped_count,
                    error_count=error_count,
                    message=message,
                )
            )
        run = session.get(SyncRun, run_id)
        run.finished_at = datetime.now(UTC)
        run.status = final_status.value
        run.requests_count = total_requests
        run.inserted_count = 0
        run.updated_count = matched
        run.skipped_count = skipped
        run.error_count = errors
        run.summary = "TheSportsDB: perfiles enriquecidos; estadísticas de temporada sin modificar"

    return TheSportsDBProfileSummary(
        run_id=run_id,
        requests_count=total_requests,
        players_checked=checked,
        players_matched=matched,
        nationality_filled=filled["nationality"],
        position_filled=filled["position"],
        birth_dates_filled=filled["birth_date"],
        height_filled=filled["height"],
        weight_filled=filled["weight"],
        skipped_count=skipped,
        error_count=errors,
        message=(
            "Perfiles TheSportsDB consultados; no se importaron estadísticas históricas "
            "como si fueran actuales."
        ),
    )
