"""OpenFootball JSON adapter for historical and fallback fixture imports."""

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from data90.domain.dto import NormalizedMatch, NormalizedTeam
from data90.domain.enums import MatchStatus
from data90.domain.exceptions import ProviderError
from data90.providers.base import HttpProvider
from data90.sync.normalizer import normalize_name

COMPETITION_TIMEZONES = {
    "PL": "Europe/London",
    "LALIGA": "Europe/Madrid",
    "SERIE_A": "Europe/Rome",
    "BUNDESLIGA": "Europe/Berlin",
    "LIGUE_1": "Europe/Paris",
    "EREDIVISIE": "Europe/Amsterdam",
}
ROUND_NUMBER = re.compile(r"(?:matchday|round|jornada|md)\s*(\d+)", re.IGNORECASE)
DATASET_NAME = re.compile(r"[a-z]{2,4}\.\d(?:-full)?\.json\Z")


def _team_external_id(name: str) -> str:
    normalized = normalize_name(name)
    if not normalized:
        raise ProviderError("OpenFootball publicó un equipo sin nombre válido")
    return f"name:{sha256(normalized.encode('utf-8')).hexdigest()[:32]}"


def _score_pair(value: Any) -> tuple[int | None, int | None]:
    if not isinstance(value, list) or len(value) != 2:
        return None, None
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value):
        return None, None
    return value[0], value[1]


