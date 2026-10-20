(() => {
  const app = document.querySelector("#appView");
  const searchForm = document.querySelector("#searchForm");
  const searchInput = document.querySelector("#globalSearch");
  const syncButton = document.querySelector("#syncButton");
  const syncPanel = document.querySelector("#syncPanel");
  const syncSummary = document.querySelector("#syncSummary");
  const syncProgress = document.querySelector("#syncProgress");
  const syncDetails = document.querySelector("#syncDetails");
  const syncLog = document.querySelector("#syncLog");
  const brandPreviewButton = document.querySelector("#brandPreviewButton");
  const brandPreviewDialog = document.querySelector("#brandPreviewDialog");
  const brandPreviewClose = document.querySelector("#brandPreviewClose");
  let syncPoll = null;
  function projectToday() {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Argentina/Buenos_Aires", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  }
  const state = { route: null, home: null, selectedDate: projectToday(),
    fixtureDate: null, competitionTab: "standings", teamTab: "squad", request: 0 };
  const competitionNav = ["UCL", "UEL", "PL", "LALIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "EREDIVISIE"];
  const missing = "Sin dato publicado";

  function el(tag, cls, content) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (content !== undefined && content !== null) node.textContent = String(content);
    return node;
  }
  function button(label, action, cls = "button ghost") {
    const node = el("button", cls, label);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }
  function linkButton(label, action, cls = "") {
    const node = el("button", cls, label);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }
  function crest(url, name, size = "") {
    if (!url || typeof url !== "string" || !/^https:\/\//i.test(url) && !url.startsWith("data:image/")) {
      return el("span", `crest crest-placeholder ${size}`.trim(), (name || "?").trim().slice(0, 1).toUpperCase());
    }
    const image = document.createElement("img");
    image.className = `crest ${size}`.trim();
    image.alt = `Escudo de ${name || "equipo"}`;
    image.loading = "lazy";
    image.src = url;
    image.addEventListener("error", () => {
      const fallback = el("span", `crest crest-placeholder ${size}`.trim(), (name || "?").trim().slice(0, 1).toUpperCase());
      image.replaceWith(fallback);
    }, { once: true });
    return image;
  }
  function fmtDate(value, options = {}) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("es-AR", { timeZone: "America/Argentina/Buenos_Aires", ...options }).format(parsed);
  }
  function dateLong(value) { return fmtDate(`${value}T12:00:00`, { weekday: "long", day: "numeric", month: "long" }); }
  function num(value, decimals = 0) {
    if (value === null || value === undefined || value === "") return "—";
    return typeof value === "number" && decimals ? value.toFixed(decimals) : String(value);
  }
  function pct(value) { return value === null || value === undefined ? "—" : `${Number(value).toFixed(1)}%`; }
  function statusText(match) {
    if (["FINISHED", "AWARDED"].includes(match.status)) return "Finalizado";
    if (["LIVE", "PAUSED"].includes(match.status)) return "En vivo";
    return fmtDate(match.kickoff, { hour: "2-digit", minute: "2-digit" });
  }
  function stageLabel(match, fallback = "Partido") {
    const stage = String(match.stage || "");
    const round = String(match.round_label || "");
    const source = `${stage} ${round}`.replaceAll("_", " ");
    const europeanLeagueStage = ["UCL", "UEL"].includes(match.competition_code) || /league\s*stage|spieltag/i.test(source);
    if (europeanLeagueStage && /league\s*stage|spieltag/i.test(source)) {
      const parsed = source.match(/(?:spieltag|matchday|jornada)\s*#?\s*(\d+)|(\d+)\.?\s*spieltag/i)
        || source.match(/league\s*stage\D*(\d+)/i);
      const parsedNumber = parsed?.[1] || parsed?.[2];
      const matchday = Number(match.matchday) > 0 ? Number(match.matchday) : Number(parsedNumber);
      return `Jornada ${Number.isFinite(matchday) && matchday > 0 ? matchday : "—"}`;
    }
    return round || stage || fallback;
  }
  function teamLink(team, cls = "team-link", side = "") {
    const wrapper = el("button", `${cls} ${side}`.trim());
    wrapper.type = "button";
    wrapper.title = `Abrir ficha de ${team.name}`;
    wrapper.append(crest(team.crest || team.crest_url, team.name, "small"), el("strong", "", team.name));
    wrapper.addEventListener("click", () => navigate(`/team/${team.id}`));
    return wrapper;
  }
  function scoreNode(match) {
    if (match.home_score === null || match.home_score === undefined || match.away_score === null || match.away_score === undefined) {
      return el("span", "versus", "VS");
    }
    const score = el("span", "score");
    let homeClass = "neutral", awayClass = "neutral";
    if (["LIVE", "PAUSED", "FINISHED"].includes(match.status)) {
      if (match.home_score === match.away_score) homeClass = awayClass = "draw";
      else if (match.home_score > match.away_score) { homeClass = "win"; awayClass = "loss"; }
      else { homeClass = "loss"; awayClass = "win"; }
    }
    score.append(el("span", homeClass, match.home_score), el("span", "", "–"), el("span", awayClass, match.away_score));
    return score;
  }
  function fixtureRow(match) {
    const europeanCompetition = ["UCL", "UEL"].includes(String(match.competition_code || "").toUpperCase());
    const row = el("article", `fixture-row${europeanCompetition ? " european-match" : ""}`);
    const meta = el("div", "fixture-meta");
    const stateLabel = el("span", ["LIVE", "PAUSED"].includes(match.status) ? "result-live" : "", statusText(match));
    meta.append(stateLabel, el("span", "", stageLabel(match)));
    const teams = el("div", "fixture-teams");
    const homeLink = teamLink(match.home);
    const awayLink = teamLink(match.away, "team-link", "away");
    if (europeanCompetition) {
      homeLink.classList.add("european-home");
      awayLink.classList.add("european-away");
    }
    teams.append(homeLink, scoreNode(match), awayLink);
    row.append(meta, teams);
    row.addEventListener("dblclick", () => navigate(`/team/${match.home.id}`));
    return row;
  }
  function appendTable(host, headers, rows, { rowLink = null, minWidth, emptyMessage = "No hay datos publicados para mostrar.", tableClass = "", sortable = null } = {}) {
    if (!rows.length) { host.append(empty(emptyMessage)); return; }
    const wrap = el("div", "data-table-wrap");
    const table = el("table", `data-table ${tableClass}`.trim());
    if (minWidth >= 1300) table.classList.add("wide-table");
    else if (minWidth >= 950) table.classList.add("medium-table");
    const thead = el("thead"), tr = el("tr");
    let sortColumn = -1, sortDirection = 1;
    const headerButtons = [];
    headers.forEach((header, index) => {
      const th = el("th", index === 0 ? "name-cell" : "");
      if (sortable) {
        const control = button(header, () => sortRows(index), "sortable-header");
        control.setAttribute("aria-sort", "none");
        headerButtons[index] = control;
        th.append(control);
      } else th.textContent = header;
      tr.append(th);
    });
    thead.append(tr);
    const tbody = el("tbody");
    const entries = rows.map((row, index) => ({ row, index, keys: sortable?.keys?.[index] || [] }));
    function compareSortValues(left, right, kind) {
      const a = left === undefined ? null : left, b = right === undefined ? null : right;
      if (a === null || a === "") return b === null || b === "" ? 0 : 1;
      if (b === null || b === "") return -1;
      if (kind === "position") {
        const rank = Number(a[0]) - Number(b[0]);
        return rank || String(a[1]).localeCompare(String(b[1]), "es", { sensitivity: "base" });
      }
      if (kind === "number") return Number(a) - Number(b);
      return String(a).localeCompare(String(b), "es", { sensitivity: "base", numeric: true });
    }
    function buildRow({ row, index }) {
      const line = el("tr");
      row.forEach((cell, index) => {
        const td = el("td", index === 0 ? "name-cell" : "");
        td.dataset.label = headers[index] || "";
        if (cell instanceof Node) td.append(cell); else td.textContent = cell === null || cell === undefined ? "—" : String(cell);
        line.append(td);
      });
      if (rowLink) { line.classList.add("clickable-row"); line.addEventListener("click", (event) => { if (!event.target.closest("button,a")) rowLink(index); }); }
      return line;
    }
    function renderRows() { tbody.replaceChildren(...entries.map(buildRow)); }
    function sortRows(column) {
      if (sortColumn === column) sortDirection *= -1;
      else { sortColumn = column; sortDirection = 1; }
      const kind = sortable.types[column] || "text";
      entries.sort((a, b) => {
        const left = a.keys[column], right = b.keys[column];
        const leftMissing = left === null || left === undefined || left === "";
        const rightMissing = right === null || right === undefined || right === "";
        if (leftMissing || rightMissing) return leftMissing === rightMissing ? a.index - b.index : (leftMissing ? 1 : -1);
        return sortDirection * compareSortValues(left, right, kind) || a.index - b.index;
      });
      headerButtons.forEach((control, index) => {
        const active = index === sortColumn;
        control.setAttribute("aria-sort", active ? (sortDirection === 1 ? "ascending" : "descending") : "none");
        control.dataset.direction = active ? (sortDirection === 1 ? "asc" : "desc") : "";
      });
      renderRows();
    }
    if (sortable && sortable.initialColumn >= 0) sortRows(sortable.initialColumn);
    else renderRows();
    table.append(thead, tbody); wrap.append(table); host.append(wrap);
  }
  function empty(message) { return el("div", "empty-state", message); }
  function addHeading(host, title, hint = "") {
    const heading = el("div", "section-heading");
    heading.append(el("h2", "", title));
    if (hint) heading.append(el("p", "muted", hint));
    host.append(heading);
  }
  async function api(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Error ${response.status}`);
    return data;
  }
  function showSyncState(data) {
    if (!data || data.status === "idle") return;
    syncPanel.hidden = false;
    const labels = {
      running: "En curso. Se consultan las fuentes una por una y se respetan sus límites.",
      completed: "Terminó correctamente.",
      partial: "Terminó con fuentes o datos pendientes; revisá el detalle.",
      busy: "La tarea diaria ya está actualizando; no se inició otra en paralelo.",
      failed: "No pudo completarse; revisá el detalle.",
    };
    syncSummary.textContent = data.message || labels[data.status] || "Estado no disponible.";
    syncButton.disabled = data.status === "running";
    syncButton.textContent = data.status === "running" ? "Actualizando…" : "Actualizar datos";
    syncProgress.hidden = data.status !== "running";
    syncProgress.removeAttribute("value");
    syncLog.replaceChildren(...(data.lines || []).slice(-24).map(line => el("li", "", line)));
    if (data.status === "running") {
      if (syncPoll) clearInterval(syncPoll);
      syncPoll = setInterval(async () => {
        try {
          const current = await api("/api/sync/status");
          showSyncState(current);
          if (current.status !== "running" && syncPoll) { clearInterval(syncPoll); syncPoll = null; }
        } catch (error) {
          syncSummary.textContent = `No se pudo consultar el avance: ${error.message}`;
        }
      }, 2500);
    } else if (syncPoll) {
      clearInterval(syncPoll); syncPoll = null;
    }
  }
  async function startFullSync() {
    syncButton.disabled = true;
    syncPanel.hidden = false;
    syncSummary.textContent = "Preparando actualización completa…";
    syncProgress.hidden = false;
    try {
      const response = await fetch("/api/sync", { method: "POST", credentials: "same-origin", cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `Error ${response.status}`);
      showSyncState(data);
    } catch (error) {
      syncButton.disabled = false;
      syncProgress.hidden = true;
      syncSummary.textContent = error.message || "No se pudo iniciar la actualización.";
    }
  }
  if (syncButton) {
    syncButton.addEventListener("click", startFullSync);
    api("/api/sync/status").then(showSyncState).catch(() => {});
  }
  function navigate(path) { location.hash = `#${path}`; }
  function parseRoute() {
    const raw = location.hash.slice(1) || "/home";
    const [path, query = ""] = raw.split("?");
    return { path: path.startsWith("/") ? path : `/${path}`, query: new URLSearchParams(query) };
  }
  function setNav(path) {
    const active = path.startsWith("/competition") ? "competitions" : "home";
    document.querySelectorAll("[data-nav]").forEach(item => item.classList.toggle("active", item.dataset.nav === active));
  }
  function updateRouteFromHash() {
    state.route = parseRoute(); setNav(state.route.path); renderRoute();
  }
  async function renderRoute() {
    const token = ++state.request;
    const { path, query } = state.route;
    app.replaceChildren(el("section", "loading-state", ""));
    app.firstChild.append(el("span", "loader"), el("p", "", "Cargando datos reales…"));
    try {
      let data;
      if (path === "/home") { data = await api(`/api/home?date=${encodeURIComponent(state.selectedDate)}`); if (token !== state.request) return; state.home = data; renderHome(data); return; }
      if (path === "/competitions") { const comps = await api("/api/competitions"); if (token !== state.request) return; renderCompetitionList(comps); return; }
      if (path.startsWith("/competition/")) { const code = decodeURIComponent(path.split("/")[2] || ""); const season = query.get("season"); data = await api(`/api/competition/${encodeURIComponent(code)}${season ? `?season=${encodeURIComponent(season)}` : ""}`); if (token !== state.request) return; renderCompetition(data); return; }
      if (path.startsWith("/team/")) { const id = path.split("/")[2]; const season = query.get("season"); data = await api(`/api/team/${encodeURIComponent(id)}${season ? `?season=${encodeURIComponent(season)}` : ""}`); if (token !== state.request) return; renderTeam(data); return; }
      if (path.startsWith("/player/")) { data = await api(`/api/player/${encodeURIComponent(path.split("/")[2])}`); if (token !== state.request) return; renderPlayer(data); return; }
      if (path === "/search") { data = await api(`/api/search?q=${encodeURIComponent(query.get("q") || "")}`); if (token !== state.request) return; renderSearch(data, query.get("q") || ""); return; }
      app.replaceChildren(errorPanel("No encontré esa pantalla."));
    } catch (error) {
      if (token !== state.request) return;
      app.replaceChildren(errorPanel(error.message || "No se pudieron cargar los datos."));
    }
  }
  function errorPanel(message) {
    const section = el("section", "error-state");
    section.append(el("strong", "", "No se pudo cargar esta pantalla"), el("p", "", message), button("Reintentar", renderRoute));
    return section;
  }
  function renderHome(data) {
    const root = el("div", "home-page");
    const heading = el("div", "page-heading");
    const copy = el("div"); copy.append(el("div", "eyebrow", "Fútbol · calendario real"), el("h1", "", "Próximos seis días"), el("p", "", `${data.timezone} · Horarios mostrados en la zona del proyecto`));
    const date = document.createElement("input"); date.className = "date-picker"; date.type = "date"; date.value = state.selectedDate; date.setAttribute("aria-label", "Elegir fecha de inicio del calendario");
    date.addEventListener("change", () => { state.selectedDate = date.value || projectToday(); state.fixtureDate = state.selectedDate; renderRoute(); });
    const actions = el("div", "heading-actions"); actions.append(date);
    heading.append(copy, actions); root.append(heading);
    const datesTitle = el("div", "section-heading"); datesTitle.append(el("h2", "", "Calendario destacado"), el("p", "muted", `${data.days.reduce((n, d) => n + d.match_count, 0)} partidos en el período`)); root.append(datesTitle);
    const strip = el("div", "day-strip");
    const fixtureDate = state.fixtureDate || data.today;
    data.days.forEach(day => {
      const card = el("button", `day-card ${day.date === fixtureDate ? "selected" : ""}`); card.type = "button";
      const top = el("div", "day-card-top"); top.append(el("strong", "", fmtDate(`${day.date}T12:00:00`, { weekday: "short", day: "2-digit", month: "short" }).toUpperCase()), el("span", "", `${day.match_count} ${day.match_count === 1 ? "PARTIDO" : "PARTIDOS"}`));
      card.append(top);
      if (day.featured) {
        const f = day.featured, spotlight = el("div", "spotlight");
        spotlight.append(spotClub(f.home), spotScore(f), spotClub(f.away)); card.append(spotlight);
      } else {
        const noMatch = el("div", "spotlight spotlight-empty"); noMatch.append(el("span", "match-label", "Sin partidos programados")); card.append(noMatch);
      }
      card.addEventListener("click", () => { state.fixtureDate = day.date; renderHome(data); }); strip.append(card);
    });
    root.append(strip);
    const fixtureHeading = el("div", "section-heading"); fixtureHeading.append(el("h2", "", "Fixture del día"), el("p", "muted", dateLong(fixtureDate))); root.append(fixtureHeading);
    const selectedDay = data.days.find(day => day.date === fixtureDate);
    const grid = el("div", "fixture-grid");
    if (!selectedDay || !selectedDay.competitions.length) grid.append(empty("Sin partidos para mostrar en esta fecha."));
    else selectedDay.competitions.forEach(group => {
      const card = el("section", "panel fixture-league");
      const head = el("div", "fixture-league-head"); head.append(crest(group.competition.logo, group.competition.name, "comp"), el("strong", "", group.competition.name.toUpperCase()), el("span", "", `${group.matches.length} ${group.matches.length === 1 ? "PARTIDO" : "PARTIDOS"}`));
      head.addEventListener("click", () => navigate(`/competition/${encodeURIComponent(group.competition.code)}`)); head.classList.add("interactive");
      const rows = el("div", "fixture-rows"); group.matches.forEach(match => rows.append(fixtureRow(match)));
      card.append(head, rows); grid.append(card);
    });
    root.append(grid); app.replaceChildren(root);
  }
  function spotClub(team) { const node = el("div", "spot-club"); node.append(crest(team.crest, team.name, "large"), el("strong", "", team.name)); return node; }
  function spotScore(match) { const node = el("div", "spot-center"); node.append(scoreNode(match), el("small", "", statusText(match)), el("small", "", stageLabel(match, ""))); return node; }
  function renderCompetitionList(items) {
    const root = el("div"); const head = el("div", "page-heading"); const copy = el("div"); copy.append(el("div", "eyebrow", "Catálogo deportivo"), el("h1", "", "Competiciones"), el("p", "", "Temporadas, tablas, calendario y goleadores disponibles.")); head.append(copy); root.append(head);
    const cards = el("div", "fixture-grid");
    if (!items.length) cards.append(empty("No hay competiciones cargadas."));
    items.forEach(item => {
      const card = el("a", "panel content-card competition-card"); card.href = `#/competition/${encodeURIComponent(item.code)}`;
      const title = el("div", "competition-title"); title.append(crest(item.logo, item.name, "large")); const detail = el("div"); detail.append(el("h3", "", item.name), el("p", "muted", `${item.season || "Sin temporada"} · ${item.country || "Europa"}`)); title.append(detail);
      const meta = el("p", "muted", `${item.teams} equipos · ${item.matches} partidos`); card.append(title, meta); cards.append(card);
    });
    root.append(cards); app.replaceChildren(root);
  }
  function compTabs(data) {
    const tabs = [{id:"standings",label:"Tabla de posiciones"},{id:"teamstats",label:"Estadísticas de equipos"},{id:"upcoming",label:"Próximos partidos"},{id:"scorers",label:"Goleadores"}];
    if (["UCL","UEL"].includes(data.competition.code)) tabs.push({id:"knockouts",label:"Fase eliminatoria"});
    return tabs;
  }
  function renderCompetition(data) {
    const root = el("div", ["competition-page", ["UCL","UEL"].includes(data.competition.code) ? "european-cup" : "", data.competition.type === "LEAGUE" ? "league-competition" : ""].filter(Boolean).join(" "));
    root.dataset.competition = data.competition.code;
    const header = el("div", "page-heading"); const title = el("div", "competition-title"); title.append(crest(data.competition.logo, data.competition.name, "large")); const copy = el("div"); copy.append(el("div", "eyebrow", data.competition.country || "Competición"), el("h1", "", data.competition.name), el("p", "", data.season ? `${data.season.team_count} equipos · ${data.season.match_count} partidos · ${data.season.last_synced_at ? `Actualizado ${fmtDate(data.season.last_synced_at,{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"})}` : "Sin fecha de actualización"}` : "Todavía no hay temporada cargada")); title.append(copy);
    const selector = document.createElement("select"); selector.className = "season-select"; selector.setAttribute("aria-label", "Temporada");
    (data.seasons || []).forEach(s => { const option = document.createElement("option"); option.value = s.competition_season_id; option.textContent = s.label; option.selected = data.season?.competition_season_id === s.competition_season_id; selector.append(option); });
    selector.addEventListener("change", () => navigate(`/competition/${encodeURIComponent(data.competition.code)}?season=${selector.value}`));
    const actions = el("div", "heading-actions"); actions.append(selector); header.append(title, actions); root.append(header);
    const tabs = el("div", "subnav"); const navItems = compTabs(data); if (!navItems.some(t => t.id === state.competitionTab)) state.competitionTab = "standings";
    navItems.forEach(tab => { const b = button(tab.label, () => { state.competitionTab = tab.id; renderCompetition(data); }, `tab-button ${state.competitionTab === tab.id ? "active" : ""}`); tabs.append(b); }); root.append(tabs);
    const content = el("section", "tab-content");
    if (!data.season) content.append(empty("Esta competición aún no tiene temporada cargada."));
    else if (state.competitionTab === "standings") renderStandings(content, data);
    else if (state.competitionTab === "teamstats") renderTeamStatistics(content, data);
    else if (state.competitionTab === "upcoming") renderUpcoming(content, data);
    else if (state.competitionTab === "scorers") renderScorers(content, data.scorers);
    else if (state.competitionTab === "knockouts") renderKnockouts(content, data.matches);
    root.append(content); app.replaceChildren(root);
  }
  function renderStandings(host, data) {
    addHeading(host, "Tabla de posiciones", `${data.season.label} · Pos.: posición · Pts: puntos · PJ: partidos jugados · G: ganados · E: empatados · P: perdidos · GF: goles a favor · GC: goles en contra · DG: diferencia de goles (GF − GC).`);
    const rows = (data.standings || []).map(item => [item.position, teamCell(item.team_id, item.team_name, item.crest_url), el("strong","highlight-points",item.points), item.played, item.won, item.drawn, item.lost, el("strong","positive",item.goals_for), el("strong","negative",item.goals_against), el("strong",Number(item.goal_difference)>0?"positive":Number(item.goal_difference)<0?"negative":"",item.goal_difference)]);
    appendTable(host, ["Pos.","Equipo","Pts","PJ","G","E","P","GF","GC","DG"], rows, { rowLink: i => navigate(`/team/${data.standings[i].team_id}`), minWidth: 820, tableClass: "standings-table", emptyMessage: "Esta fuente no publicó una tabla de posiciones para la temporada." });
  }
  function teamCell(id, name, crestUrl) { const wrap = el("div", "table-team"); wrap.append(crest(crestUrl,name,"small"), linkButton(name, () => navigate(`/team/${id}`))); return wrap; }
  function renderTeamStatistics(host, data) {
    const expanded = data.competition.type === "LEAGUE" || ["UCL","UEL"].includes(data.competition.code);
    const legend = expanded
      ? "GF/PJ y GC/PJ: goles a favor y en contra por partido · % V: porcentaje de victorias · VI: vallas invictas · Total/Local/Visitante: sumas de la temporada · Prom./PJ: promedio por partido con dato · % partidos: partidos con al menos un evento · PJ: partidos con estadística publicada · Posesión: promedio de la temporada · Penales: convertidos o cometidos/intentados según Promiedos. Los agregados de Promiedos no incluyen desglose local/visitante."
      : "PJ: partidos jugados · V/E/D: victorias, empates y derrotas · GF/GC: goles a favor/en contra · DG: diferencia de goles · GF/PJ y GC/PJ: goles a favor/en contra por partido jugado · % V: porcentaje de victorias · VI: vallas invictas (partidos sin recibir goles).";
    addHeading(host, "Estadísticas de equipos", legend);
    const teams = [...(data.team_statistics || [])].sort((a,b) => {
      const x=a.metrics||{}, y=b.metrics||{};
      return (y.points-x.points)||(y.goal_difference-x.goal_difference)||(y.goals_for-x.goals_for)||String(b.team_name).toLowerCase().localeCompare(String(a.team_name).toLowerCase(),"es");
    });
    if (expanded) { renderExpandedTeamStatistics(host, teams); return; }
    const rows = teams.map(item => {
      const m = item.metrics || {};
      return [teamCell(item.team_id,item.team_name,item.crest_url),m.played,m.won,m.drawn,m.lost,
        el("strong","positive",num(m.goals_for)),el("strong","negative",num(m.goals_against)),
        el("strong",Number(m.goal_difference)>0?"positive":Number(m.goal_difference)<0?"negative":"",num(m.goal_difference)),
        num(m.average_goals_for,2),num(m.average_goals_against,2),pct(m.win_percent),m.clean_sheets];
    });
    appendTable(host,["Equipo","PJ","V","E","D","GF","GC","DG","GF/PJ","GC/PJ","% V","VI"],rows,{minWidth:850,emptyMessage:"No hay partidos finalizados para calcular estadísticas de equipos."});
  }
  function renderExpandedTeamStatistics(host, teams) {
    const definitions=[["yellow_cards","Tarjetas amarillas"],["red_cards","Tarjetas rojas"],["corners","Córners"],["possession_percentage","Posesión"],["penalties_scored","Penales convertidos"],["penalties_conceded","Penales cometidos"]];
    const wrap=el("div","data-table-wrap"),table=el("table","data-table expanded-team-stats"),colgroup=el("colgroup");
    [21,6,6,6,5,15,7,7,7,8,7,5].forEach(value=>{const col=document.createElement("col");col.style.width=`${value}%`;colgroup.append(col);});
    const thead=el("thead"),head=el("tr");
    ["Equipo","GF/PJ","GC/PJ","% V","VI","Métrica","Total","Local","Visitante","Prom./PJ","% partidos","PJ"].forEach((label,i)=>head.append(el("th",i===0?"name-cell":"",label)));
    thead.append(head);const tbody=el("tbody");
    teams.forEach(item=>{
      const m=item.metrics||{},agg=item.published_aggregate||{},events=item.event_statistics||{};
      definitions.forEach(([field,label],offset)=>{
        const tr=el("tr");
        if(offset===0){
          const team=el("td","name-cell team-stats-team");team.rowSpan=definitions.length;team.append(teamCell(item.team_id,item.team_name,item.crest_url));tr.append(team);
          [[num(m.average_goals_for,2),"positive"],[num(m.average_goals_against,2),"negative"],[pct(m.win_percent),""],[num(m.clean_sheets),""]].forEach(([v,c])=>{const td=el("td",`stat-center ${c}`);td.rowSpan=definitions.length;td.textContent=v;tr.append(td);});
        }
        const summary=events[field];let values;
        if(summary){values=[label,num(summary.total),num(summary.home_total),num(summary.away_total),num(summary.average_per_match,2),pct(summary.matches_with_event_percent),num(summary.matches_with_data)];}
        else if(field==="possession_percentage"&&agg.possession_percentage!=null){values=[label,pct(agg.possession_percentage),"—","—","—","—","—"];}
        else if(field==="penalties_scored"&&agg.penalties_scored!=null){values=[label,`${num(agg.penalties_scored)}/${num(agg.penalties_attempted)}`,"—","—","—","—","—"];}
        else if(field==="penalties_conceded"&&agg.penalties_conceded!=null){values=[label,`${num(agg.penalties_conceded)}/${num(agg.penalties_conceded_attempted)}`,"—","—","—","—","—"];}
        else values=[label,"—","—","—","—","—","—"];
        values.forEach((value,index)=>{const td=el("td",index===0?"metric-label":"stat-center",value);if(summary?.source)td.title=summary.source;if(!summary&&index===0&&["possession_percentage","penalties_scored","penalties_conceded"].includes(field))td.title=agg?"Promiedos: agregado de temporada, sin desglose local/visitante.":"Promiedos no publica este dato para esta competición.";tr.append(td);});
        tbody.append(tr);
      });
    });
    if(!teams.length){host.append(empty("No hay datos de equipos para esta temporada."));return;}
    table.append(colgroup,thead,tbody);wrap.append(table);host.append(wrap);
  }
  function isKnockout(match) {
    const phase=`${match.stage||""} ${match.round_label||""}`.toLowerCase().replaceAll("_"," ").replace(/[–—-]/g," ");
    return ["knockout","play off","playoff","round of","round-of","last 16","last 8","quarter final","semi final","final","octavos","dieciseisavos","cuartos","semifinal","repechaje","eliminatoria","1/8","1/4","1/16"].some(marker=>phase.includes(marker));
  }
  function matchDateLabel(match) {
    const date=fmtDate(match.kickoff,{day:"2-digit",month:"2-digit",year:"numeric"});
    if(["FINISHED","AWARDED"].includes(match.status))return `${date} · Finalizado`;
    if(["LIVE","PAUSED"].includes(match.status))return `${date} · En vivo`;
    return fmtDate(match.kickoff,{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
  }
  function renderUpcoming(host,data) {
    const matches=(data.matches||[]).filter(m=>new Date(m.kickoff)>=new Date()&&["SCHEDULED","POSTPONED"].includes(m.status));
    addHeading(host,"Próximos partidos",`${matches.length} partidos con fecha confirmada · Local primero, visitante después`);
    const hideStage=data.competition.type==="LEAGUE";
    const headers=hideStage?["Fecha y hora","Local","Visitante","Estado"]:["Fecha y hora","Jornada / ronda","Local","Visitante","Estado"];
    const rows=matches.map(m=>hideStage?[matchDateLabel(m),teamCell(m.home.id,m.home.name,m.home.crest),teamCell(m.away.id,m.away.name,m.away.crest),statusLabel(m.status)]:[matchDateLabel(m),stageLabel(m),teamCell(m.home.id,m.home.name,m.home.crest),teamCell(m.away.id,m.away.name,m.away.crest),statusLabel(m.status)]);
    appendTable(host,headers,rows,{minWidth:hideStage?760:900,tableClass:"fixtures-table",emptyMessage:"No hay próximos partidos con fecha confirmada para esta temporada."});
  }
  function statusLabel(status) {
    return ({SCHEDULED:"Programado",POSTPONED:"Aplazado",LIVE:"En vivo",PAUSED:"Pausado",FINISHED:"Finalizado",SUSPENDED:"Suspendido",CANCELLED:"Cancelado",UNKNOWN:"Sin confirmar"})[status]||status||"Sin confirmar";
  }
  function renderKnockouts(host,matches) {
    const selected=(matches||[]).filter(isKnockout).sort((a,b)=>new Date(a.kickoff)-new Date(b.kickoff));
    addHeading(host,"Fase eliminatoria",`${selected.length} partidos de fase eliminatoria`);
    const rows=selected.map(m=>[matchDateLabel(m),m.round_label||m.stage||"Eliminatoria",teamCell(m.home.id,m.home.name,m.home.crest),scoreNode(m),teamCell(m.away.id,m.away.name,m.away.crest),statusLabel(m.status)]);
    appendTable(host,["Fecha y hora","Ronda","Local","Resultado","Visitante","Estado"],rows,{minWidth:1000,tableClass:"knockouts-table",emptyMessage:"La fase eliminatoria aparecerá aquí cuando comience y se publiquen sus partidos."});
  }
  function renderScorers(host, scorers) {
    addHeading(host,"Goleadores","Datos de la temporada seleccionada; partidos jugados y asistencias solo cuando la fuente los publica.");
    const ordered = [...scorers].sort((a,b) => (b.goals||0)-(a.goals||0) || String(a.name).localeCompare(String(b.name),"es"));
    const rows = ordered.map((item,index) => [index+1,linkButton(item.name,()=>navigate(`/player/${item.id}`),"player-link"),item.team ? teamCell(item.team.id,item.team.name,item.team.crest) : "—",num(item.appearances),num(item.goals),num(item.assists)]);
    appendTable(host,["Pos.","Jugador","Equipo","PJ","Goles","Asistencias"],rows,{minWidth:850,tableClass:"scorers-table"});
  }
  function renderTeam(data) {
    const root = el("div"); const header = el("div", "page-heading");
    const back = button("← Inicio",()=>navigate("/home"));
    const hero = el("div","profile-hero"); hero.append(crest(data.team.crest,data.team.name,"large")); const copy = el("div","hero-copy"); copy.append(el("h1","",data.team.name),el("p","",`Entrenador: ${data.team.coach || "No informado"}`)); hero.append(copy);
    const season = document.createElement("select"); season.className="season-select"; season.setAttribute("aria-label","Competición y temporada");
    (data.competitions||[]).forEach(item=>{const op=document.createElement("option");op.value=item.competition_season_id;op.textContent=`${item.competition_name} · ${item.season_label}`;op.selected=item.competition_season_id===data.selected_season_id;season.append(op);});
    season.addEventListener("change",()=>navigate(`/team/${data.team.id}?season=${season.value}`));
    const actions=el("div","heading-actions"); actions.append(season); header.append(back); root.append(header,hero);
    const tabs=el("div","subnav"); const navItems=[{id:"squad",label:"Plantel"},{id:"matches",label:"Partidos"},{id:"calendar",label:"Calendario"},{id:"performance",label:"Rendimiento"},{id:"matchstats",label:"Estadísticas de partidos"}]; if(data.rivalries?.length || ["PL","LALIGA","SERIE_A","BUNDESLIGA","LIGUE_1","EREDIVISIE"].includes(data.selected_competition_code)) navItems.push({id:"classics",label:"Clásicos"});
    if(!navItems.some(t=>t.id===state.teamTab)) state.teamTab="squad";
    navItems.forEach(tab=>tabs.append(button(tab.label,()=>{state.teamTab=tab.id;renderTeam(data);},`tab-button ${state.teamTab===tab.id?"active":""}`)));
    root.append(tabs); const content=el("section");
    if(state.teamTab==="squad") renderSquad(content,data.squad);
    else if(state.teamTab==="matches") renderTeamMatches(content,data.match_history,data.team);
    else if(state.teamTab==="calendar") renderTeamCalendar(content,data);
    else if(state.teamTab==="performance") renderPerformance(content,data);
    else if(state.teamTab==="matchstats") renderMatchStats(content,data);
    else if(state.teamTab==="classics") renderRivalries(content,data);
    root.append(content); app.replaceChildren(root);
  }
  function age(birth) { if(!birth) return missing; const d=new Date(`${birth}T12:00:00`), now=new Date(); let n=now.getFullYear()-d.getFullYear(); if(now.getMonth()<d.getMonth() || now.getMonth()===d.getMonth()&&now.getDate()<d.getDate()) n--; return n; }
  function positionDisplay(value) {
    const position=String(value||"").normalize("NFKD").replace(/[\u0300-\u036f]/g,"").replace(/[-_]/g," ").toLocaleLowerCase("es").trim().replace(/\s+/g," ");
    if(!position)return "No informada";
    const exact={gk:"Portero",g:"Portero",p:"Portero",goalkeeper:"Portero",goalie:"Portero",keeper:"Portero",portero:"Portero",arquero:"Portero",goleiro:"Portero",doelman:"Portero",torwart:"Portero",gardien:"Portero",portiere:"Portero",cb:"Defensa central","centre back":"Defensa central","center back":"Defensa central","central defender":"Defensa central",zagueiro:"Defensa central",lb:"Lateral izquierdo","left back":"Lateral izquierdo",rb:"Lateral derecho","right back":"Lateral derecho",lwb:"Carrilero izquierdo","left wing back":"Carrilero izquierdo",rwb:"Carrilero derecho","right wing back":"Carrilero derecho",def:"Defensa",df:"Defensa",d:"Defensa",defender:"Defensa",defence:"Defensa",defense:"Defensa",defensa:"Defensa",abwehr:"Defensa",dm:"Mediocampista defensivo",cdm:"Mediocampista defensivo","defensive midfielder":"Mediocampista defensivo",cm:"Mediocampista central","central midfielder":"Mediocampista central","central midfield":"Mediocampista central",am:"Mediocampista ofensivo",cam:"Mediocampista ofensivo","attacking midfielder":"Mediocampista ofensivo",lm:"Mediocampista por izquierda","left midfielder":"Mediocampista por izquierda",rm:"Mediocampista por derecha","right midfielder":"Mediocampista por derecha",m:"Mediocampista",mf:"Mediocampista",midfielder:"Mediocampista",midfield:"Mediocampista",mediocampista:"Mediocampista",medio:"Mediocampista",milieu:"Mediocampista",mittelfeld:"Mediocampista",meia:"Mediocampista",lw:"Extremo izquierdo","left winger":"Extremo izquierdo",rw:"Extremo derecho","right winger":"Extremo derecho",ss:"Segundo delantero","second striker":"Segundo delantero",cf:"Delantero centro",st:"Delantero centro","centre forward":"Delantero centro","center forward":"Delantero centro",striker:"Delantero centro",fw:"Delantero",f:"Delantero",forward:"Delantero",attacker:"Delantero",attaquant:"Delantero",attaccante:"Delantero",atacante:"Delantero",delantero:"Delantero",spits:"Delantero",aanvaller:"Delantero",extremo:"Delantero",punta:"Delantero"};
    if(exact[position])return exact[position];
    if(/goalkeeper|goal keeper|goalie|keeper|portero|arquero|goleiro|doelman|torwart|gardien|portiere/.test(position))return "Portero";
    if(/defender|defence|defense|defens|defensa|verdediger|centre back|center back|full back|left back|right back|abwehr|difensore|zagueiro/.test(position))return "Defensa";
    if(/midfielder|midfield|mediocampista|medio|middenvelder|centrocampista|milieu|mittelfeld|meio campista/.test(position))return "Mediocampista";
    if(/forward|attacker|attack|offence|offense|delantero|spits|aanvaller|striker|winger|extremo|punta|attaccante|atacante|attaquant|sturm/.test(position))return "Delantero";
    return "Otra posición";
  }
  function renderSquad(host,squad) {
    addHeading(host,"Plantel",`${squad.length} jugadores vinculados a esta plantilla`);
    const rows=squad.map(item=>[linkButton(item.player_name,()=>navigate(`/player/${item.player_id}`),"player-link"),item.nationality||missing,positionDisplay(item.position),item.birth_date?age(item.birth_date):missing,num(item.shirt_number)]);
    const normalize=value=>String(value||"").normalize("NFKD").replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase("es").trim();
    const positionKey=value=>{
      const position=normalize(value);
      const rank=/goalkeeper|goal keeper|goalie|keeper|portero|arquero|doelman|torwart|gardien|portiere/.test(position)?0:
        /defender|defence|defens|defensa|verdediger|centre back|center back|full back|left back|right back/.test(position)?1:
        /midfielder|midfield|mediocampista|medio|middenvelder|centrocampista|attacking mid|left mid|right mid/.test(position)?2:
        /forward|attacker|attack|offence|offense|delantero|spits|aanvaller|striker|winger|extremo|punta/.test(position)?3:4;
      return [rank,position];
    };
    const sortable={
      initialColumn:2,
      types:["text","text","position","number","number"],
      keys:squad.map(item=>[normalize(item.player_name),normalize(item.nationality),positionKey(item.position),item.birth_date?age(item.birth_date):null,item.shirt_number===null||item.shirt_number===undefined?null:Number(item.shirt_number)])
    };
    appendTable(host,["Jugador","Nacionalidad","Posición","Edad","Dorsal"],rows,{minWidth:0,tableClass:"squad-table",sortable});
  }
  function renderTeamMatches(host,matches,team) {
    addHeading(host,"Historial de partidos",`${matches.length} partidos vinculados al equipo`);
    const sorted=[...matches].sort((a,b)=>new Date(b.kickoff_utc)-new Date(a.kickoff_utc));
    const rows=sorted.map(m=>[fmtDate(m.kickoff_utc,{day:"2-digit",month:"2-digit",year:"numeric"}),teamCell(m.opponent_id,m.opponent,m.opponent_crest_url),m.competition_name,m.season_label,m.venue,m.goals_for===null||m.goals_against===null?"—":`${m.goals_for} – ${m.goals_against}`,m.status==="FINISHED"?"Finalizado":statusText({status:m.status,kickoff:m.kickoff_utc})]);
    appendTable(host,["Fecha","Rival","Competición","Temporada","Localía","Resultado","Estado"],rows,{minWidth:0,tableClass:"team-match-history"});
  }
  function renderTeamCalendar(host,data) {
    const columns=el("div","team-calendar-columns");
    [["Últimos 5 partidos",data.recent_matches,false],["Próximos 5 partidos",data.upcoming_matches,true]].forEach(([title,items,upcoming])=>{
      const panel=el("section","panel team-calendar-panel");
      const heading=el("div","team-calendar-heading");heading.append(el("h3","",title),el("span","",`${items.length} partidos`));panel.append(heading);
      if(!items.length){panel.append(empty(upcoming?"No hay próximos partidos confirmados.":"No hay partidos terminados cargados."));}
      else {
        const list=el("div","team-calendar-list");
        items.forEach(m=>{
          const row=el("article","team-calendar-row");
          const date=el("div","team-calendar-date");date.append(el("strong","",fmtDate(m.kickoff_utc,{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})),el("small","",stageLabel(m)));
          const opponent=teamLink({id:m.opponent_id,name:m.opponent,crest:m.opponent_crest_url},"team-link team-calendar-rival");
          const venue=el("span","team-calendar-venue",m.venue||"Localía sin informar");
          const result=upcoming?el("span","team-calendar-result upcoming-result",statusText({status:m.status,kickoff:m.kickoff_utc})):el("span","team-calendar-result",m.goals_for===null||m.goals_against===null?"—":`${m.goals_for} – ${m.goals_against}`);
          if(!upcoming&&m.goals_for!==null&&m.goals_against!==null)result.classList.add(m.goals_for>m.goals_against?"win":m.goals_for<m.goals_against?"loss":"draw");
          row.append(date,opponent,venue,result);list.append(row);
        });
        panel.append(list);
      }
      columns.append(panel);
    }); host.append(columns);
  }
  function metricCard(title,value,detail,tone="") { const card=el("div",`kpi-card ${tone}`); card.append(el("label","",title),el("strong","",value??"—")); if(detail)card.append(el("small","",detail)); return card; }
  function formBadges(form) { const row=el("div","form-row"); if(!form){row.append(el("span","muted","Sin partidos con resultado"));return row;} for(const letter of form){const tone=letter==="W"?"win":letter==="D"?"draw":"loss";row.append(el("span",`form-badge ${tone}`,letter==="W"?"V":letter==="D"?"E":"D"));}return row; }
  function renderPerformance(host,data) {
    const m=data.metrics||{}; addHeading(host,"Rendimiento de temporada",`${m.played??0} partidos jugados · Posición ${data.standing.position??"—"} · ${data.standing.points??"—"} puntos`);
    const kpis=el("div","kpi-grid"); kpis.append(metricCard("Victorias",pct(m.win_percent),`${m.won??0} · ${m.home_won??0} local / ${m.away_won??0} visitante`,"win"),metricCard("Empates",pct(m.draw_percent),`${m.drawn??0} · ${m.home_drawn??0} local / ${m.away_drawn??0} visitante`,`draw`),metricCard("Derrotas",pct(m.loss_percent),`${m.lost??0} · ${m.home_lost??0} local / ${m.away_lost??0} visitante`,`loss`)); host.append(kpis);
    const dash=el("div","dashboard-grid"); const goals=el("section","dashboard-card"); goals.append(el("h3","","Goles por partido")); const split=el("div","goal-split"); const gf=el("div");gf.append(el("small","","A favor"),el("strong","",num(m.average_goals_for,2)));const ga=el("div");ga.append(el("small","","En contra"),el("strong","against",num(m.average_goals_against,2)));split.append(gf,ga);goals.append(split);
    const form=el("section","dashboard-card");form.append(el("h3","","Forma últimos 5"),formBadges(m.form_last_5),el("h3","","Forma últimos 10"),formBadges(m.form_last_10));
    const venue=el("section","dashboard-card");venue.append(el("h3","","Balance por localía"));const venueSplit=el("div","venue-split"); [["Local",m.home_played,m.home_won,m.home_drawn,m.home_lost,m.home_points],["Visitante",m.away_played,m.away_won,m.away_drawn,m.away_lost,m.away_points]].forEach(v=>{const d=el("div");d.append(el("small","",v[0]),el("strong","",`${v[1]} PJ · ${v[5]} pts`),el("small","",`${v[2]} V · ${v[3]} E · ${v[4]} D`));venueSplit.append(d);}); venue.append(venueSplit);dash.append(goals,form,venue);host.append(dash);
    const info=el("div","player-overview"); [["GF",m.goals_for],["GC",m.goals_against],["DG",m.goal_difference],["Vallas invictas",m.clean_sheets],["Sin marcar",m.failed_to_score],["Racha invicto",m.unbeaten_streak],["Victorias seguidas",m.win_streak],["Partidos marcando",m.scoring_streak]].forEach(([name,value])=>{const card=el("div","player-field");card.append(el("small","",name),el("strong","",num(value)));info.append(card);}); host.append(info);
  }
  function renderMatchStats(host,data) {
    addHeading(host,"Estadísticas de partidos","Promedios por partido y totales con desglose de localía, en una sola tabla.");
    const avg=new Map((data.match_statistics||[]).map(([name,value,count])=>[name,{value,count}]));const totals=new Map((data.match_stat_totals||[]).map(x=>[x.metric,x]));
    const metrics=["Posesión (%)","Tiros totales","Tiros al arco","Tiros bloqueados","Córners","Faltas","Fuera de juego","Tarjetas amarillas","Tarjetas rojas","Pases totales","Pases precisos","Atajadas"];
    const rows=metrics.map(name=>{const a=avg.get(name),key={"Tarjetas amarillas":"Amarillas","Tarjetas rojas":"Rojas","Córners":"Córners"}[name],t=key?totals.get(key):null;return [name,a?num(a.value,1)+(name==="Posesión (%)"?"%":""):"—",t?num(t.total):"—",t?num(t.home):"—",t?num(t.away):"—",t&&t.matches_played?`${t.matches_covered}/${t.matches_played}`:a?`${a.count} partidos`:"—"];});
    appendTable(host,["Métrica","Prom./PJ","Total","Local","Visitante","Cobertura"],rows,{minWidth:0,tableClass:"team-match-stats-table"});
  }
  function renderRivalries(host,data) {
    addHeading(host,"Clásicos y derbis","Hasta cinco enfrentamientos recientes por rival, cuando están cargados en la base.");
    if(!data.rivalries?.length){host.append(empty("No hay clásicos verificados con partidos disponibles para esta competición."));return;}
    data.rivalries.forEach(r=>{const card=el("section","panel rivalry-card");const head=el("div","rivalry-head");head.append(el("strong","",r.category));if(r.rival_id)head.append(teamLink({id:r.rival_id,name:r.rival_name,crest:r.rival_crest_url},"team-link"));else head.append(el("span","",r.rival_name));head.append(el("span","","Últimos 5 enfrentamientos"));card.append(head);
      const rows=(r.matches||[]).map(m=>{const gf=m.venue==="Local"?m.goals_for:m.goals_against,ga=m.venue==="Local"?m.goals_against:m.goals_for;const home=m.venue==="Local"?{id:data.team.id,name:data.team.name,crest:data.team.crest}:{id:m.opponent_id,name:m.opponent,crest:m.opponent_crest_url};const away=m.venue==="Local"?{id:m.opponent_id,name:m.opponent,crest:m.opponent_crest_url}:{id:data.team.id,name:data.team.name,crest:data.team.crest};return [fmtDate(m.kickoff_utc,{day:"2-digit",month:"2-digit",year:"numeric"}),m.round_label||(m.matchday?`Jornada ${m.matchday}`:m.stage||"—"),teamCell(home.id,home.name,home.crest),gf===null||ga===null?"—":`${gf} – ${ga}`,teamCell(away.id,away.name,away.crest)];});appendTable(card,["Fecha","Jornada","Local","Resultado","Visitante"],rows,{minWidth:0,tableClass:"derby-history-table"});host.append(card);});
  }
  function renderPlayer(data) {
    const root=el("div");const back=button("← Volver",()=>history.back());root.append(back);
    const hero=el("section","profile-hero player-hero");const copy=el("div","hero-copy");copy.append(el("div","eyebrow","Ficha del jugador"),el("h1","",data.display_name),el("p","",(data.team_competitions||[]).map(x=>`${x.team_name} · ${x.competition_name}`).join(" / ")||"Equipo no informado"));hero.append(copy);root.append(hero);
    const memberships=data.team_competitions||[];const primary=memberships[0]||{};const fields=[["Edad",data.age===null?missing:`${data.age} años`],["Nacionalidad",data.nationality||missing],["Posición",positionDisplay(data.position||primary.position)],["Dorsal",num(primary.shirt_number)],["Pierna hábil",data.preferred_foot||missing],["Estatura",data.height||missing],["Peso",data.weight||missing],["Fecha de nacimiento",data.birth_date||missing]];
    const grid=el("div","player-overview");fields.forEach(([label,value])=>{const card=el("div","player-field");card.append(el("small","",label),el("strong","",value));grid.append(card);});root.append(grid);
    addHeading(root,"Estadísticas por competición y temporada","Se muestran valores publicados; un guion significa que la fuente no informó ese dato.");
    const stats=data.statistics||[];const rows=stats.map(s=>[`${s.competition_name} · ${s.season_label}`,num(s.appearances),num(s.starts),num(s.substitute_appearances),num(s.minutes_played),num(s.goals),num(s.assists),num(s.yellow_cards),num(s.red_cards),num(s.shots_total),num(s.shots_on_target),num(s.goals_conceded),num(s.clean_sheets)]);
    appendTable(root,["Competición · temporada","PJ","Titular","Suplente","Minutos","Goles","Asistencias","Amarillas","Rojas","Remates","Al arco","Goles recibidos","Vallas invictas"],rows,{minWidth:1380});
    if(!stats.length)root.append(empty("No hay estadísticas individuales publicadas para este jugador."));
    app.replaceChildren(root);
  }
  function renderSearch(items, term) {
    const root=el("div");const head=el("div","page-heading");const copy=el("div");copy.append(el("div","eyebrow","Búsqueda global"),el("h1","",`Resultados para “${term}”`),el("p","",`${items.length} resultados vinculados a las competiciones cargadas`));head.append(copy);root.append(head);
    if(!items.length){root.append(empty("No se encontraron equipos o jugadores con ese nombre."));app.replaceChildren(root);return;}
    const results=el("div","search-results");items.forEach(item=>{const card=el("article","panel content-card search-result");const heading=el("div","competition-title");if(item.kind==="Equipo")heading.append(crest(item.crest_url,item.name,"large"));const info=el("div");info.append(el("div","small-caps",item.kind),linkButton(item.name,()=>navigate(item.kind==="Equipo"?`/team/${item.entity_id}`:`/player/${item.entity_id}`),"player-link"),el("p","muted",[item.country_or_nationality,item.leagues_or_teams].filter(Boolean).join(" · ")));heading.append(info);card.append(heading);
      if(item.team_links?.length){const links=el("div","search-team-links");item.team_links.forEach(([id,name,league,crestUrl])=>{const t=teamLink({id,name,crest:crestUrl},"team-link");t.append(el("small","",` · ${league}`));links.append(t);});card.append(links);}results.append(card);});root.append(results);app.replaceChildren(root);
  }
  searchForm.addEventListener("submit",event=>{event.preventDefault();const q=searchInput.value.trim();if(!q){searchInput.focus();return;}navigate(`/search?q=${encodeURIComponent(q)}`);});
  brandPreviewButton?.addEventListener("click", () => brandPreviewDialog?.showModal());
  brandPreviewClose?.addEventListener("click", () => brandPreviewDialog?.close());
  brandPreviewDialog?.addEventListener("click", event => {
    if (event.target === brandPreviewDialog) brandPreviewDialog.close();
  });
  window.addEventListener("hashchange",updateRouteFromHash);
  if(!location.hash) history.replaceState(null,"", "#/home");
  updateRouteFromHash();
})();
