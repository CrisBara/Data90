"""Read-only smoke check for a Highlightly player profile and season statistics."""

import argparse
import json
import sys
from datetime import date

from data90.domain.exceptions import ProviderError
from data90.providers.highlightly import HighlightlyProvider


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", default="Lautaro Martínez", help="Player name to look up")
    today = date.today()
    start_year = today.year if today.month >= 7 else today.year - 1
    current_season = f"{start_year % 100:02d}/{(start_year + 1) % 100:02d}"
    parser.add_argument(
        "--season", default=current_season, help="Season label to show, e.g. 26/27"
    )
    args = parser.parse_args()

    provider = HighlightlyProvider()
    try:
        matches = provider.search_players(args.player, limit=10).get("data", [])
        if not matches:
            print(f"No se encontraron coincidencias para {args.player!r}.")
            return 1

        print("Coincidencias:")
        for item in matches:
            print(f"  {item.get('id')}: {item.get('fullName') or item.get('name')}")

        exact = [
            item
            for item in matches
            if str(item.get("name", "")).casefold() == args.player.casefold()
            or str(item.get("fullName", "")).casefold() == args.player.casefold()
        ]
        selected = exact[0] if len(exact) == 1 else matches[0]
        player_id = selected.get("id")
        if not isinstance(player_id, int):
            raise ProviderError("Highlightly no devolvió un identificador válido para el jugador")

        profile = provider.get_player_profile(player_id)
        statistics = provider.get_player_statistics(player_id)
        print("\nPerfil (resumen de campos comprobables):")
        print(
            json.dumps(
                {
                    "id": profile.get("id", player_id),
                    "name": profile.get("fullName") or profile.get("name"),
                    "profile": profile.get("profile"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        season_rows = [
            {key: row.get(key) for key in (
                "league", "season", "club", "gamesPlayed", "minutesPlayed", "goals",
                "assists", "yellowCards", "redCards", "substitutedIn", "cleanSheets",
                "goalsConceded",
            )}
            for item in statistics
            for row in item.get("perCompetition", [])
            if str(row.get("season", "")).casefold() == args.season.casefold()
        ]
        print(f"\nEstadísticas encontradas para {args.season}:")
        print(json.dumps(season_rows, ensure_ascii=False, indent=2))
        print(f"\nSolicitudes consumidas: {provider.request_count} de la cuota diaria.")
        return 0
    except ProviderError as error:
        print(f"No se pudo validar Highlightly: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
