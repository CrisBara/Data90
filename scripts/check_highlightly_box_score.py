"""Read-only check that a finished fixture has player-level match statistics."""

import argparse
import json
import sys

from football_analytics.domain.exceptions import ProviderError
from football_analytics.providers.highlightly import HighlightlyProvider


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="Serie A")
    parser.add_argument("--date", default="2026-09-19", help="Fixture date YYYY-MM-DD")
    parser.add_argument("--team", default="Inter", help="Team name to select a fixture")
    parser.add_argument("--player", default="Lautaro")
    args = parser.parse_args()

    provider = HighlightlyProvider()
    try:
        fixtures = provider.get_matches(league_name=args.league, match_date=args.date).get(
            "data", []
        )
        finished = [
            row
            for row in fixtures
            if isinstance(row.get("state"), dict)
            and "finished" in str(row["state"].get("description", "")).casefold()
        ]
        print("Partidos encontrados:")
        for match in finished:
            state = match.get("state", {})
            print(
                f"  {match.get('id')}: {match.get('homeTeam', {}).get('name')} "
                f"{state.get('score', {}).get('current')} "
                f"{match.get('awayTeam', {}).get('name')}"
            )
        if not finished:
            print("No se encontró un partido finalizado para esos filtros.")
            return 1

        selected = next(
            (
                match
                for match in finished
                if args.team.casefold()
                in f"{match.get('homeTeam', {}).get('name')} "
                f"{match.get('awayTeam', {}).get('name')}".casefold()
            ),
            finished[0],
        )
        box_score = provider.get_box_score(int(selected["id"]))
        rows = []
        for team in box_score:
            for player in team.get("players", []):
                name = str(player.get("fullName") or player.get("name") or "")
                if args.player.casefold() not in name.casefold():
                    continue
                stats = player.get("statistics")
                if isinstance(stats, list):
                    stats = stats[0] if stats and isinstance(stats[0], dict) else {}
                stats = stats if isinstance(stats, dict) else {}
                rows.append(
                    {
                        "team": team.get("team", {}).get("name"),
                        "playerId": player.get("id"),
                        "name": name,
                        "position": player.get("position"),
                        "shirtNumber": player.get("shirtNumber"),
                        "isSubstitute": player.get("isSubstitute"),
                        "minutesPlayed": player.get("minutesPlayed"),
                        "statisticsFields": sorted(stats),
                        "goals": stats.get("goalsScored"),
                        "assists": stats.get("assists"),
                        "shotsTotal": stats.get("shotsTotal"),
                        "shotsOnTarget": stats.get("shotsOnTarget"),
                        "yellowCards": stats.get("cardsYellow"),
                        "redCards": stats.get("cardsRed"),
                    }
                )
        if not rows:
            raise ProviderError("No apareció el jugador indicado en el box score del partido")
        print("\nBox score del jugador:")
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        print(f"\nSolicitudes consumidas: {provider.request_count} de la cuota diaria.")
        return 0
    except (ProviderError, TypeError, ValueError, AttributeError) as error:
        print(f"No se pudo validar el box score: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
