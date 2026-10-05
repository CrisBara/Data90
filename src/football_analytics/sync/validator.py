from football_analytics.domain.dto import NormalizedMatch
from football_analytics.domain.exceptions import FootballAnalyticsError


class InvalidProviderRecord(FootballAnalyticsError):
    """A normalized provider record violates domain invariants."""


def validate_match(match: NormalizedMatch) -> None:
    if match.kickoff_utc.tzinfo is None or match.kickoff_utc.utcoffset() is None:
        raise InvalidProviderRecord("kickoff_utc debe incluir zona horaria")
    if not match.home_external_id or not match.away_external_id:
        raise InvalidProviderRecord("El partido debe identificar ambos equipos")
    if match.home_external_id == match.away_external_id:
        raise InvalidProviderRecord("El equipo local y visitante no pueden ser el mismo")
    for value in (
        match.home_score_ft,
        match.away_score_ft,
        match.home_score_ht,
        match.away_score_ht,
        match.home_score_et,
        match.away_score_et,
        match.home_score_pen,
        match.away_score_pen,
    ):
        if value is not None and value < 0:
            raise InvalidProviderRecord("Los goles conocidos no pueden ser negativos")
