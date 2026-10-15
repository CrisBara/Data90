"""Source-audited rivalries between clubs in the current Premier League set.

Pairs are deliberately curated rather than inferred from geography alone. Each
entry is undirected and carries a display priority and a Spanish label.
"""

from itertools import combinations
from typing import NamedTuple


class PremierLeagueRivalry(NamedTuple):
    priority: int
    category: str
    rival_name: str


_NORTH_LONDON = ("Arsenal FC", "Tottenham Hotspur FC")
_SPECIAL_DERBIES = (
    ("Manchester City FC", "Manchester United FC", 0, "Derbi de Manchester"),
    ("Everton FC", "Liverpool FC", 0, "Derbi de Merseyside"),
    ("Newcastle United FC", "Sunderland AFC", 0, "Derbi Tyne-Wear"),
    ("Brighton & Hove Albion FC", "Crystal Palace FC", 0, "Derbi M23"),
    (*_NORTH_LONDON, 0, "Derbi del Norte de Londres"),
)

# These six clubs are in the current 2026/27 PL participant set. The PL's
# published 2025/26 derby fixture list covers every pairing below; its 2026/27
# fixture commentary separately confirms Chelsea–Fulham as a local derby.
_LONDON_CLUBS = (
    "Arsenal FC",
    "Brentford FC",
    "Chelsea FC",
    "Crystal Palace FC",
    "Fulham FC",
    "Tottenham Hotspur FC",
)
_SPECIAL_PAIRS = {frozenset((left, right)) for left, right, *_ in _SPECIAL_DERBIES}
_LONDON_DERBIES = tuple(
    (left, right, 1, "Derbis londinenses")
    for left, right in combinations(_LONDON_CLUBS, 2)
    if frozenset((left, right)) not in _SPECIAL_PAIRS
)

# The PL editorial archive explicitly describes these fixtures as historic,
# classic, or rivalries. They rank after geographic derbies.
_CLASSICS = (
    ("Arsenal FC", "Manchester United FC", 2, "Clásicos históricos"),
    ("Arsenal FC", "Liverpool FC", 2, "Clásicos históricos"),
    ("Arsenal FC", "Manchester City FC", 2, "Clásicos históricos"),
    ("Chelsea FC", "Liverpool FC", 2, "Clásicos históricos"),
    ("Liverpool FC", "Manchester United FC", 2, "Clásicos históricos"),
    ("Liverpool FC", "Manchester City FC", 2, "Clásicos históricos"),
    ("Liverpool FC", "Newcastle United FC", 2, "Clásicos históricos"),
    ("Newcastle United FC", "Manchester United FC", 2, "Clásicos históricos"),
    ("Chelsea FC", "Leeds United FC", 2, "Clásicos históricos"),
)

# Keep one primary derby per club in the visible team profile. These London
# pairings are the most locally significant options for clubs without a named
# derby above; the full source-audited London candidate set remains available
# in _LONDON_DERBIES for reference.
_PRIMARY_LONDON_DERBIES = (
    ("Brentford FC", "Fulham FC"),
    ("Chelsea FC", "Fulham FC"),
)
_PAIRINGS = (*_SPECIAL_DERBIES, *_LONDON_DERBIES, *_CLASSICS)

# Per-club order for the two historic classics shown after its primary derby.
# These rankings intentionally keep each club's most established matchups.
_CLASSIC_OPPONENT_ORDER = {
    "Arsenal FC": ("Manchester United FC", "Liverpool FC", "Manchester City FC"),
    "Chelsea FC": ("Liverpool FC", "Leeds United FC"),
    "Liverpool FC": (
        "Manchester United FC",
        "Manchester City FC",
        "Arsenal FC",
        "Newcastle United FC",
        "Chelsea FC",
    ),
    "Manchester United FC": (
        "Liverpool FC",
        "Arsenal FC",
        "Newcastle United FC",
    ),
    "Newcastle United FC": ("Manchester United FC", "Liverpool FC"),
    "Leeds United FC": ("Chelsea FC",),
}


def rivalries_for(team_name: str) -> tuple[PremierLeagueRivalry, ...]:
    """Return at most one primary derby and two priority-ranked classics."""
    matches: list[PremierLeagueRivalry] = []
    for left, right, priority, label in _PAIRINGS:
        if team_name == left:
            matches.append(PremierLeagueRivalry(priority, label, right))
        elif team_name == right:
            matches.append(PremierLeagueRivalry(priority, label, left))

    named_derbies = [rivalry for rivalry in matches if rivalry.priority == 0]
    if named_derbies:
        primary_derby = named_derbies[0]
    else:
        preferred_opponent = next(
            (
                rival
                for left, right in _PRIMARY_LONDON_DERBIES
                for team, rival in ((left, right), (right, left))
                if team == team_name
            ),
            None,
        )
        primary_derby = next(
            (
                rivalry
                for rivalry in matches
                if rivalry.priority == 1 and rivalry.rival_name == preferred_opponent
            ),
            None,
        )

    classic_order = _CLASSIC_OPPONENT_ORDER.get(team_name, ())
    classics = sorted(
        (rivalry for rivalry in matches if rivalry.priority == 2),
        key=lambda rivalry: (
            classic_order.index(rivalry.rival_name)
            if rivalry.rival_name in classic_order
            else len(classic_order)
        ),
    )[:2]
    return tuple(([primary_derby] if primary_derby else []) + classics)
