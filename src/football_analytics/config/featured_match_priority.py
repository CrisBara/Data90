"""Deterministic ranking for the homepage's featured daily match."""

from __future__ import annotations

import re
import unicodedata

from football_analytics.config.league_rivalries import _PAIRS
from football_analytics.config.premier_league_rivalries import _PAIRINGS as _PL_PAIRS

FEATURED_COMPETITION_PRIORITY = (
    "UCL",
    "UEL",
    "PL",
    "LALIGA",
    "SERIE_A",
    "BUNDESLIGA",
    "LIGUE_1",
    "EREDIVISIE",
)

FEATURED_MATCH_POLICY_SETTING = "featured_match_policy"
DEFAULT_FEATURED_MATCH_POLICY = {
    "version": 1,
    "derby_bonus": 22,
    # Brighton–Crystal Palace is a genuine local derby, but should not
    # automatically outrank high-stature fixtures outside the Premier League.
    "derby_bonus_overrides": [
        {
            "competition_code": "PL",
            "teams": ("Brighton & Hove Albion FC", "Crystal Palace FC"),
            "bonus": 6,
        }
    ],
}

# Title stature is curated for the clubs in the tracked competitions. The
# historical tier reflects major domestic and continental honours; the recent
# tier reflects major titles won in the latest completed seasons. Neither uses
# current league position, points, or form.
_TROPHY_TIERS: dict[str, tuple[str, ...]] = {
    "global": (
        "Real Madrid CF",
        "FC Barcelona",
        "FC Bayern München",
        "Liverpool FC",
        "Manchester United FC",
        "Juventus FC",
        "AC Milan",
        "FC Internazionale Milano",
        "AFC Ajax",
    ),
    "global_major": (
        "Manchester City FC",
        "Arsenal FC",
        "Chelsea FC",
        "Paris Saint-Germain FC",
        "Borussia Dortmund",
        "Club Atlético de Madrid",
        "Tottenham Hotspur FC",
        "SL Benfica",
        "FC Porto",
    ),
    "PL": (
        "Everton FC",
        "Newcastle United FC",
        "Aston Villa FC",
        "Tottenham Hotspur FC",
        "Nottingham Forest FC",
        "West Ham United FC",
        "Leeds United FC",
        "Blackburn Rovers FC",
        "Wolverhampton Wanderers FC",
        "Sunderland AFC",
    ),
    "LALIGA": (
        "Club Atlético de Madrid",
        "Valencia CF",
        "Sevilla FC",
        "Athletic Club",
        "Villarreal CF",
        "Real Sociedad de Fútbol",
        "Real Betis Balompié",
    ),
    "SERIE_A": (
        "SSC Napoli",
        "AS Roma",
        "SS Lazio",
        "ACF Fiorentina",
        "Torino FC",
        "Bologna FC 1909",
        "UC Sampdoria",
        "Genoa CFC",
        "Parma Calcio 1913",
    ),
    "BUNDESLIGA": (
        "Bayer 04 Leverkusen",
        "FC Schalke 04",
        "Hamburger SV",
        "SV Werder Bremen",
        "Borussia Mönchengladbach",
        "RB Leipzig",
        "VfB Stuttgart",
        "Eintracht Frankfurt",
    ),
    "LIGUE_1": (
        "Olympique de Marseille",
        "Olympique Lyonnais",
        "AS Monaco FC",
        "AS Saint-Étienne",
        "Girondins de Bordeaux",
        "LOSC Lille",
        "Racing Club de Lens",
    ),
    "EREDIVISIE": (
        "PSV",
        "Feyenoord Rotterdam",
        "AZ",
        "FC Twente",
        "FC Utrecht",
        "Sparta Rotterdam",
        "Willem II Tilburg",
    ),
}

_ALIASES = {
    "real madrid": "real madrid cf",
    "barcelona": "fc barcelona",
    "atletico madrid": "club atletico de madrid",
    "atletico de madrid": "club atletico de madrid",
    "manchester united": "manchester united",
    "man united": "manchester united",
    "manchester city": "manchester city",
    "man city": "manchester city",
    "inter": "internazionale milano",
    "inter milan": "internazionale milano",
    "internazionale": "internazionale milano",
    "ac milan": "ac milan",
    "bayern munich": "bayern munchen",
    "bayern munchen": "bayern munchen",
    "borussia monchengladbach": "borussia monchengladbach",
    "psg": "paris saint germain",
    "paris saint germain": "paris saint germain",
    "olympique lyon": "olympique lyonnais",
    "olympique marseille": "olympique de marseille",
    "ajax": "ajax",
    "psv eindhoven": "psv",
    "feyenoord": "feyenoord rotterdam",
    "athletic bilbao": "athletic club",
    "real sociedad": "real sociedad de futbol",
    "manchester city fc": "manchester city",
    "manchester united fc": "manchester united",
    "bayern": "bayern munchen",
    "bayer munich": "bayern munchen",
    "man utd": "manchester united",
    "manchester utd": "manchester united",
    "az alkmaar": "az",
    "losc lille": "lille osc",
    "lille osc": "lille osc",
}

