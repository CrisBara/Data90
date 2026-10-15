"""Curated, source-checked rivalry pairs for supported domestic leagues.

Each pair is undirected. A team may show one derby and up to two classic
fixtures; the UI history is assembled from real finished league matches.
"""

from data90.config.premier_league_rivalries import PremierLeagueRivalry

# (club 1, club 2, kind, display label). Keep entries specific and avoid
# inferring a rivalry only from geographic proximity.
_PAIRS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "LALIGA": (
        ("Club Atlético de Madrid", "Real Madrid CF", "derby", "Derbi madrileño"),
        ("FC Barcelona", "RCD Espanyol de Barcelona", "derby", "Derbi barcelonés"),
        ("Real Betis Balompié", "Sevilla FC", "derby", "Gran Derbi"),
        ("Athletic Club", "Real Sociedad de Fútbol", "derby", "Derbi vasco"),
        ("RC Celta de Vigo", "RC Deportivo La Coruña", "derby", "Derbi gallego"),
        ("Valencia CF", "Levante UD", "derby", "Derbi valenciano"),
        ("FC Barcelona", "Real Madrid CF", "classic", "El Clásico"),
        ("Club Atlético de Madrid", "FC Barcelona", "classic", "Clásico nacional"),
        ("Club Atlético de Madrid", "Sevilla FC", "classic", "Rivalidad histórica"),
        ("Athletic Club", "FC Barcelona", "classic", "Clásico histórico"),
        ("Real Madrid CF", "Valencia CF", "classic", "Clásico histórico"),
        ("Valencia CF", "Villarreal CF", "classic", "Derbi de la Comunitat"),
    ),
    "BUNDESLIGA": (
        ("Borussia Dortmund", "FC Schalke 04", "derby", "Revierderby"),
        ("1. FC Köln", "Borussia Mönchengladbach", "derby", "Derbi del Rin"),
        ("Hamburger SV", "SV Werder Bremen", "derby", "Nordderby"),
        ("FC Bayern München", "Borussia Dortmund", "classic", "Der Klassiker"),
        ("FC Bayern München", "Borussia Mönchengladbach", "classic", "Klassiker histórico"),
    ),
    "EREDIVISIE": (
        ("Feyenoord Rotterdam", "Sparta Rotterdam", "derby", "Derbi de Róterdam"),
        ("FC Groningen", "SC Heerenveen", "derby", "Derbi del Norte"),
        ("AFC Ajax", "Feyenoord Rotterdam", "classic", "De Klassieker"),
        ("AFC Ajax", "PSV", "classic", "Topper neerlandés"),
        ("Feyenoord Rotterdam", "PSV", "classic", "Clásico neerlandés"),
    ),
    "LIGUE_1": (
        ("AS Monaco FC", "OGC Nice", "derby", "Derbi de la Costa Azul"),
        ("Lille OSC", "Racing Club de Lens", "derby", "Derbi del Norte"),
        ("Paris Saint-Germain FC", "Paris FC", "derby", "Derbi parisino"),
        ("Paris Saint-Germain FC", "Olympique de Marseille", "classic", "Le Classique"),
        ("Paris Saint-Germain FC", "Olympique Lyonnais", "classic", "Clásico francés"),
        ("Olympique de Marseille", "Olympique Lyonnais", "classic", "Olympico"),
        ("AS Monaco FC", "Olympique de Marseille", "classic", "Clásico francés"),
    ),
    "SERIE_A": (
        ("AC Milan", "FC Internazionale Milano", "derby", "Derby della Madonnina"),
        ("AS Roma", "SS Lazio", "derby", "Derby della Capitale"),
        ("Juventus FC", "Torino FC", "derby", "Derby della Mole"),
        ("ACF Fiorentina", "Bologna FC 1909", "derby", "Derby dell’Appennino"),
        ("SSC Napoli", "AS Roma", "classic", "Derby del Sole"),
        ("FC Internazionale Milano", "Juventus FC", "classic", "Derby d’Italia"),
        ("Juventus FC", "AC Milan", "classic", "Classico d’Italia"),
        ("SSC Napoli", "Juventus FC", "classic", "Classico del Sud"),
        ("SSC Napoli", "FC Internazionale Milano", "classic", "Clásico italiano"),
    ),
}

# Rank the two classics per club where there are more candidates. The listed
# opponent names are checked against the competition's actual participant set.
_CLASSIC_ORDER: dict[str, dict[str, tuple[str, ...]]] = {
    "LALIGA": {
        "FC Barcelona": ("Real Madrid CF", "Club Atlético de Madrid"),
        "Real Madrid CF": ("FC Barcelona", "Club Atlético de Madrid", "Valencia CF"),
        "Club Atlético de Madrid": ("FC Barcelona", "Sevilla FC"),
        "Athletic Club": ("FC Barcelona",),
        "Valencia CF": ("Villarreal CF", "Real Madrid CF"),
    },
    "BUNDESLIGA": {
        "FC Bayern München": ("Borussia Dortmund", "Borussia Mönchengladbach"),
        "Borussia Dortmund": ("FC Bayern München",),
        "Borussia Mönchengladbach": ("FC Bayern München",),
    },
    "EREDIVISIE": {
        "AFC Ajax": ("Feyenoord Rotterdam", "PSV"),
        "Feyenoord Rotterdam": ("AFC Ajax", "PSV"),
        "PSV": ("AFC Ajax", "Feyenoord Rotterdam"),
    },
    "LIGUE_1": {
        "Paris Saint-Germain FC": ("Olympique de Marseille", "Olympique Lyonnais"),
        "Olympique de Marseille": ("Paris Saint-Germain FC", "Olympique Lyonnais"),
        "Olympique Lyonnais": ("Paris Saint-Germain FC", "Olympique de Marseille"),
        "AS Monaco FC": ("Olympique de Marseille",),
    },
    "SERIE_A": {
        "FC Internazionale Milano": ("Juventus FC", "SSC Napoli"),
        "Juventus FC": ("FC Internazionale Milano", "AC Milan", "SSC Napoli"),
        "AC Milan": ("Juventus FC", "FC Internazionale Milano"),
        "SSC Napoli": ("Juventus FC", "FC Internazionale Milano", "AS Roma"),
        "AS Roma": ("SSC Napoli",),
    },
}


def rivalries_for_competition(
    competition_code: str, team_name: str
) -> tuple[PremierLeagueRivalry, ...]:
    """Return one curated derby and up to two priority-ranked classics."""
    if competition_code == "PL":
        from data90.config.premier_league_rivalries import rivalries_for

        return tuple(rivalries_for(team_name))

    pairs = _PAIRS.get(competition_code, ())
    matches: list[PremierLeagueRivalry] = []
    for left, right, kind, label in pairs:
        priority = 0 if kind == "derby" else 1
        if team_name == left:
            matches.append(PremierLeagueRivalry(priority, label, right))
        elif team_name == right:
            matches.append(PremierLeagueRivalry(priority, label, left))

    derby = next((item for item in matches if item.priority == 0), None)
    classic_order = _CLASSIC_ORDER.get(competition_code, {}).get(team_name, ())
    classics = sorted(
        (item for item in matches if item.priority == 1),
        key=lambda item: (
            classic_order.index(item.rival_name)
            if item.rival_name in classic_order
            else len(classic_order)
        ),
    )[:2]
    return tuple(([derby] if derby else []) + classics)
