from datetime import UTC, datetime

import pytest

from football_analytics.domain.enums import MatchStatus
from football_analytics.domain.exceptions import ProviderError
from football_analytics.providers.openfootball import OpenFootballProvider


def test_openfootball_normalizes_local_kickoff_and_result(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenFootballProvider()
    calls = []
    payload = {
        "matches": [
            {
                "round": "Matchday 1",
                "date": "2025-08-16",
                "time": "12:30",
                "team1": "Arsenal FC",
                "team2": "Manchester United FC",
                "score": {"ft": [2, 0], "ht": [1, 0]},
            }
        ]
    }
    monkeypatch.setattr(
        provider,
        "request_json",
        lambda path: calls.append(path) or payload,
    )

    match = list(provider.get_matches("PL", 2025, "en.1.json"))[0]

    assert match.kickoff_utc == datetime(2025, 8, 16, 11, 30, tzinfo=UTC)
    assert match.status == MatchStatus.FINISHED
    assert (match.home_score_ft, match.away_score_ft) == (2, 0)
    assert (match.home_score_ht, match.away_score_ht) == (1, 0)
    assert match.matchday == 1
    assert match.home_external_id.startswith("name:")
    assert calls == ["openfootball/football.json/master/2025-26/en.1.json"]


def test_openfootball_does_not_treat_score_array_as_a_final_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenFootballProvider()
    monkeypatch.setattr(
        provider,
        "request_json",
        lambda _path: {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2000-08-16",
                    "time": "12:30",
                    "team1": "Arsenal FC",
                    "team2": "Manchester United FC",
                    "score": [0, 0],
                }
            ]
        },
    )

    match = list(provider.get_matches("PL", 2000, "en.1.json"))[0]

    assert match.status == MatchStatus.UNKNOWN
    assert match.home_score_ft is None
    assert match.away_score_ft is None


def test_openfootball_bulk_import_can_skip_unusable_kickoff_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenFootballProvider()
    monkeypatch.setattr(
        provider,
        "request_json",
        lambda _path: {
            "matches": [
                {
                    "date": "2020-03-01",
                    "team1": "Arsenal FC",
                    "team2": "Chelsea FC",
                },
                {
                    "date": "2020-03-08",
                    "time": "16:30",
                    "team1": "Arsenal FC",
                    "team2": "Chelsea FC",
                    "score": {"ft": [2, 1]},
                },
            ]
        },
    )

    matches = list(provider.get_matches("PL", 2019, "en.1.json", skip_incomplete=True))

    assert len(matches) == 1
    assert provider.incomplete_match_count == 1


def test_openfootball_rejects_unconfigured_timezone_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenFootballProvider()
    monkeypatch.setattr(
        provider,
        "request_json",
        lambda _path: pytest.fail("No debe consultar antes de validar la zona horaria"),
    )

    with pytest.raises(ProviderError, match="zona horaria configurada"):
        list(provider.get_matches("UCL", 2025, "en.1.json"))


def test_openfootball_rejects_dataset_paths_outside_filename_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenFootballProvider()
    monkeypatch.setattr(
        provider,
        "request_json",
        lambda _path: pytest.fail("No debe solicitar una ruta no permitida"),
    )

    with pytest.raises(ProviderError, match="(?i)archivo de temporada"):
        list(provider.get_matches("PL", 2025, "../en.1.json"))


def test_openfootball_team_catalog_is_deduplicated_and_dataset_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenFootballProvider()
    calls = []
    monkeypatch.setattr(
        provider,
        "request_json",
        lambda path: calls.append(path)
        or {
            "matches": [
                {
                    "date": "2025-08-16",
                    "time": "12:30",
                    "team1": "Arsenal FC",
                    "team2": "Manchester United FC",
                },
                {
                    "date": "2025-08-23",
                    "time": "12:30",
                    "team1": "Arsenal FC",
                    "team2": "Liverpool FC",
                },
            ]
        },
    )

    teams = list(provider.get_teams("PL", 2025, "en.1.json"))
    list(provider.get_matches("PL", 2025, "en.1.json"))

    assert [team.canonical_name for team in teams] == [
        "Arsenal FC",
        "Manchester United FC",
        "Liverpool FC",
    ]
    assert len(calls) == 1


def test_openfootball_team_catalog_can_skip_incomplete_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenFootballProvider()
    monkeypatch.setattr(
        provider,
        "request_json",
        lambda _path: {
            "matches": [
                {
                    "date": "2025-08-16",
                    "time": "12:30",
                    "team1": "Arsenal FC",
                    "team2": "Liverpool FC",
                },
                {
                    "date": "2025-08-23",
                    "team1": "Missing kickoff FC",
                    "team2": "Chelsea FC",
                },
            ]
        },
    )

    teams = list(
        provider.get_teams("PL", 2025, "en.1.json", skip_incomplete=True)
    )

    assert [team.canonical_name for team in teams] == ["Arsenal FC", "Liverpool FC"]