# A recent-title tier adds recency to the honours record without letting a
# league-table run replace actual titles. Values are deliberately coarse tiers,
# not claims about exact trophy counts.
_RECENT_TITLE_RATING: dict[str, int] = {
    "Real Madrid CF": 100,
    "Manchester City FC": 100,
    "Chelsea FC": 96,
    "Paris Saint-Germain FC": 96,
    "FC Bayern München": 94,
    "FC Internazionale Milano": 92,
    "Liverpool FC": 90,
    "FC Barcelona": 88,
    "PSV": 88,
    "Club Atlético de Madrid": 84,
    "SSC Napoli": 82,
    "Arsenal FC": 80,
    "FC Porto": 80,
    "SL Benfica": 80,
    "AFC Ajax": 76,
    "Juventus FC": 72,
    "Manchester United FC": 72,
    "Borussia Dortmund": 72,
    "Real Betis Balompié": 70,
    "Tottenham Hotspur FC": 68,
    "Club Brugge KV": 68,
}

_DOMESTIC_LEAGUE_PRIORITY = {
    "PL": 100,
    "LALIGA": 90,
    "SERIE_A": 80,
    "BUNDESLIGA": 70,
    "LIGUE_1": 60,
    "EREDIVISIE": 50,
}

_DERBY_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {}
for _competition, _pairs in _PAIRS.items():
    _DERBY_PAIRS[_competition] = tuple(
        (left, right) for left, right, kind, _label in _pairs if kind == "derby"
    )
_DERBY_PAIRS["PL"] = tuple(
    (left, right) for left, right, priority, _label in _PL_PAIRS if priority in (0, 1)
)

# Domestic derbies remain meaningful in continental cups when the same clubs
# meet there. Classics are not promoted to derby status; club prominence
# already accounts for those fixtures below.
_CROSS_COMPETITION_DERBIES = tuple(pair for pairs in _DERBY_PAIRS.values() for pair in pairs)


def _normalized_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    plain = re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()
    plain = re.sub(r"\b(?:fc|cf|afc|sc|ac|cfc|ud|sv|fk|club)\b", " ", plain)
    plain = " ".join(plain.split())
    return _ALIASES.get(plain, plain)


normalize_team_name = _normalized_name

_ELITE_CLUBS = frozenset(
    _normalized_name(name)
    for name in (
        "FC Barcelona",
        "Real Madrid CF",
        "Paris Saint-Germain FC",
        "Arsenal FC",
        "Liverpool FC",
        "FC Bayern München",
        "Manchester City FC",
        "FC Internazionale Milano",
        "AC Milan",
    )
)
_REAL_MADRID_BARCELONA = frozenset(
    (_normalized_name("Real Madrid CF"), _normalized_name("FC Barcelona"))
)


def _elite_match_tier(home_team: str, away_team: str) -> int:
    """Apply the user's global club exception before every normal ranking rule."""
    home = _normalized_name(home_team)
    away = _normalized_name(away_team)
    matchup = frozenset((home, away))
    if matchup == _REAL_MADRID_BARCELONA:
        return 0
    elite_count = int(home in _ELITE_CLUBS) + int(away in _ELITE_CLUBS)
    if elite_count == 2:
        return 1
    # A single listed club does not override the normal competition, derby,
    # league, and title ranking. The exception protects matchups between two
    # listed clubs, such as PSG–Barcelona, plus the Clásico above.
    return 2


def _historical_trophy_rating(competition_code: str, team_name: str) -> int:
    name = _normalized_name(team_name)
    global_tier = {_normalized_name(club): 100 for club in _TROPHY_TIERS["global"]}
    global_major = {_normalized_name(club): 88 for club in _TROPHY_TIERS["global_major"]}
    local_competitions = (
        FEATURED_COMPETITION_PRIORITY[2:]
        if competition_code in {"UCL", "UEL"}
        else (competition_code,)
    )
    local_tier = {
        _normalized_name(club): 76 if index < 3 else 64
        for code in local_competitions
        for index, club in enumerate(_TROPHY_TIERS.get(code, ()))
    }
    return max(global_tier.get(name, 0), global_major.get(name, 0), local_tier.get(name, 0), 38)


