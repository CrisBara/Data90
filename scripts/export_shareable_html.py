"""Export the current read-only web companion as one shareable HTML file."""

from __future__ import annotations

import base64
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data90.config.featured_match_priority import (  # noqa: E402
    FEATURED_COMPETITION_PRIORITY,
    featured_match_key,
    normalize_team_name,
)
from data90.config.settings import get_settings  # noqa: E402
from data90.database.models import Team  # noqa: E402
from data90.database.session import make_engine, make_session_factory  # noqa: E402
from data90.repositories.players import (  # noqa: E402
    player_profile_detail,
    player_roster_for_competition_season,
)
from data90.services.competition_service import CompetitionService  # noqa: E402
from data90.services.featured_match_policy import featured_match_policy  # noqa: E402
from data90.web import (  # noqa: E402
    _apply_verified_crests,
    _competition_detail,
    _home_data,
    _jsonable,
    _player_detail,
    _team_detail,
)


def _player_search_entry(player_id: int, roster_rows, player_profile) -> dict:
    by_team: dict[tuple[int, str, str, str | None, str], None] = {}
    for competition_name, member in roster_rows:
        by_team[(member.team_id, member.team_name, competition_name, member.crest_url, "")] = None
    links = [list(item[:4]) for item in sorted(by_team, key=lambda item: (item[2], item[1].casefold()))]
    teams = sorted({f"{name} · {league}" for _, name, league, _, _ in by_team})
    return {
        "kind": "Jugador",
        "entity_id": player_id,
        "name": player_profile.get("display_name") or next(iter(roster_rows))[1].player_name,
        "country_or_nationality": player_profile.get("nationality"),
        "leagues_or_teams": ", ".join(teams),
        "crest_url": None,
        "team_links": links,
    }


