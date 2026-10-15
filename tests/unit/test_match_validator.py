from datetime import UTC, datetime

import pytest

from data90.domain.dto import NormalizedMatch
from data90.domain.enums import MatchStatus
from data90.sync.validator import InvalidProviderRecord, validate_match


def _match(**changes: object) -> NormalizedMatch:
    values: dict[str, object] = {
        "source_code": "fixture",
        "external_id": "match-1",
        "competition_code": "PL",
        "season_start_year": 2026,
        "kickoff_utc": datetime(2026, 8, 1, tzinfo=UTC),
        "home_external_id": "home",
        "away_external_id": "away",
        "home_name": "Home FC",
        "away_name": "Away FC",
        "status": MatchStatus.SCHEDULED,
    }
    values.update(changes)
    return NormalizedMatch(**values)  # type: ignore[arg-type]


def test_valid_match_preserves_unknown_scores_as_null() -> None:
    match = _match()
    validate_match(match)
    assert match.home_score_ft is None


def test_rejects_naive_kickoff() -> None:
    with pytest.raises(InvalidProviderRecord, match="zona horaria"):
        validate_match(_match(kickoff_utc=datetime(2026, 8, 1)))


def test_rejects_negative_score() -> None:
    with pytest.raises(InvalidProviderRecord, match="negativos"):
        validate_match(_match(home_score_ft=-1))
