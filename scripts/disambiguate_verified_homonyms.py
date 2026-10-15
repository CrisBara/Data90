"""Give verified footballer homonyms distinct full names in the local DB."""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from data90.database.models import Player
from data90.database.session import make_session_factory


VERIFIED_NAMES = {
    5941: ("Pedrinho", "2002-07-11", "Pedro Henrique Azevedo Pereira"),
    5953: ("Pedrinho", "1998-04-13", "Pedro Victor Delmino da Silva"),
    3170: ("Alemão", "1998-04-01", "Alexandre Zurawski"),
    6281: ("Alemao", "2002-11-06", "João Tornich"),
    4977: ("Éderson", "1989-03-13", "Éderson José dos Santos Lourenço da Silva"),
    5844: ("Ederson", "1993-08-17", "Ederson Santana de Moraes"),
    9433: ("João Costa", "1996-02-02", "João Paulo Santos da Costa"),
    10174: ("João Costa", "1992-08-25", "João José Pereira da Costa"),
    3781: ("Otávio", "2002-04-21", "Otávio Ataíde da Silva"),
    5483: ("Otávio", "2005-11-25", "Otávio Manoel Galdino Fernandes"),
    5246: ("Raúl", "2000-11-03", "Raúl García de Haro"),
    7087: ("Raúl", "2003-02-13", "Raúl Asencio del Rosario"),
    7345: ("Rui Silva", "1994-02-07", "Rui Tiago Dantas da Silva"),
    9660: ("Rui Silva", "2007-03-18", "Rui Pedro Ferreira da Silva"),
    4337: ("Mikkel Hansen", "2009-01-25", "Mikkel Bro Hansen"),
    7021: ("Oso", "2003-07-09", "Joaquín Martínez Gauna"),
    9510: ("Sergio Martínez", "2007-05-15", "Sergio Martínez Montero"),
}
VERIFIED_DOB_CORRECTIONS = {
    # Atalanta's official profile gives 07/07/1999; the provider row had 1989.
    4977: ("1989-03-13", "1999-07-07"),
}


def run(apply: bool) -> None:
    factory = make_session_factory()
    with factory() as session, session.begin():
        players = {
            player.id: player
            for player in session.scalars(select(Player).where(Player.id.in_(VERIFIED_NAMES)))
        }
        changes = []
        for player_id, (old_name, birth_date, full_name) in VERIFIED_NAMES.items():
            player = players.get(player_id)
            if player is None or player.display_name not in {old_name, full_name}:
                raise RuntimeError(f"Cambió el registro esperado para el jugador ID {player_id}.")
            if player.birth_date is None or player.birth_date.isoformat() != birth_date:
                raise RuntimeError(f"La fecha de nacimiento del jugador ID {player_id} no coincide.")
            print(f"ID {player_id}: {player.display_name} ({birth_date}) -> {full_name}")
            if player.display_name != full_name:
                changes.append((player, player.display_name, full_name))

        dob_corrections = []
        for player_id, (old_date, verified_date) in VERIFIED_DOB_CORRECTIONS.items():
            player = players.get(player_id) or session.get(Player, player_id)
            if player is None or player.birth_date is None or player.birth_date.isoformat() != old_date:
                raise RuntimeError(f"La fecha del registro esperado para el jugador ID {player_id} cambió.")
            print(f"ID {player_id}: fecha {old_date} -> {verified_date} (ficha oficial Atalanta)")
            dob_corrections.append((player, old_date, verified_date))

        if not apply or not changes and not dob_corrections:
            print("Simulación solamente; no se guardó ningún cambio." if not apply else "No hay cambios pendientes.")
            return

        backup_dir = Path(__file__).resolve().parents[1] / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"player-name-disambiguation-{stamp}.json.gz"
        payload = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "players": [
                {"id": player.id, "display_name": old_name, "birth_date": player.birth_date.isoformat()}
                for player, old_name, _ in changes
            ],
            "verified_birth_date_corrections": [
                {"id": player.id, "display_name": player.display_name, "birth_date": old_date}
                for player, old_date, _ in dob_corrections
            ],
        }
        with gzip.open(backup_path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        for player, _, full_name in changes:
            player.display_name = full_name
        for player, _, verified_date in dob_corrections:
            from datetime import date

            player.birth_date = date.fromisoformat(verified_date)
        session.flush()
        print(f"Respaldo creado: {backup_path}")
        print(f"Nombres completos actualizados: {len(changes)}")
        print(f"Fechas oficiales corregidas: {len(dob_corrections)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="aplica los nombres completos verificados")
    args = parser.parse_args()
    run(args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