class OpenFootballProvider(HttpProvider):
    """Read season JSON files from the raw GitHub host without guessing timezones."""

    source_code = "openfootball"
    base_url = "https://raw.githubusercontent.com"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._dataset_cache: dict[str, list[dict[str, Any]]] = {}
        self.incomplete_match_count = 0

    def _dataset(
        self, season_start_year: int, dataset_file: str
    ) -> tuple[str, list[dict[str, Any]]]:
        if not DATASET_NAME.fullmatch(dataset_file):
            raise ProviderError("Archivo de temporada OpenFootball inválido")
        season_folder = f"{season_start_year}-{(season_start_year + 1) % 100:02d}"
        path = f"openfootball/football.json/master/{season_folder}/{dataset_file}"
        if path not in self._dataset_cache:
            data = self.request_json(path)
            if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
                raise ProviderError("OpenFootball devolvió un dataset con formato inesperado")
            self._dataset_cache[path] = data["matches"]
        return path, self._dataset_cache[path]

    @staticmethod
    def _timezone(competition_code: str, timezone_name: str | None) -> ZoneInfo:
        zone_name = timezone_name or COMPETITION_TIMEZONES.get(competition_code)
        if not zone_name:
            raise ProviderError(
                f"OpenFootball requiere una zona horaria configurada para {competition_code}"
            )
        try:
            return ZoneInfo(zone_name)
        except ZoneInfoNotFoundError as error:
            raise ProviderError(f"Zona horaria inválida para {competition_code}") from error

    @staticmethod
    def _local_kickoff(item: dict[str, Any], zone: ZoneInfo) -> datetime:
        try:
            match_date = date.fromisoformat(item["date"])
            kickoff_time = time.fromisoformat(item["time"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderError(
                "OpenFootball publicó un partido sin fecha/hora local válida"
            ) from error
        return datetime.combine(match_date, kickoff_time, tzinfo=zone)

    @classmethod
    def _normalize_match(
        cls,
        item: dict[str, Any],
        competition_code: str,
        season_start_year: int,
        zone: ZoneInfo,
    ) -> NormalizedMatch:
        home_name = item.get("team1")
        away_name = item.get("team2")
        if not isinstance(home_name, str) or not home_name.strip():
            raise ProviderError("OpenFootball publicó un partido sin equipo local")
        if not isinstance(away_name, str) or not away_name.strip():
            raise ProviderError("OpenFootball publicó un partido sin equipo visitante")

        local_kickoff = cls._local_kickoff(item, zone)
        round_label = item.get("round")
        round_label = round_label.strip() if isinstance(round_label, str) else None
        round_match = ROUND_NUMBER.search(round_label or "")
        matchday = int(round_match.group(1)) if round_match else None
        score = item.get("score")
        score = score if isinstance(score, dict) else {}
        home_ft, away_ft = _score_pair(score.get("ft"))
        home_ht, away_ht = _score_pair(score.get("ht"))
        home_et, away_et = _score_pair(score.get("et"))
        home_pen, away_pen = _score_pair(score.get("p") or score.get("pens"))

        if home_ft is not None and away_ft is not None:
            status = MatchStatus.FINISHED
        elif local_kickoff > datetime.now(UTC).astimezone(zone):
            status = MatchStatus.SCHEDULED
        else:
            status = MatchStatus.UNKNOWN

        identity = "|".join(
            (
                competition_code,
                str(season_start_year),
                local_kickoff.date().isoformat(),
                normalize_name(round_label or ""),
                normalize_name(home_name),
                normalize_name(away_name),
            )
        )
        return NormalizedMatch(
            source_code=cls.source_code,
            external_id=sha256(identity.encode("utf-8")).hexdigest(),
            competition_code=competition_code,
            season_start_year=season_start_year,
            kickoff_utc=local_kickoff.astimezone(UTC),
            home_external_id=_team_external_id(home_name),
            away_external_id=_team_external_id(away_name),
            home_name=home_name.strip(),
            away_name=away_name.strip(),
            status=status,
            matchday=matchday,
            round_label=round_label,
            stage=round_label,
            home_score_ft=home_ft,
            away_score_ft=away_ft,
            home_score_ht=home_ht,
            away_score_ht=away_ht,
            home_score_et=home_et,
            away_score_et=away_et,
            home_score_pen=home_pen,
            away_score_pen=away_pen,
        )

    def get_matches(
        self,
        competition_code: str,
        season_start_year: int,
        dataset_file: str,
        *,
        timezone_name: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        skip_incomplete: bool = False,
    ) -> Iterable[NormalizedMatch]:
        zone = self._timezone(competition_code, timezone_name)
        _, records = self._dataset(season_start_year, dataset_file)
        matches = []
        self.incomplete_match_count = 0
        for item in records:
            if not isinstance(item, dict):
                if skip_incomplete:
                    self.incomplete_match_count += 1
                    continue
                raise ProviderError("OpenFootball publicó un partido con formato inesperado")
            try:
                local_kickoff = self._local_kickoff(item, zone)
            except ProviderError:
                if skip_incomplete:
                    self.incomplete_match_count += 1
                    continue
                raise
            if date_from is not None and local_kickoff.date() < date_from:
                continue
            if date_to is not None and local_kickoff.date() > date_to:
                continue
            try:
                matches.append(
                    self._normalize_match(item, competition_code, season_start_year, zone)
                )
            except ProviderError:
                if not skip_incomplete:
                    raise
                self.incomplete_match_count += 1
        return matches

    def get_teams(
        self,
        competition_code: str,
        season_start_year: int,
        dataset_file: str,
        *,
        timezone_name: str | None = None,
        skip_incomplete: bool = False,
    ) -> Iterable[NormalizedTeam]:
        zone = self._timezone(competition_code, timezone_name)
        _, records = self._dataset(season_start_year, dataset_file)
        teams: dict[str, NormalizedTeam] = {}
        for item in records:
            if not isinstance(item, dict):
                if skip_incomplete:
                    continue
                raise ProviderError("OpenFootball publicó un partido con formato inesperado")
            if skip_incomplete:
                try:
                    self._local_kickoff(item, zone)
                except ProviderError:
                    continue
            names = [item.get("team1"), item.get("team2")]
            if any(not isinstance(name, str) or not name.strip() for name in names):
                if skip_incomplete:
                    continue
                raise ProviderError("OpenFootball publicó un partido sin nombre de equipo")
            for name in names:
                assert isinstance(name, str)
                team_id = _team_external_id(name.strip())
                teams.setdefault(
                    team_id,
                    NormalizedTeam(
                        source_code=self.source_code,
                        external_id=team_id,
                        canonical_name=name.strip(),
                    ),
                )
        return list(teams.values())
