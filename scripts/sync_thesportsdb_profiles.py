"""Enrich missing biographies for player squads in all active competitions."""

from __future__ import annotations

import argparse

from data90.database.session import make_engine, make_session_factory
from data90.services.thesportsdb_profile_sync import sync_thesportsdb_profiles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=(
            "PL",
            "LALIGA",
            "SERIE_A",
            "BUNDESLIGA",
            "LIGUE_1",
            "EREDIVISIE",
            "UCL",
            "UEL",
        ),
        default=(
            "PL",
            "LALIGA",
            "SERIE_A",
            "BUNDESLIGA",
            "LIGUE_1",
            "EREDIVISIE",
            "UCL",
            "UEL",
        ),
    )
    args = parser.parse_args()
    engine = make_engine()
    try:
        summary = sync_thesportsdb_profiles(
            make_session_factory(engine), competition_codes=tuple(args.competitions)
        )
    finally:
        engine.dispose()
    print(f"SyncRun: {summary.run_id}")
    print(
        f"Consultados: {summary.players_checked}; coincidencias únicas: {summary.players_matched}"
    )
    print(
        "Campos completados: "
        f"nacionalidad={summary.nationality_filled}, posición={summary.position_filled}, "
        f"nacimiento={summary.birth_dates_filled}, altura={summary.height_filled}, "
        f"peso={summary.weight_filled}"
    )
    print(
        f"Solicitudes={summary.requests_count}; omitidos={summary.skipped_count}; "
        f"errores={summary.error_count}"
    )
    print(summary.message)
    return 1 if summary.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
