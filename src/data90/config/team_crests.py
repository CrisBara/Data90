"""Verified club crest URL corrections for the web client."""

import unicodedata

VERIFIED_TEAM_CREST_OVERRIDES = {
    "athletic club": "https://crests.football-data.org/77.png",
    "athletic club de bilbao": "https://crests.football-data.org/77.png",
    "athletic club bilbao": "https://crests.football-data.org/77.png",
    "athletic bilbao": "https://crests.football-data.org/77.png",
    "athletic club de espana": "https://crests.football-data.org/77.png",
    "atletic club": "https://crests.football-data.org/77.png",
    "atletic club de espana": "https://crests.football-data.org/77.png",
    "ca osasuna": "https://crests.football-data.org/79.png",
    "osasuna": "https://crests.football-data.org/79.png",
    "elche cf": "https://crests.football-data.org/285.png",
    "fc barcelona": "https://crests.football-data.org/81.png",
    "barcelona": "https://crests.football-data.org/81.png",
    "rc celta de vigo": "https://crests.football-data.org/558.png",
    "celta vigo": "https://crests.football-data.org/558.png",
    "celta de vigo": "https://crests.football-data.org/558.png",
    "real sociedad": "https://crests.football-data.org/92.png",
    "real sociedad de futbol": "https://crests.football-data.org/92.png",
    "levante ud": "https://crests.football-data.org/88.png",
    "deportivo alaves": "https://crests.football-data.org/263.png",
    "cd alaves": "https://crests.football-data.org/263.png",
    "alaves": "https://crests.football-data.org/263.png",
    "bayer 04 leverkusen": "https://crests.football-data.org/3.png",
    "borussia monchengladbach": "https://crests.football-data.org/18.png",
    "eintracht frankfurt": "https://crests.football-data.org/19.png",
    "fc augsburg": "https://crests.football-data.org/16.png",
    "fc bayern munchen": "https://crests.football-data.org/5.png",
    "rb leipzig": "https://crests.football-data.org/721.png",
    "sc freiburg": "https://crests.football-data.org/17.png",
    "vfb stuttgart": "https://crests.football-data.org/10.png",
    "tsg hoffenheim": "https://crests.football-data.org/2.png",
    "tsg 1899 hoffenheim": "https://crests.football-data.org/2.png",
    "fc schalke 04": "https://crests.football-data.org/6.png",
    "schalke 04": "https://crests.football-data.org/6.png",
    "shalke 04": "https://crests.football-data.org/6.png",
    "az alkmaar": "https://media.api-sports.io/football/teams/201.png",
    "az alkmar": "https://media.api-sports.io/football/teams/201.png",
    "nk celje": "https://upload.wikimedia.org/wikipedia/commons/5/5d/NK_Celje_logo.png",
    "celje": "https://upload.wikimedia.org/wikipedia/commons/5/5d/NK_Celje_logo.png",
    "benfica lissabon": "https://upload.wikimedia.org/wikipedia/en/a/a2/SL_Benfica_logo.svg",
    "sparta prag": "https://upload.wikimedia.org/wikipedia/commons/d/dd/AC-Sparta-LOGO2021.svg",
    "newcastle united": "https://crests.football-data.org/67.png",
    "newcastle united fc": "https://crests.football-data.org/67.png",
    "tottenham hotspur": "https://crests.football-data.org/73.svg",
    "tottenham hotspur fc": "https://crests.football-data.org/73.svg",
    "chelsea": "https://crests.football-data.org/61.png",
    "chelsea fc": "https://crests.football-data.org/61.png",
    "crystal palace": "https://crests.football-data.org/354.png",
    "crystal palace fc": "https://crests.football-data.org/354.png",
}


def normalize_team_crest_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_marks.split())


def verified_team_crest_url(name: str | None, url: str | None) -> str | None:
    if not name:
        return url
    return VERIFIED_TEAM_CREST_OVERRIDES.get(normalize_team_crest_name(name), url)
