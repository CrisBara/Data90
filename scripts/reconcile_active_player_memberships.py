"""Deactivate verified stale club memberships for the active season.

The script does not delete players, statistics, historical memberships, or
source links. It only marks stale active-season roster memberships inactive.
Run without arguments for a dry run; ``--apply`` creates a compressed backup.
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from data90.config.settings import get_settings
from data90.database.models import (
    CompetitionSeason,
    Player,
    PlayerTeamSeason,
    Season,
    Team,
)
from data90.database.session import make_session_factory


# Player ID: expected name, verified current club, first-party evidence.
VERIFIED_CLUBS = {
    3669: ("Malick Fofana", "Sunderland AFC", "https://finance.ol.fr/en/recent-announcements/"),
    3683: ("Romain Del Castillo", "CA Osasuna", "https://www.osasuna.es/en/osasuna-signs-forward-romain-del-castillo"),
    3887: ("Nick Woltemade", "Juventus FC", "https://www.juventus.com/en/news/articles/nick-woltemade-joins-01-09-26"),
    3995: ("Thilo Kehrer", "AFC Ajax", "https://english.ajax.nl/articles/ajax-and-as-monaco-reach-agreement-on-thilo-kehrer"),
    4337: ("Mikkel Bro Hansen", "PSV", "https://www.psv.nl/en/media/artikel/transfer-psv-verwelkomt-mikkel-bro-hansen"),
    4120: ("Miguel Rodríguez", "Deportivo Alavés", "https://fcutrecht.nl/nieuws/2026/juli/miguel-rodriguez-vertrekt-naar-deportivo-alaves/"),
    4332: ("Essien Bassey", "Viking FK", "https://www.psv.nl/media/artikel/transfer-bassey-overgenomen-door-viking-fk"),
    4396: ("Isaac Babadi", "Sparta Rotterdam", "https://www.psv.nl/en/media/artikel/alles-over-psv-ontvangt-sparta-rotterdam-in-het-philips-stadion"),
    4521: ("Maxim Dekker", "Willem II Tilburg", "https://www.willem-ii.nl/artikel/willem-ii-legt-maxim-dekker-vast-tot-2029"),
    5120: ("Joey Veerman", "Borussia Dortmund", "https://www.bvb.de/de/de/aktuelles/news/news.html/2026/8/18/Joey-Veerman-wechselt-zum-BVB.html"),
    5141: ("Ko Itakura", "Borussia Mönchengladbach", "https://www.bundesliga.com/en/bundesliga/news/borussia-monchengladbach-sign-japanese-defender-ko-itakura-ajax-38696"),
    5746: ("In-beom Hwang", "FC Porto", "https://www.feyenoord.com/en/news/inbeom-hwang-leaves-feyenoord-for-fc-porto-200726"),
    5805: ("Sam Lammers", "PSV", "https://www.psv.nl/media/artikel/transfer-psv-haalt-sam-lammers-terug-naar-eindhoven"),
    5881: ("Jordan Lotomba", "Celtic Glasgow", "https://www.feyenoord.com/en/news/jordan-lotomba-signs-with-celtic-fc-040926"),
    6007: ("Ilias Koutsoupias", "RSC Anderlecht", "https://www.frosinonecalcio.com/category/prima-squadra/?page_id_all=11"),
    6028: ("David Affengruber", "Fulham FC", "https://www.fulhamfc.com/news/2026/september/01/david-affengruber-joins-fulham/"),
    6163: ("Daniel Elfadli", "LASK Linz", "https://www.hsv.de/news/hsv-verleiht-daniel-elfadli-an-den-linzer-ask"),
    6402: ("Arnaud Bodart", "Le Havre AC", "https://hac.football/fr/posts/arnaud-bodart-debarque-au-hac"),
    6774: ("Mika Godts", "Paris Saint-Germain FC", "https://www.psg.fr/en/content/mika-godts-joins-paris-saint-germain-psg-club-news-2026-2027"),
    6845: ("Joel van den Berg", "Celtic Glasgow", "https://www.psv.nl/media/artikel/transfer-joel-van-den-berg-maakt-overstap-naar-celtic-fc"),
    6853: ("Sami Ouaissa", "PSV", "https://www.psv.nl/en/media/artikel/transfer-psv-presenteert-sami-ouaissa-als-nieuwe-speler"),
    6926: ("Neil El Aynaoui", "RB Leipzig", "https://rbleipzig.com/de/news/neil-el-aynaoui-transfer-rb-leipzig-as-rom-saison-2026-27"),
    6961: ("Couhaib Driouech", "RC Celta de Vigo", "https://www.psv.nl/media/artikel/transfer-driouech-maakt-transfer-naar-celta-de-vigo"),
    6998: ("Andrew Omobamidele", "RSC Anderlecht", "https://press.rsca.be/andrew-omobamidele-versterkt-de-verdediging-van-rsc-anderlecht"),
    10786: ("Andrew Abiola Omobamidele", "RSC Anderlecht", "https://press.rsca.be/andrew-omobamidele-renforce-la-defense-du-rsc-anderlecht"),
    10805: ("Malick Martin Fofana", "Sunderland AFC", "https://finance.ol.fr/communiques"),
    7243: ("Luuk Brouwers", "Omonia Nikosia", "https://www.sc-heerenveen.nl/nieuws/luuk-brouwers-vertrekt-naar-omonia-nicosia"),
    7281: ("Rayan Philippe", "SC Paderborn 07", "https://www.hsv.de/en/news/rayan-philippe-joins-sc-paderborn-on-loan"),
    7343: ("Ayoni Santos", "PSV", "https://www.psv.nl/media/artikel/transfer-ayoni-santos-van-sparta-naar-psv"),
    7386: ("Josip Šutalo", "SS Lazio", "https://english.ajax.nl/articles/josip-sutalo-joins-ss-lazio-on-loan/"),
    7401: ("Albert Guðmundsson", "SS Lazio", "https://www.sslazio.it/en/news/comunicati/albert-gudmundsson-e-un-nuovo-calciatore-biancoceleste"),
    7462: ("Alidu Seidu", "OGC Nice", "https://www.ogcnice.com/en/article/130857/alidu-seidu-joins-ogc-nice-on-loan.html"),
    7477: ("Boulaye Dia", "Stade Rennais FC 1901", "https://prod.sslazio.it/en/news/comunicati/boulaye-dia-ceduto-allo-stade-rennais"),
    7534: ("Jason Ceka", "SC Cambuur-Leeuwarden", "https://cambuur.nl/nieuws/sc-cambuur-huurt-vleugelaanvaller-jason-ceka-van-bundesliga-club/"),
    7558: ("Youri Regeer", "SV Werder Bremen", "https://www.werder.de/en/news/first-team/2026-2027/regeer-loan-31082026"),
    7609: ("Rolando Mandragora", "Torino FC", "https://www.torinofc.it/en/news/02/09/2026/mandragora-joins-torino-fc_44673"),
    7613: ("Kian Fitz-Jim", "Torino FC", "https://english.ajax.nl/articles/kian-fitz-jim-joins-torino-fc/"),
    7670: (
        "Yann Gboho",
        "Coventry City FC",
        "https://www.toulousefc.com/play/blog/2026/09/yann-gboho-quitte-le-toulouse-football-club-pour-coventry",
    ),
    7898: ("Leo Sauer", "VfB Stuttgart", "https://www.vfb.de/en/vfb/latest/news/professionals/2627/vfb-verpflichtet-leo-sauer-/"),
    8283: ("Conrad Harder Weibel Schandorf", "RC Strasbourg Alsace", "https://www.bundesliga.com/de/bundesliga/news/conrad-harder-rb-leipzig-wechsel-leihe-strassburg-38947"),
    8469: ("Nökkvi Þeyr Þórisson", "Telstar 1963", "https://www.sparta-rotterdam.nl/nokkvi-thorisson-maakt-seizoen-op-huurbasis-af-bij-telstar-2/"),
    8673: ("Troy Parrott", "Real Betis Balompié", "https://www.az.nl/inside-az/nieuws/2026/augustus/parrott-maakt-transfer-naar-real-betis"),
    9510: ("Sergio Martínez Montero", "Real Madrid CF", "https://www.realmadrid.com/es-ES/futbol/cantera-masculina/castilla/sergio-martinez-montero"),
    5725: ("Isak Jensen", "FC Lorient", "https://www.az.nl/inside-az/nieuws/2026/augustus/jensen-op-huurbasis-naar-fc-lorient"),
    7021: ("Joaquín Martínez Gauna", "RC Strasbourg Alsace", "https://prod.sevillafc.es/en/actualidad/noticias/agreement-oso-transfer-rc-strasbourg"),
    8849: ("Casper Tengstedt", "Toulouse FC", "https://www.feyenoord.com/en/news/tengstedt-completes-transfer-to-toulouse-fc-310826"),
    8851: ("Ayase Ueda", "Lille OSC", "https://www.feyenoord.com/en/news/ayase-ueda-leaves-feyenoord-for-losc-lille-310826"),
    9257: ("Moise Bioty Kean", "Como 1907", "https://www.legaseriea.it/serie-a/news/moise-kean-e-un-nuovo-giocatore-del-como"),
    9267: ("Alieu Eybi Njie", "ACF Fiorentina", "https://be.torinofc.it/news/30/08/2026/njie-alla-fiorentina_44658"),
    9270: ("Norberto Bercique Gomes Betuncal", "ACF Fiorentina", "https://www.torinofc.it/news/05/09/2026/i-convocati-la-fiorentina_44694"),
    9765: ("Luka Ivanusec", "Dinamo Zagreb", "https://gnkdinamo.hr/en/news/luka-ivanusec-ponovno-u-dinamu"),
    10422: ("Daniel Bassi", "Lillestrøm SK", "https://www.glimt.no/nyheter/daniel-bassi-forlater-glimt-vanskelig-a-si-farvel"),
    10432: ("Marcus Holmgren Pedersen", "Olympiakos Piräus", "https://www.olympiacos.org/en/2026/08/29/marcus-holmgren-pedersen-joins-olympiacos/"),
    4409: ("Nigel Ogidi Nwankwo", "Telstar 1963", "https://fcutrecht.nl/nieuws/2025/juli/neville-ogidi-nwankwo-tekent-bij-fc-utrecht-op-huurbasis-naar-telstar-2/"),
}


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def active_conflicts(session: Any, active_season_ids: set[int]) -> dict[tuple[int, int], dict[int, str]]:
    rows = session.execute(
        select(PlayerTeamSeason.player_id, CompetitionSeason.season_id, Team.id, Team.canonical_name)
        .join(CompetitionSeason, CompetitionSeason.id == PlayerTeamSeason.competition_season_id)
        .join(Team, Team.id == PlayerTeamSeason.team_id)
        .where(
            PlayerTeamSeason.active.is_(True),
            CompetitionSeason.season_id.in_(active_season_ids),
        )
    ).all()
    by_identity: dict[tuple[int, int], dict[int, str]] = {}
    for player_id, season_id, team_id, name in rows:
        by_identity.setdefault((player_id, season_id), {})[team_id] = name
    return {key: clubs for key, clubs in by_identity.items() if len(clubs) > 1}


def run(apply: bool) -> None:
    factory = make_session_factory()
    with factory() as session, session.begin():
        active_seasons = session.scalars(select(Season).where(Season.active.is_(True))).all()
        season_ids = {season.id for season in active_seasons}
        if not season_ids:
            raise RuntimeError("No hay temporadas activas configuradas.")

        conflicts = active_conflicts(session, season_ids)
        if not conflicts:
            print("Auditoría de planteles activos: no hay jugadores asignados a más de un club.")
            if apply:
                print("No hay cambios para aplicar.")
            else:
                print("Simulación solamente; no se guardó ningún cambio.")
            return

        actions: list[tuple[PlayerTeamSeason, int, str]] = []
        for (player_id, season_id), clubs in sorted(conflicts.items()):
            verified = VERIFIED_CLUBS.get(player_id)
            player = session.get(Player, player_id)
            if verified is None:
                raise RuntimeError(
                    f"Conflicto sin revisión oficial: jugador ID {player_id} ({player.display_name if player else 'desconocido'}), "
                    f"temporada {season_id}, clubes={sorted(clubs.values())}."
                )
            expected_name, target_name, evidence = verified
            if player is None or player.display_name != expected_name:
                raise RuntimeError(f"Cambió la identidad del jugador ID {player_id}; revisar antes de continuar.")
            targets = session.scalars(select(Team).where(Team.canonical_name == target_name)).all()
            if len(targets) != 1:
                raise RuntimeError(f"Club esperado no es único o no existe: {target_name}.")
            target = targets[0]
            if target.id not in clubs:
                raise RuntimeError(
                    f"La fuente oficial indica {target_name}, pero no hay membresía activa del jugador "
                    f"{expected_name} en ese club para la temporada activa."
                )
            stale_names = [name for team_id, name in clubs.items() if team_id != target.id]
            membership_rows = session.scalars(
                select(PlayerTeamSeason)
                .join(CompetitionSeason, CompetitionSeason.id == PlayerTeamSeason.competition_season_id)
                .where(
                    PlayerTeamSeason.player_id == player_id,
                    PlayerTeamSeason.active.is_(True),
                    CompetitionSeason.season_id == season_id,
                    PlayerTeamSeason.team_id != target.id,
                )
            ).all()
            for membership in membership_rows:
                actions.append((membership, target.id, evidence))
            print(
                f"{expected_name}: conservar {target_name}; desactivar {', '.join(sorted(stale_names))}; "
                f"temporada_id={season_id}; respaldo/fuente={evidence}"
            )

        if not actions:
            raise RuntimeError("Se encontraron conflictos, pero no hay filas elegibles para corregir.")
        if not apply:
            print(f"Filas de plantel que se desactivarían: {len(actions)}")
            print("Simulación solamente; no se guardó ningún cambio.")
            return

        backup_dir = Path(__file__).resolve().parents[1] / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"player-membership-reconcile-{stamp}.json.gz"
        payload = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "database": get_settings().database_name,
            "active_seasons": [{"id": season.id, "label": season.label} for season in active_seasons],
            "evidence": {
                str(player_id): {
                    "name": details[0],
                    "verified_club": details[1],
                    "source": details[2],
                }
                for player_id, details in VERIFIED_CLUBS.items()
            },
            "player_team_seasons": [_row_dict(row) for row, _, _ in actions],
        }
        with gzip.open(backup_path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, default=_json_default)

        for row, _, _ in actions:
            row.active = False
        session.flush()
        remaining = active_conflicts(session, season_ids)
        if remaining:
            sample = sorted((player_id, season_id, clubs) for (player_id, season_id), clubs in remaining.items())[:10]
            raise RuntimeError(f"Quedaron conflictos activos después de corregir: {sample}")
        print(f"Respaldo creado: {backup_path}")
        print(f"Filas de plantel marcadas inactivas: {len(actions)}")
        print("Validación final: cero jugadores activos en más de un club por temporada.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="aplica la reconciliación con respaldo")
    args = parser.parse_args()
    run(args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
