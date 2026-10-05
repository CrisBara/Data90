"""OpenLigaDB adapter; shortcuts are supplied from the source-season database mapping."""

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from football_analytics.domain.dto import NormalizedMatch, NormalizedStanding, NormalizedTeam
from football_analytics.domain.enums import MatchStatus
from football_analytics.domain.exceptions import ProviderError
from football_analytics.providers.base import HttpProvider
from football_analytics.sync.normalizer import normalize_name


class OpenLigaDBProvider(HttpProvider):
    source_code = "openligadb"
    base_url = "https://api.openligadb.de"

    @staticmethod
    def _date(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed

    @staticmethod
    def _int(value: Any) -> int | None:
        return int(value) if value is not None else None

    def get_last_change_date(self, shortcut: str, season: int, group_order: int) -> datetime | None:
        value = self.request_json(f"getlastchangedate/{shortcut}/{season}/{group_order}")
        if not value:
            return None
        return self._date(value) if isinstance(value, str) else None

    def get_available_groups(self, shortcut: str, season: int) -> list[dict[str, Any]]:
        result = self.request_json(f"getavailablegroups/{shortcut}/{season}")
        if not isinstance(result, list):
            raise ProviderError("OpenLigaDB devolvió jornadas con formato inesperado")
        return result

    def get_current_group(self, shortcut: str) -> dict[str, Any] | None:
        result = self.request_json(f"getcurrentgroup/{shortcut}")
        return result if isinstance(result, dict) else None

    def get_available_leagues(self, season: int) -> list[dict[str, Any]]:
        result = self.request_json(f"getavailableleagues/{season}")
        if not isinstance(result, list):
            raise ProviderError("OpenLigaDB devolvió competiciones con formato inesperado")
        return result

    def validate_league_shortcut(self, competition_names: tuple[str, ...], season: int) -> str:
        """Resolve an exact name, allowing the season suffix used by the provider catalog."""
        accepted_names = {normalize_name(name) for name in competition_names}
        matches = [
            league
            for league in self.get_available_leagues(season)
            if normalize_name(league.get("leagueName", "")) in accepted_names
            or normalize_name(re.sub(r"\s+\d{4}(?:/\d{2,4})?$", "", league.get("leagueName", "")))
            in accepted_names
        ]
        if len(matches) != 1:
            raise ProviderError(
                f"Shortcut de OpenLigaDB no validado para temporada {season} "
                f"({len(matches)} coincidencias exactas)"
            )
        shortcut = matches[0].get("leagueShortcut")
        if not shortcut:
            raise ProviderError("OpenLigaDB encontró la competición sin leagueShortcut")
        return str(shortcut)

    def get_teams(self, shortcut: str, season: int) -> Iterable[NormalizedTeam]:
        result = self.request_json(f"getavailableteams/{shortcut}/{season}")
        if not isinstance(result, list):
            raise ProviderError("OpenLigaDB devolvió equipos con formato inesperado")
        return [
            NormalizedTeam(
                self.source_code,
                str(team["teamId"]),
                team.get("teamName", ""),
                team.get("shortName"),
                None,
                crest_url=team.get("teamIconUrl"),
                aliases=(team["shortName"],) if team.get("shortName") else (),
            )
            for team in result
        ]

    def get_matches(
        self,
        competition_code: str,
        season_start_year: int,
        shortcut: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> Iterable[NormalizedMatch]:
        result = self.request_json(f"getmatchdata/{shortcut}/{season_start_year}")
        if not isinstance(result, list):
            raise ProviderError("OpenLigaDB devolvió partidos con formato inesperado")
        return self._normalize_matches(
            result, competition_code, season_start_year, date_from, date_to
        )

    def get_changed_matches(
        self,
        competition_code: str,
        season_start_year: int,
        shortcut: str,
        known_last_changed: dict[str, str],
    ) -> tuple[list[NormalizedMatch], dict[str, str], int]:
        """Fetch only groups whose provider-reported last-change marker has moved."""
        groups = self.get_available_groups(shortcut, season_start_year)
        matches: list[NormalizedMatch] = []
        updated_markers: dict[str, str] = {}
        skipped_groups = 0
        for group in groups:
            group_order = group.get("groupOrderID")
            if group_order is None:
                continue
            try:
                marker = self.get_last_change_date(shortcut, season_start_year, int(group_order))
            except ProviderError as error:
                if getattr(error, "status_code", None) == 404:
                    skipped_groups += 1
                    continue
                raise
            group_key = str(group_order)
            marker_value = marker.isoformat() if marker is not None else None
            if marker_value is not None and known_last_changed.get(group_key) == marker_value:
                skipped_groups += 1
                continue
            result = self.request_json(
                f"getmatchdata/{shortcut}/{season_start_year}/{int(group_order)}"
            )
            if not isinstance(result, list):
                raise ProviderError("OpenLigaDB devolvió partidos con formato inesperado")
            matches.extend(self._normalize_matches(result, competition_code, season_start_year))
            if marker_value is not None:
                updated_markers[group_key] = marker_value
        return matches, updated_markers, skipped_groups

    def _normalize_matches(
        self,
        result: list[dict[str, Any]],
        competition_code: str,
        season_start_year: int,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[NormalizedMatch]:
        matches = []
        for item in result:
            kickoff = self._date(item.get("matchDateTimeUTC"))
            if kickoff is None:
                continue
            if (date_from and kickoff.date() < date_from) or (date_to and kickoff.date() > date_to):
                continue
            home, away = item.get("team1") or {}, item.get("team2") or {}
            results = item.get("matchResults") or []
            full = next((row for row in results if row.get("resultTypeID") == 2), None)
            half = next((row for row in results if row.get("resultTypeID") == 1), None)
            group = item.get("group") or {}
            is_finished = bool(item.get("matchIsFinished"))
            location = item.get("location") or {}
            matches.append(
                NormalizedMatch(
                    source_code=self.source_code,
                    external_id=str(item["matchID"]),
                    competition_code=competition_code,
                    season_start_year=season_start_year,
                    kickoff_utc=kickoff,
                    home_external_id=str(home.get("teamId", "")),
                    away_external_id=str(away.get("teamId", "")),
                    home_name=home.get("teamName", ""),
                    away_name=away.get("teamName", ""),
                    status=MatchStatus.FINISHED if is_finished else MatchStatus.SCHEDULED,
                    matchday=group.get("groupOrderID"),
                    round_label=group.get("groupName"),
                    stage=group.get("groupName"),
                    home_score_ft=self._int(full.get("pointsTeam1")) if full else None,
                    away_score_ft=self._int(full.get("pointsTeam2")) if full else None,
                    home_score_ht=self._int(half.get("pointsTeam1")) if half else None,
                    away_score_ht=self._int(half.get("pointsTeam2")) if half else None,
                    source_updated_at=self._date(item.get("lastUpdateDateTime")),
                    venue_name=location.get("locationStadium"),
                )
            )
        return matches

    def get_standings(
        self, competition_code: str, season_start_year: int, shortcut: str
    ) -> Iterable[NormalizedStanding]:
        result = self.request_json(f"getbltable/{shortcut}/{season_start_year}")
        if not isinstance(result, list):
            raise ProviderError("OpenLigaDB devolvió tabla con formato inesperado")
        rows = []
        for position, row in enumerate(result, start=1):
            played = int(row.get("matches", 0))
            rows.append(
                NormalizedStanding(
                    self.source_code,
                    competition_code,
                    season_start_year,
                    str(row.get("teamInfoId", "")),
                    position,
                    played,
                    int(row.get("won", 0)),
                    int(row.get("draw", 0)),
                    int(row.get("lost", 0)),
                    int(row.get("goals", 0)),
                    int(row.get("opponentGoals", 0)),
                    int(row.get("goalDiff", 0)),
                    int(row.get("points", 0)),
                )
            )
        return rows