def _club_trophy_rating(
    competition_code: str,
    team_name: str,
) -> float:
    historical = _historical_trophy_rating(competition_code, team_name)
    name = _normalized_name(team_name)
    recent = next(
        (rating for club, rating in _RECENT_TITLE_RATING.items() if _normalized_name(club) == name),
        45,
    )
    return historical * 0.7 + recent * 0.3


def _domestic_league_priority(
    competition_code: str,
    team_name: str,
    current_positions: dict[str, dict[str, tuple[int, int]]],
) -> int:
    """Return league identity priority, using table membership only, never rank."""
    name = _normalized_name(team_name)
    if competition_code not in {"UCL", "UEL"}:
        return _DOMESTIC_LEAGUE_PRIORITY.get(competition_code, 0)
    return max(
        (
            _DOMESTIC_LEAGUE_PRIORITY[code]
            for code in _DOMESTIC_LEAGUE_PRIORITY
            if name in current_positions.get(code, {})
        ),
        default=0,
    )


def _european_match_league_tier(
    competition_code: str,
    home_team: str,
    away_team: str,
    current_positions: dict[str, dict[str, tuple[int, int]]],
) -> int:
    """Promote top-league European matchups, without consulting table places."""
    if competition_code not in {"UCL", "UEL"}:
        return 0
    leagues = sorted(
        (
            _domestic_league_priority(competition_code, home_team, current_positions),
            _domestic_league_priority(competition_code, away_team, current_positions),
        ),
        reverse=True,
    )
    premier, second = leagues
    if premier == 100 and second == 100:  # Premier League vs Premier League
        return 0
    if premier == 100 and second == 90:  # Premier League vs LaLiga
        return 1
    if premier == 100:
        return 2
    return 3


def is_derby(competition_code: str, home_team: str, away_team: str) -> bool:
    pairs = _DERBY_PAIRS.get(competition_code, ())
    if competition_code in {"UCL", "UEL"}:
        pairs = _CROSS_COMPETITION_DERBIES
    matchup = frozenset((_normalized_name(home_team), _normalized_name(away_team)))
    return any(
        matchup == frozenset((_normalized_name(left), _normalized_name(right)))
        for left, right in pairs
    )


def featured_match_key(
    match,
    current_positions: dict[str, dict[str, tuple[int, int]]],
    policy: dict | None = None,
):
    """Rank special fixtures first, then competition tier and club stature."""
    policy = policy or DEFAULT_FEATURED_MATCH_POLICY
    elite_tier = _elite_match_tier(match.home_team, match.away_team)
    derby_bonus = (
        float(policy.get("derby_bonus", DEFAULT_FEATURED_MATCH_POLICY["derby_bonus"]))
        if is_derby(match.competition_code, match.home_team, match.away_team)
        else 0
    )
    matchup = frozenset(
        (_normalized_name(match.home_team), _normalized_name(match.away_team))
    )
    for override in policy.get("derby_bonus_overrides", ()):
        if override.get("competition_code") != match.competition_code:
            continue
        if frozenset(_normalized_name(name) for name in override.get("teams", ())) == matchup:
            derby_bonus = float(override.get("bonus", derby_bonus))
            break
    league_match_tier = _european_match_league_tier(
        match.competition_code, match.home_team, match.away_team, current_positions
    )
    home_rating = _club_trophy_rating(match.competition_code, match.home_team)
    away_rating = _club_trophy_rating(match.competition_code, match.away_team)
    # Give the stature of both clubs a voice, so one famous team alone does
    # not dominate a fixture against a much smaller opponent. A derby boosts
    # the matchup, but a smaller rivalry cannot automatically outrank a much
    # more important fixture such as Real Madrid–Sevilla.
    match_rating = (home_rating + away_rating) / 2 + derby_bonus
    if match.competition_code == "UCL":
        competition_tier = 0
    elif match.competition_code == "UEL":
        competition_tier = 1
    elif match.competition_code in _DOMESTIC_LEAGUE_PRIORITY:
        competition_tier = 2
    else:
        competition_tier = 3
    # When two domestic matches compete for the home spotlight, club stature
    # takes precedence over the league's default order. The league order breaks
    # close ties; continental competitions retain their explicit top priority.
    domestic_league_tie_break = (
        -_DOMESTIC_LEAGUE_PRIORITY.get(match.competition_code, 0)
        if competition_tier == 2
        else 0
    )
    live_statuses = {"LIVE", "PAUSED"}
    return (
        elite_tier,
        competition_tier,
        league_match_tier,
        -match_rating,
        domestic_league_tie_break,
        0 if match.status in live_statuses else 1,
        match.kickoff_utc,
    )