def main() -> int:
    settings = get_settings()
    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            session.connection().exec_driver_sql("SET TRANSACTION READ ONLY")
            service = CompetitionService(session)
            items = service.get_competitions()
            by_code = {item.code: item for item in items}
            ordered = [by_code[code] for code in FEATURED_COMPETITION_PRIORITY if code in by_code]
            competitions = []
            team_memberships: dict[int, set[int]] = defaultdict(set)
            player_memberships: dict[int, list[tuple[str, object]]] = defaultdict(list)
            team_competitions: dict[int, set[str]] = defaultdict(set)
            all_matches = []
            current_positions: dict[str, dict[str, tuple[int, int]]] = {}

            for comp in ordered:
                seasons = service.get_seasons(comp.code)
                if not seasons:
                    continue
                season = seasons[0]
                detail = _competition_detail(session, comp.code, str(season.competition_season_id))
                if detail is None:
                    continue
                # The exported view advertises only data we actually packaged.
                detail["seasons"] = [_jsonable(season)]
                competitions.append(detail)
                standings = detail.get("standings") or []
                if standings:
                    current_positions[comp.code] = {
                        normalize_team_name(row.get("team_name", "")): (
                            int(row.get("position") or 999), len(standings)
                        )
                        for row in standings
                    }
                for match in detail.get("matches", []):
                    all_matches.append(match)
                    for side in ("home", "away"):
                        team_id = match.get(side, {}).get("id")
                        if team_id is not None:
                            team_memberships[int(team_id)].add(season.competition_season_id)
                            team_competitions[int(team_id)].add(comp.name)
                for roster in player_roster_for_competition_season(
                    session, season.competition_season_id
                ):
                    team_memberships[roster.team_id].add(season.competition_season_id)
                    team_competitions[roster.team_id].add(comp.name)
                    player_memberships[roster.player_id].append((comp.name, roster))

            timezone = ZoneInfo(settings.app_timezone)
            today = datetime.now(timezone).date()
            home = _home_data(session, today)
            priority_policy = featured_match_policy(session)

            # Include current team and player profiles, plus searchable index.
            team_profiles = {}
            team_search = {}
            player_profiles = {}
            for team_id, season_ids in team_memberships.items():
                team = session.get(Team, team_id)
                if team is None:
                    continue
                team_search[str(team_id)] = {
                    "kind": "Equipo",
                    "entity_id": team.id,
                    "name": team.canonical_name,
                    "country_or_nationality": team.country_name,
                    "leagues_or_teams": ", ".join(sorted(team_competitions[team_id])),
                    "crest_url": team.crest_url,
                    "team_links": [],
                }
                default_profile = _team_detail(session, team_id, None)
                if default_profile:
                    default_profile["competitions"] = [
                        item for item in default_profile["competitions"]
                        if item.get("competition_season_id") in season_ids
                    ]
                    team_profiles[str(team_id)] = default_profile
                for season_id in season_ids:
                    profile = _team_detail(session, team_id, str(season_id))
                    if profile:
                        profile["competitions"] = [
                            item for item in profile["competitions"]
                            if item.get("competition_season_id") in season_ids
                        ]
                        team_profiles[f"{team_id}:{season_id}"] = profile

            for player_id, memberships in player_memberships.items():
                detail = _player_detail(session, player_id)
                if not detail:
                    continue
                player_profiles[str(player_id)] = detail
                player_search = _player_search_entry(player_id, memberships, detail)
                # Keep both club search hits and the player's current memberships.
                team_profiles.setdefault(f"__search_player_{player_id}", player_search)

            search_index = list(team_search.values()) + [
                value for key, value in team_profiles.items() if key.startswith("__search_player_")
            ]
            team_profiles = {key: value for key, value in team_profiles.items() if not key.startswith("__search_player_")}
            for match in all_matches:
                kickoff = match.get("kickoff")
                if kickoff:
                    try:
                        local_day = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00")).astimezone(timezone).date().isoformat()
                    except (ValueError, TypeError):
                        local_day = ""
                else:
                    local_day = ""
                item = SimpleNamespace(
                    home_team=match["home"]["name"],
                    away_team=match["away"]["name"],
                    competition_code=match.get("competition_code"),
                    status=match.get("status"),
                    kickoff_utc=kickoff or "9999-12-31T00:00:00+00:00",
                )
                match["_local_date"] = local_day
                match["_featured_rank"] = _jsonable(
                    featured_match_key(item, current_positions, priority_policy)
                )

            bundle = _apply_verified_crests(_jsonable({
                "exported_at": datetime.now(timezone).isoformat(timespec="minutes"),
                "timezone": settings.app_timezone,
                "home": home,
                "competitions": competitions,
                "teams": team_profiles,
                "players": player_profiles,
                "search": search_index,
                "matches": all_matches,
                "competition_priority": list(FEATURED_COMPETITION_PRIORITY),
            }))

        html = (ROOT / "src/data90/ui/web/index.html").read_text(encoding="utf-8")
        logo_svg = (ROOT / "src/data90/ui/web/data90-mark.svg").read_text(
            encoding="utf-8"
        )
        full_logo_svg = (ROOT / "src/data90/ui/web/data90-logo-full.svg").read_text(
            encoding="utf-8"
        )
        logo_data_uri = (
            "data:image/svg+xml;base64,"
            + base64.b64encode(logo_svg.encode("utf-8")).decode("ascii")
        )
        html = html.replace('src="/assets/data90-mark.svg"', f'src="{logo_data_uri}"')
        full_logo_data_uri = (
            "data:image/svg+xml;base64,"
            + base64.b64encode(full_logo_svg.encode("utf-8")).decode("ascii")
        )
        html = html.replace(
            'src="/assets/data90-logo-full.svg"', f'src="{full_logo_data_uri}"'
        )
        html = html.replace(
            '    <script>\n'
            '      if (location.protocol === "file:") {\n'
            '        location.replace(new URL("../../../../dist/share/Data90-Compartir.html", location.href).href);\n'
            '      }\n'
            '    </script>\n',
            "",
        )
        css = (ROOT / "src/data90/ui/web/app.css").read_text(encoding="utf-8")
        js = (ROOT / "src/data90/ui/web/app.js").read_text(encoding="utf-8")
        html = html.replace('<link rel="stylesheet" href="/assets/app.css">', f'<style id="offline-style">{css}</style>')
        html = html.replace('<script src="/assets/app.js" defer></script>', '')
        html = html.replace(
            '      <button class="button primary sync-button" id="syncButton" '
            'type="button">Actualizar datos</button>\n',
            "",
        )
        sync_panel = (
            '    <section class="sync-panel" id="syncPanel" hidden aria-live="polite">\n'
            '      <div class="sync-panel-heading">\n'
            '        <strong>Actualización completa</strong>\n'
            '        <span id="syncSummary">Lista para iniciar.</span>\n'
            '      </div>\n'
            '      <progress id="syncProgress" hidden></progress>\n'
            '      <details id="syncDetails">\n'
            '        <summary>Ver fuentes y pasos</summary>\n'
            '        <ul id="syncLog"></ul>\n'
            '      </details>\n'
            '    </section>\n'
        )
        html = html.replace(sync_panel, "")
        html = html.replace('<footer class="app-footer"><span>Data90</span><span>Datos de las fuentes configuradas en la aplicación</span></footer>',
            f'<footer class="app-footer"><span>Data90 · Copia descargable de solo lectura</span><span>Datos exportados {bundle["exported_at"]} · Los escudos y logos requieren conexión a internet</span></footer>')
        shim = r"""
  const OFFLINE = JSON.parse(document.getElementById("D90-export-data").textContent);
  function offlineHome(requested) {
    const base = new Date(`${requested}T12:00:00-03:00`);
    const dateKey = date => new Intl.DateTimeFormat("en-CA", {timeZone: OFFLINE.timezone, year:"numeric",month:"2-digit",day:"2-digit"}).format(date);
    const priority = OFFLINE.competition_priority;
    const byCode = new Map(OFFLINE.competitions.map(c=>[c.competition.code,c.competition]));
    const rank = (a,b)=>{
      const x=a._featured_rank||[], y=b._featured_rank||[];
      for(let i=0;i<Math.max(x.length,y.length);i++){if(x[i]<y[i])return -1;if(x[i]>y[i])return 1;}
      return String(a.kickoff||"").localeCompare(String(b.kickoff||""));
    };
    const days=[];
    for(let offset=0;offset<6;offset++){
      const date=new Date(base);date.setDate(date.getDate()+offset);const key=dateKey(date);
      const matches=OFFLINE.matches.filter(m=>m._local_date===key);
      const groups=priority.map(code=>{
        const items=matches.filter(m=>m.competition_code===code).sort((a,b)=>String(a.kickoff||"").localeCompare(String(b.kickoff||"")));
        return items.length?{competition:byCode.get(code),matches:items,featured:[...items].sort(rank)[0]}:null;
      }).filter(Boolean);
      days.push({date:key,match_count:matches.length,featured:matches.length?[...matches].sort(rank)[0]:null,competitions:groups});
    }
    return {today:requested,timezone:OFFLINE.timezone,days,today_matches:OFFLINE.matches.filter(m=>m._local_date===requested),competitions:OFFLINE.home.competitions};
  }
  async function offlineApi(path) {
    // Parse API routes independently from location.href. A standalone file can be
    // opened from file:// or a mobile content:// URI, neither of which should
    // affect matching of the app's root-relative API paths.
    const raw=String(path||"");
    const queryIndex=raw.indexOf("?");
    const pathPart=(queryIndex<0?raw:raw.slice(0,queryIndex)).split("#",1)[0];
    const queryPart=queryIndex<0?"":raw.slice(queryIndex+1).split("#",1)[0];
    const normalized=pathPart.replace(/^https?:\/\/[^/]+/i,"").replace(/\/+$/,"")||"/";
    const p=normalized.startsWith("/api/")?normalized:(normalized.includes("/api/")?normalized.slice(normalized.indexOf("/api/")):normalized);
    const q=new URLSearchParams(queryPart);
    if(p==="/api/competitions" || p==="/api/competitions/index") return OFFLINE.competitions.map(x=>x.competition);
    if(p==="/api/home" || p==="/api/home/") return offlineHome(q.get("date")||OFFLINE.home.today);
    if(p==="/api/search"){
      const term=(q.get("q")||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase();
      return OFFLINE.search.filter(x=>`${x.name} ${x.country_or_nationality||""} ${x.leagues_or_teams||""}`.normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase().includes(term)).slice(0,80);
    }
    let m=p.match(/^\/api\/competition\/([^/]+)$/);
    if(m){const comp=OFFLINE.competitions.find(x=>x.competition.code===decodeURIComponent(m[1]));if(comp)return comp;}
    m=p.match(/^\/api\/team\/(\d+)$/);
    if(m){const id=m[1],season=q.get("season");const result=OFFLINE.teams[season?`${id}:${season}`:id]||OFFLINE.teams[id];if(result)return result;}
    m=p.match(/^\/api\/player\/(\d+)$/);if(m&&OFFLINE.players[m[1]])return OFFLINE.players[m[1]];
    throw new Error(`Ruta no incluida en la copia exportada: ${p}`);
  }
"""
        js = js.replace("  async function api(path) {", shim + "\n  async function api(path) {")
        start = js.index("  async function api(path) {")
        end = js.index("\n  function navigate(path)", start)
        js = js[:start] + "  async function api(path) { return offlineApi(path); }" + js[end:]
        embedded_data = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        html = html.replace("</body>",
            f'<script id="D90-export-data" type="application/json">{embedded_data}</script>\n<script>{js}</script>\n</body>')
        output_dir = ROOT / "dist" / "share"
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = [
            output_dir / "Data90-Compartir.html",
        ]
        for output in outputs:
            output.write_text(html, encoding="utf-8")
        print(json.dumps({
            "outputs": [str(output) for output in outputs],
            "bytes": outputs[0].stat().st_size,
            "competitions": len(bundle["competitions"]),
            "teams": len(team_search),
            "players": len(player_profiles),
            "matches": len(all_matches),
            "exported_at": bundle["exported_at"],
        }, ensure_ascii=False))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
