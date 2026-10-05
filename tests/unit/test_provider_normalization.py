from datetime import date, datetime

from football_analytics.domain.dto import NormalizedMatch
from football_analytics.domain.enums import MatchStatus
from football_analytics.domain.exceptions import ProviderError
from football_analytics.providers.football_data import FootballDataProvider
from football_analytics.providers.openligadb import OpenLigaDBProvider


def test_football_data_match_is_normalized_and_keeps_missing_stats_unknown() -> None:
    provider = FootballDataProvider(token="fixture-token")
    provider._get = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "matches": [
            {
                "id": 10,
                "utcDate": "2026-08-01T16:00:00Z",
                "status": "FINISHED",
                "matchday": 1,
                "homeTeam": {"id": 1, "name": "Home FC"},
                "awayTeam": {"id": 2, "name": "Away FC"},
                "score": {
                    "fullTime": {"home": 2, "away": 1},
                    "halfTime": {"home": 1, "away": 1},
                },
            }
        ]
    }
    match = list(provider.get_matches("PL", 2026, date(2026, 8, 1), date(2026, 8, 2)))[0]
    assert isinstance(match, NormalizedMatch)
    assert match.status == MatchStatus.FINISHED
    assert (match.home_score_ft, match.away_score_ft) == (2, 1)
    assert match.home_score_et is None


def test_openligadb_uses_source_specific_ids_and_score_types() -> None:
    provider = OpenLigaDBProvider()
    provider.request_json = lambda _path: [  # type: ignore[method-assign]
        {
            "matchID": 72,
            "matchDateTimeUTC": "2026-08-01T16:00:00Z",
            "lastUpdateDateTime": "2026-08-01T18:00:00Z",
            "matchIsFinished": True,
            "group": {"groupName": "Gruppe 1", "groupOrderID": 1},
            "team1": {"teamId": 501, "teamName": "Home FC"},
            "team2": {"teamId": 502, "teamName": "Away FC"},
            "matchResults": [
                {"resultTypeID": 2, "pointsTeam1": 2, "pointsTeam2": 1},
                {"resultTypeID": 1, "pointsTeam1": 1, "pointsTeam2": 1},
            ],
        }
    ]  # type: ignore[method-assign]
    match = list(provider.get_matches("UEL", 2026, "validated-shortcut"))[0]
    assert match.external_id == "72"
    assert match.home_external_id == "501"
    assert match.home_score_ft == 2
    assert match.home_score_ht == 1
    assert match.status == MatchStatus.FINISHED


def test_football_data_rejects_records_without_a_kickoff() -> None:
    provider = FootballDataProvider(token="fixture-token")
    provider._get = lambda *_args, **_kwargs: {"matches": [{"id": 10}]}  # type: ignore[method-assign]
    try:
        list(provider.get_matches("PL", 2026, date(2026, 8, 1), date(2026, 8, 2)))
    except ProviderError as error:
        assert "utcDate" in str(error)
    else:
        raise AssertionError("Se esperaba rechazo de un partido sin fecha")


def test_openligadb_shortcut_is_read_from_a_validated_season_catalogue() -> None:
    provider = OpenLigaDBProvider()
    provider.get_available_leagues = lambda _season: [  # type: ignore[method-assign]
        {"leagueName": "UEFA Europa League", "leagueShortcut": "uel-verified"}
    ]
    assert provider.validate_league_shortcut(("Europa League", "UEFA Europa League"), 2026) == (
        "uel-verified"
    )


def test_openligadb_validates_season_suffix_in_competition_name() -> None:
    provider = OpenLigaDBProvider()
    provider.get_available_leagues = lambda _season: [  # type: ignore[method-assign]
        {"leagueName": "Europa League 2026/27", "leagueShortcut": "uel2026"}
    ]

    assert provider.validate_league_shortcut(("Europa League", "UEFA Europa League"), 2026) == (
        "uel2026"
    )


def test_openligadb_incremental_sync_skips_groups_without_changes() -> None:
    provider = OpenLigaDBProvider()
    provider.get_available_groups = lambda *_args: [  # type: ignore[method-assign]
        {"groupOrderID": 1},
        {"groupOrderID": 2},
    ]
    provider.get_last_change_date = lambda _shortcut, _season, group: (  # type: ignore[method-assign]
        datetime.fromisoformat("2026-10-01T10:00:00+00:00")
        if group == 1
        else datetime.fromisoformat("2026-10-02T10:00:00+00:00")
    )
    paths = []

    def request_json(path: str) -> list[dict]:
        paths.append(path)
        return []

    provider.request_json = request_json  # type: ignore[method-assign]
    matches, updates, skipped = provider.get_changed_matches(
        "UEL", 2026, "verified", {"1": "2026-10-01T10:00:00+00:00"}
    )

    assert matches == []
    assert updates == {"2": "2026-10-02T10:00:00+00:00"}
    assert skipped == 1
    assert paths == ["getmatchdata/verified/2026/2"]


def test_openligadb_incremental_sync_skips_groups_without_change_marker() -> None:
    provider = OpenLigaDBProvider()
    provider.get_available_groups = lambda *_args: [  # type: ignore[method-assign]
        {"groupOrderID": 9}
    ]

    def no_change_marker(*_args):
        error = ProviderError("not available")
        error.status_code = 404
        raise error

    provider.get_last_change_date = no_change_marker  # type: ignore[method-assign]
    provider.request_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("No se descargan grupos que carecen de marcador")
    )

    matches, updates, skipped = provider.get_changed_matches("UEL", 2026, "uel2026", {})

    assert matches == []
    assert updates == {}
    assert skipped == 1
