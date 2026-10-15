"""Dashboard and calendar views using only the local football database."""

import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QSize, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkDiskCache,
    QNetworkReply,
    QNetworkRequest,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from data90.config.featured_match_priority import (
    FEATURED_COMPETITION_PRIORITY,
    featured_match_key,
    normalize_team_name,
)
from data90.config.settings import get_settings
from data90.services.competition_service import CompetitionService
from data90.services.featured_match_policy import featured_match_policy
from data90.services.match_service import MatchService
from data90.ui.match_labels import match_clock_label

COMPETITION_LOGO_URLS = {
    "PL": "https://crests.football-data.org/PL.png",
    "BUNDESLIGA": "https://crests.football-data.org/BL1.png",
    "EREDIVISIE": (
        "https://eredivisie.b-cdn.net/production/pages/"
        "VriendenLoterij_Eredivisie_LOGO_HOR_DIAP.png?height=240&quality=85"
    ),
    "LALIGA": "https://crests.football-data.org/PD.png",
    "LIGUE_1": "https://crests.football-data.org/FL1.png",
    "SERIE_A": "https://crests.football-data.org/SA.png",
    "UCL": "https://crests.football-data.org/CL.png",
    "UEL": "https://crests.football-data.org/EL.png",
}

COMPETITION_ORDER = ("PL", "BUNDESLIGA", "EREDIVISIE", "LALIGA", "LIGUE_1", "SERIE_A")

def match_stage_label(stage: str | None, matchday: int | None, fallback: str = "—") -> str:
    """Return a readable matchday label without exposing generic phase codes."""
    if stage:
        label = " ".join(str(stage).replace("_", " ").split())
        normalized = label.casefold()
        if normalized == "regular" + " " + "season":
            return f"Jornada {matchday}" if matchday else fallback
        return label
    return f"Jornada {matchday}" if matchday else fallback


class CrestLoader:
    """Load verified catalog crests asynchronously and keep an on-disk cache."""

    VERIFIED_CREST_OVERRIDES = {
        # LaLiga clubs: use the stable crest endpoints for the source IDs stored
        # in the local catalog, instead of small UEFA/Google preview images.
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
        # Bundesliga, including the URL already reported as broken by the app.
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
        # The source stored embedded data URIs for these two teams; replace them
        # with stable, verifiable crest files.
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

    def __init__(self, owner: QWidget) -> None:
        self.manager = QNetworkAccessManager(owner)
        cache = QNetworkDiskCache(self.manager)
        cache_path = (
            Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
            / "team-crests"
        )
        try:
            cache_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            # The cache is optional: locked-down Windows profiles must still be
            # able to open the dashboard and load crests in memory.
            cache = None
        if cache is not None:
            cache.setCacheDirectory(str(cache_path))
            cache.setMaximumCacheSize(64 * 1024 * 1024)
            self.manager.setCache(cache)
        self.pixmaps: dict[str, QPixmap] = {}
        self.pending: dict[str, list[QPushButton]] = {}
        self.crest_issues: set[str] = set()

    @staticmethod
    def _normalized_team_name(team_name: str) -> str:
        decomposed = unicodedata.normalize("NFKD", team_name.casefold())
        without_marks = "".join(
            character for character in decomposed if not unicodedata.combining(character)
        )
        return " ".join(without_marks.split())

    @classmethod
    def _verified_url(cls, url: str | None, team_name: str) -> str | None:
        return cls.VERIFIED_CREST_OVERRIDES.get(cls._normalized_team_name(team_name), url)

    def apply(self, button: QPushButton, url: str | None, team_name: str) -> None:
        size = button.property("crestSize") or (42 if button.objectName() == "heroClub" else 22)
        button.setIconSize(QSize(size, size))
        placeholder = QPixmap(size, size)
        placeholder.fill(Qt.GlobalColor.transparent)
        painter = QPainter(placeholder)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#587184"))
        painter.setBrush(QColor("#112432"))
        inset = max(1, size // 10)
        painter.drawEllipse(inset, inset, size - inset * 2, size - inset * 2)
        painter.setPen(QColor("#8299a9"))
        painter.drawPoint(size // 2, size // 2)
        painter.end()
        button.setIcon(QIcon(placeholder))
        url = self._verified_url(url, team_name)
        if not url or not url.startswith("https://"):
            button.setToolTip("Escudo no disponible · " + team_name)
            self._record_crest_issue(team_name, "No hay una URL asociada en los datos locales.")
            return
        cached = self.pixmaps.get(url)
        if cached is not None:
            button.setIcon(QIcon(cached))
            return
        waiters = self.pending.get(url)
        if waiters is not None:
            waiters.append((button, None))
            return
        self.pending[url] = [button]
        reply = self.manager.get(QNetworkRequest(QUrl(url)))
        reply.finished.connect(
            lambda request=reply, image_url=url: self._finish(request, image_url)
        )

    def _finish(self, reply: QNetworkReply, url: str) -> None:
        raw = bytes(reply.readAll())
        pixmap = QPixmap()
        if reply.error() == QNetworkReply.NetworkError.NoError and pixmap.loadFromData(raw):
            if pixmap.width() <= 1024 and pixmap.height() <= 1024:
                self.pixmaps[url] = pixmap
                for button in self.pending.get(url, []):
                    try:
                        button.setIcon(QIcon(pixmap))
                    except RuntimeError:
                        pass
        else:
            self._record_crest_issue(url, "La URL no respondió con una imagen válida.")
        self.pending.pop(url, None)
        reply.deleteLater()

    def _record_crest_issue(self, item: str, reason: str) -> None:
        self.crest_issues.add(f"{item}: {reason}")
        app_data = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        )
        try:
            app_data.mkdir(parents=True, exist_ok=True)
            report = app_data / "team-crest-review.txt"
            report.write_text("\n".join(sorted(self.crest_issues)) + "\n", encoding="utf-8")
        except OSError:
            # A neutral placeholder remains visible if the local issue log cannot be written.
            pass


class BlueprintSurface(QWidget):
    """Subtle tactical grid behind the mockup's cards and panels."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor(105, 201, 223, 10))
        for x in range(0, self.width(), 24):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 24):
            painter.drawLine(0, y, self.width(), y)


class CalendarSpotlight(QFrame):
    """A daily fixture card with foreground team crests only."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("calendarSpotlight")


class HomePage(BlueprintSurface):
    """Two-column dashboard matching the chosen Pizarra + Blueprint mockup."""

    team_selected = Signal(int)

    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.setObjectName("dashboardPage")
        self.session_factory = session_factory
        self.timezone = ZoneInfo(get_settings().app_timezone)
        self._competitions_loaded = False
        self._crest_urls: dict[int, str | None] = {}
        self.crest_loader = CrestLoader(self)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        page_head = QHBoxLayout()
        title_group = QVBoxLayout()
        self.date_kicker = QLabel("", objectName="pageKicker")
        title_group.addWidget(self.date_kicker)
        title_group.addWidget(QLabel("La jornada, en contexto", objectName="dashboardTitle"))
        title_group.addWidget(
            QLabel("Partidos destacados y forma reciente, en una sola mirada.", objectName="muted")
        )
        page_head.addLayout(title_group)
        page_head.addStretch(1)
        self.date_edit = QDateEdit()
        self.date_edit.setObjectName("dateFilter")
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("ddd · dd MMM yyyy")
        self.date_edit.setDate(datetime.now(self.timezone).date())
        self.date_edit.dateChanged.connect(self.refresh)
        page_head.addWidget(self.date_edit)
        root.addLayout(page_head)

        body = QGridLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setHorizontalSpacing(12)
        body.setVerticalSpacing(12)
        body.setColumnStretch(0, 15)
        body.setColumnStretch(1, 8)
        root.addLayout(body, 1)

        self.left_column = QVBoxLayout()
        self.left_column.setSpacing(12)
        body.addLayout(self.left_column, 0, 0)
        self.right_column = QVBoxLayout()
        self.right_column.setSpacing(12)
        body.addLayout(self.right_column, 0, 1)

        self.featured_card = QFrame(objectName="matchHero")
        featured_layout = QVBoxLayout(self.featured_card)
        featured_layout.setContentsMargins(19, 15, 19, 15)
        featured_layout.setSpacing(12)
        hero_top = QHBoxLayout()
        self.featured_competition = QLabel("ENCUENTRO DESTACADO", objectName="heroEyebrow")
        hero_top.addWidget(self.featured_competition)
        hero_top.addStretch(1)
        self.featured_status = QLabel("", objectName="statusBadge")
        hero_top.addWidget(self.featured_status)
        featured_layout.addLayout(hero_top)
        hero_match = QHBoxLayout()
        hero_match.setSpacing(8)
        self.featured_home = self._club_button("—", 0, large=True)
        self.featured_home.setObjectName("heroClub")
        self.featured_home.clicked.connect(lambda: self._emit_featured_team(self.featured_home))
        hero_match.addWidget(self.featured_home, 1)
        center = QVBoxLayout()
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.featured_time = QLabel("—:—", objectName="heroTime")
        self.featured_stage = QLabel("", objectName="heroStage")
        self.featured_score = QLabel("", objectName="heroScore")
        center.addWidget(self.featured_time, alignment=Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.featured_score, alignment=Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.featured_stage, alignment=Qt.AlignmentFlag.AlignCenter)
        hero_match.addLayout(center)
        self.featured_away = self._club_button("—", 0, large=True)
        self.featured_away.setObjectName("heroClub")
        self.featured_away.clicked.connect(lambda: self._emit_featured_team(self.featured_away))
        hero_match.addWidget(self.featured_away, 1)
        featured_layout.addLayout(hero_match)
        self.featured_note = QLabel("Datos de la agenda local", objectName="heroFooter")
        featured_layout.addWidget(self.featured_note)
        self.left_column.addWidget(self.featured_card)

        self.standings_card = QFrame(objectName="mockCard")
        standings_layout = QVBoxLayout(self.standings_card)
        standings_layout.setContentsMargins(0, 0, 0, 0)
        standings_head = QHBoxLayout()
        standings_head.setContentsMargins(13, 10, 13, 9)
        self.standings_title = QLabel("Posiciones", objectName="cardTitle")
        standings_head.addWidget(self.standings_title)
        standings_head.addStretch(1)
        self.standings_filter = QComboBox()
        self.standings_filter.setObjectName("compactFilter")
        self.standings_filter.currentIndexChanged.connect(self._refresh_standings)
        standings_head.addWidget(self.standings_filter)
        standings_layout.addLayout(standings_head)
        self.standings_table = QTableWidget(0, 7)
        self.standings_table.setObjectName("blueprintTable")
        self.standings_table.setHorizontalHeaderLabels(["#", "Club", "Pts", "PJ", "GF", "GC", "DG"])
        self.standings_table.horizontalHeader().setStretchLastSection(False)
        for column in range(7):
            self.standings_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.standings_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.standings_table.verticalHeader().setVisible(False)
        self.standings_table.verticalHeader().setDefaultSectionSize(38)
        self.standings_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.standings_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.standings_table.setShowGrid(False)
        self.standings_table.itemClicked.connect(self._open_standing_team)
        standings_layout.addWidget(self.standings_table)
        self.left_column.addWidget(self.standings_card, 1)

        self.agenda_card = QFrame(objectName="mockCard")
        agenda_layout = QVBoxLayout(self.agenda_card)
        agenda_layout.setContentsMargins(0, 0, 0, 0)
        agenda_head = QHBoxLayout()
        agenda_head.setContentsMargins(13, 10, 13, 9)
        agenda_head.addWidget(QLabel("Agenda de hoy", objectName="cardTitle"))
        agenda_head.addStretch(1)
        self.agenda_count = QLabel("0 partidos", objectName="cardMeta")
        agenda_head.addWidget(self.agenda_count)
        agenda_layout.addLayout(agenda_head)
        self.agenda_items = QVBoxLayout()
        self.agenda_items.setContentsMargins(0, 0, 0, 0)
        self.agenda_items.setSpacing(0)
        agenda_layout.addLayout(self.agenda_items)
        self.right_column.addWidget(self.agenda_card)

        self.week_card = QFrame(objectName="mockCard")
        week_layout = QVBoxLayout(self.week_card)
        week_layout.setContentsMargins(12, 10, 12, 12)
        week_head = QHBoxLayout()
        week_head.addWidget(QLabel("Resumen semanal", objectName="cardTitle"))
        week_head.addStretch(1)
        self.week_label = QLabel("", objectName="weekBadge")
        week_head.addWidget(self.week_label)
        week_layout.addLayout(week_head)
        stats = QHBoxLayout()
        stats.setSpacing(7)
        self.week_matches_value, self.week_competitions_value, self.week_live_value = (
            QLabel("—", objectName="statValue"),
            QLabel("—", objectName="statValue"),
            QLabel("—", objectName="statValue"),
        )
        for value, title in (
            (self.week_matches_value, "partidos"),
            (self.week_competitions_value, "competiciones"),
            (self.week_live_value, "en vivo"),
        ):
            tile = QFrame(objectName="statTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(9, 8, 9, 8)
            tile_layout.addWidget(value)
            tile_layout.addWidget(QLabel(title, objectName="statCaption"))
            stats.addWidget(tile, 1)
        week_layout.addLayout(stats)
        self.right_column.addWidget(self.week_card)
        self.right_column.addStretch(1)

    def refresh(self, *_args) -> None:
        selected_date = self.date_edit.date().toPython()
        try:
            with self.session_factory() as session:
                match_service = MatchService(session)
                matches = match_service.get_matches(selected_date)
                week_start = selected_date - timedelta(days=selected_date.weekday())
                weekly = [
                    match
                    for offset in range(7)
                    for match in match_service.get_matches(week_start + timedelta(days=offset))
                ]
                competitions = CompetitionService(session).get_competitions()
                standings, competition_name = self._load_preview_standings(session, competitions)
        except SQLAlchemyError as error:
            self._show_database_error(error)
            return

        self._crest_urls = {}
        for match in [*matches, *weekly]:
            self._crest_urls[match.home_team_id] = match.home_crest_url
            self._crest_urls[match.away_team_id] = match.away_crest_url
        for standing in standings:
            self._crest_urls[standing.team_id] = standing.crest_url
        self._set_kicker(selected_date)
        self._load_competition_filter(competitions)
        self._render_featured(matches)
        self._render_agenda(matches)
        self._render_standings(standings, competition_name)
        self._render_weekly_summary(weekly, week_start)

    def _load_preview_standings(self, session, competitions):
        service = CompetitionService(session)
        selected_code = self.standings_filter.currentData() if self._competitions_loaded else None
        candidates = competitions
        if selected_code:
            candidates = [item for item in competitions if item.code == selected_code]
        for competition in candidates:
            seasons = service.get_seasons(competition.code)
            if seasons:
                standings = service.get_standings(seasons[0].competition_season_id)
                if standings:
                    return standings, competition.name
        return [], "Tabla de posiciones"

    def _load_competition_filter(self, competitions) -> None:
        if self._competitions_loaded:
            return
        self.standings_filter.blockSignals(True)
        for competition in competitions:
            if competition.latest_season:
                self.standings_filter.addItem(competition.name, competition.code)
        self.standings_filter.blockSignals(False)
        self._competitions_loaded = True

    def _refresh_standings(self, _index: int = -1) -> None:
        try:
            with self.session_factory() as session:
                competitions = CompetitionService(session).get_competitions()
                standings, competition_name = self._load_preview_standings(session, competitions)
        except SQLAlchemyError as error:
            self._show_database_error(error)
            return
        self._render_standings(standings, competition_name)

    def _set_kicker(self, selected_date: date) -> None:
        weekdays = ("LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO")
        months = (
            "ENE",
            "FEB",
            "MAR",
            "ABR",
            "MAY",
            "JUN",
            "JUL",
            "AGO",
            "SEP",
            "OCT",
            "NOV",
            "DIC",
        )
        weekday = weekdays[selected_date.weekday()]
        month = months[selected_date.month - 1]
        self.date_kicker.setText(f"{weekday} · {selected_date.day} {month}")

    def _render_featured(self, matches) -> None:
        if not matches:
            self.featured_competition.setText("AGENDA LOCAL · SIN PARTIDOS PARA ESTA FECHA")
            self.featured_status.setText("SIN PARTIDOS")
            self.featured_home.setText("Elegí otra fecha")
            self.featured_home.setProperty("teamId", None)
            self.featured_home.setEnabled(False)
            self.crest_loader.apply(self.featured_home, None, "Elegí otra fecha")
            self.featured_away.setText("")
            self.featured_away.setProperty("teamId", None)
            self.featured_away.setEnabled(False)
            self.crest_loader.apply(self.featured_away, None, "")
            self.featured_time.setText("—:—")
            self.featured_score.setText("—")
            self.featured_stage.setText("")
            self.featured_note.setText("No se muestran cruces de demostración.")
            return
        selected = next(
            (match for match in matches if match.status in {"LIVE", "PAUSED"}),
            next((match for match in matches if match.status == "SCHEDULED"), matches[0]),
        )
        local_kickoff = selected.kickoff_utc.astimezone(self.timezone)
        stage = match_stage_label(selected.stage, selected.matchday, selected.season_label)
        score = (
            f"{selected.home_score_ft} – {selected.away_score_ft}"
            if selected.home_score_ft is not None and selected.away_score_ft is not None
            else ""
        )
        self.featured_competition.setText(
            f"ENCUENTRO DESTACADO · {selected.competition_name.upper()}"
        )
        self.featured_status.setText(self._status_label(selected.status).upper())
        self.featured_status.setProperty("live", selected.status in {"LIVE", "PAUSED"})
        self.featured_status.style().unpolish(self.featured_status)
        self.featured_status.style().polish(self.featured_status)
        self.featured_home.setText(selected.home_team)
        self.featured_home.setProperty("teamId", selected.home_team_id)
        self.featured_home.setEnabled(True)
        self.crest_loader.apply(
            self.featured_home, self._crest_urls.get(selected.home_team_id), selected.home_team
        )
        self.featured_away.setText(selected.away_team)
        self.featured_away.setProperty("teamId", selected.away_team_id)
        self.featured_away.setEnabled(True)
        self.crest_loader.apply(
            self.featured_away, self._crest_urls.get(selected.away_team_id), selected.away_team
        )
        self.featured_time.setText(
            match_clock_label(selected.kickoff_utc, selected.status, self.timezone)
        )
        self.featured_score.setText(score)
        self.featured_stage.setText(f"{stage.upper()} · {local_kickoff.strftime('%d/%m')}")
        self.featured_note.setText("Seleccioná un equipo para abrir su ficha.")

    def _render_agenda(self, matches) -> None:
        self._clear_layout(self.agenda_items)
        self.agenda_count.setText(f"{len(matches)} partidos")
        if not matches:
            self.agenda_items.addWidget(
                QLabel("No hay partidos registrados para esta fecha.", objectName="emptyHint")
            )
            return
        for match in matches[:2]:
            row = QFrame(objectName="fixtureRow")
            grid = QGridLayout(row)
            grid.setContentsMargins(12, 9, 12, 9)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(4)
            time = QLabel(
                match_clock_label(match.kickoff_utc, match.status, self.timezone),
                objectName="fixtureTime",
            )
            grid.addWidget(time, 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(self._club_button(match.home_team, match.home_team_id), 0, 1)
            score = self._score_label(match)
            grid.addWidget(score, 0, 2, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(self._club_button(match.away_team, match.away_team_id), 0, 3)
            detail = QLabel(f"{match.competition_name} · {self._status_label(match.status)}")
            detail.setObjectName("fixtureMeta")
            grid.addWidget(detail, 1, 1, 1, 3)
            self.agenda_items.addWidget(row)

    def _render_standings(self, standings, competition_name: str) -> None:
        self.standings_title.setText(f"{competition_name} · posiciones")
        table = self.standings_table
        table.setRowCount(0)
        for standing in standings[:3]:
            row = table.rowCount()
            table.insertRow(row)
            values = [
                standing.position,
                standing.team_name,
                standing.points,
                standing.played,
                standing.goals_for,
                standing.goals_against,
                standing.goal_difference,
            ]
            for column, value in enumerate(values):
                if column == 1:
                    button = self._club_button(
                        standing.team_name,
                        standing.team_id,
                        crest_url=standing.crest_url,
                    )
                    table.setCellWidget(row, column, button)
                    continue
                item = QTableWidgetItem(str(value))
                if column != 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 2:
                    item.setForeground(QColor("#effbff"))
                    item.setBackground(QColor("#143042"))
                    font = item.font()
                    font.setBold(True)
                    font.setPointSize(font.pointSize() + 1)
                    item.setFont(font)
                elif column == 4:
                    item.setForeground(QColor("#54d6a0"))
                elif column == 5:
                    item.setForeground(QColor("#ff777c"))
                elif column == 6:
                    color = "#54d6a0" if value > 0 else "#ff777c" if value < 0 else "#edf4f8"
                    item.setForeground(QColor(color))
                table.setItem(row, column, item)
        if not standings:
            self._show_empty_table("No hay tabla de posiciones disponible.")

    def _render_weekly_summary(self, matches, week_start: date) -> None:
        competitions = {match.competition_code for match in matches}
        live = sum(match.status in {"LIVE", "PAUSED"} for match in matches)
        self.week_label.setText(
            f"{week_start.day:02d}–{(week_start + timedelta(days=6)).day:02d} "
            f"{week_start.strftime('%b').upper()}"
        )
        self.week_matches_value.setText(str(len(matches)))
        self.week_competitions_value.setText(f"{len(competitions):02d}")
        self.week_live_value.setText(f"{live:02d}")

    def _show_database_error(self, error: Exception) -> None:
        self._clear_layout(self.agenda_items)
        self.agenda_items.addWidget(
            QLabel(f"No se pudo consultar PostgreSQL ({error.__class__.__name__}).")
        )
        self.agenda_count.setText("Base local no disponible")
        self.featured_competition.setText("DATOS LOCALES NO DISPONIBLES")
        self._show_empty_table("Revisá PostgreSQL y volvé a cargar la vista.")

    def _emit_featured_team(self, button: QPushButton) -> None:
        team_id = button.property("teamId")
        if team_id is not None:
            self.team_selected.emit(int(team_id))

    def _show_empty_table(self, message: str) -> None:
        self.standings_table.setRowCount(1)
        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.standings_table.setItem(0, 0, item)
        self.standings_table.setSpan(0, 0, 1, self.standings_table.columnCount())

    def _open_standing_team(self, item: QTableWidgetItem) -> None:
        if item.column() == 1 and item.data(Qt.ItemDataRole.UserRole) is not None:
            self.team_selected.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _club_button(
        self,
        name: str,
        team_id: int,
        *,
        large: bool = False,
        crest_url: str | None = None,
    ) -> QPushButton:
        label = QPushButton(name)
        label.setObjectName("heroClub" if large else "clubLink")
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setToolTip("Abrir ficha del equipo" if team_id else "")
        self.crest_loader.apply(
            label, crest_url if crest_url is not None else self._crest_urls.get(team_id), name
        )
        if team_id:
            label.clicked.connect(
                lambda _checked=False, selected_id=team_id: self.team_selected.emit(selected_id)
            )
        else:
            label.setEnabled(False)
        return label

    @staticmethod
    def _score_label(match) -> QLabel:
        score = QLabel(
            f"{match.home_score_ft} – {match.away_score_ft}"
            if match.home_score_ft is not None and match.away_score_ft is not None
            else "— : —",
            objectName="fixtureScore",
        )
        return score

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _status_label(status: str) -> str:
        return {
            "SCHEDULED": "Próximo",
            "LIVE": "En vivo",
            "PAUSED": "Pausado",
            "FINISHED": "Finalizado",
            "POSTPONED": "Aplazado",
            "SUSPENDED": "Suspendido",
            "CANCELLED": "Cancelado",
            "UNKNOWN": "Sin confirmar",
        }.get(status, status)


class CompetitionDashboardPage(BlueprintSurface):
    """Competition-first dashboard matching modern-crest with database-backed content."""

    team_selected = Signal(int)
    competition_selected = Signal(str)

    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.setObjectName("competitionDashboard")
        self.session_factory = session_factory
        self.timezone = ZoneInfo(get_settings().app_timezone)
        self.crest_loader = CrestLoader(self)
        self.competitions = []
        self.active_code: str | None = None
        self._crest_urls: dict[int, str | None] = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(18)

        self.league_rail = QFrame(objectName="leagueRail")
        self.league_rail.setFixedWidth(190)
        rail_layout = QVBoxLayout(self.league_rail)
        rail_layout.setContentsMargins(8, 12, 8, 12)
        rail_layout.setSpacing(7)
        rail_layout.addWidget(QLabel("COMPETICIONES", objectName="railKicker"))
        self.season_label = QLabel("Temporada —", objectName="railSeason")
        rail_layout.addWidget(self.season_label)
        self.league_buttons_layout = QVBoxLayout()
        self.league_buttons_layout.setSpacing(5)
        rail_layout.addLayout(self.league_buttons_layout)
        rail_layout.addStretch(1)
        rail_layout.addWidget(
            QLabel(
                "Datos y horarios según la información cargada en la aplicación.",
                objectName="railNote",
            )
        )
        root.addWidget(self.league_rail)

        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setMinimumWidth(0)
        content = QWidget()
        content.setObjectName("competitionContent")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        self.content_scroll.setWidget(content)
        root.addWidget(self.content_scroll, 1)

        page_head = QHBoxLayout()
        title_group = QVBoxLayout()
        title_group.setSpacing(2)
        self.date_kicker = QLabel("", objectName="pageKicker")
        title_group.addWidget(self.date_kicker)
        self.dashboard_title = QLabel("Competiciones", objectName="dashboardTitle")
        title_group.addWidget(self.dashboard_title)
        self.competition_meta = QLabel("", objectName="competitionMeta")
        title_group.addWidget(self.competition_meta)
        page_head.addLayout(title_group, 1)
        self.open_competition_button = QPushButton("Ver competición  ↗")
        self.open_competition_button.setObjectName("quietAction")
        self.open_competition_button.clicked.connect(self._open_competition)
        page_head.addWidget(self.open_competition_button, alignment=Qt.AlignmentFlag.AlignBottom)
        column.addLayout(page_head)

        hero_row = QHBoxLayout()
        self.hero_row = hero_row
        hero_row.setSpacing(12)
        self.featured_card = QFrame(objectName="competitionHero")
        self.featured_card.setMinimumHeight(202)
        hero_layout = QVBoxLayout(self.featured_card)
        hero_layout.setContentsMargins(20, 15, 20, 14)
        hero_layout.setSpacing(10)
        hero_top = QHBoxLayout()
        self.featured_heading = QPushButton("PARTIDO DESTACADO")
        self.featured_heading.setObjectName("competitionWordmark")
        self.featured_heading.setProperty("crestSize", 34)
        self.featured_heading.setCursor(Qt.CursorShape.ArrowCursor)
        self.featured_heading.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hero_top.addWidget(self.featured_heading)
        hero_top.addStretch(1)
        self.featured_status = QLabel("", objectName="heroStatus")
        hero_top.addWidget(self.featured_status)
        hero_layout.addLayout(hero_top)

        duel = QHBoxLayout()
        duel.setSpacing(10)
        self.featured_home = self._club_button("—", 0, large=True)
        self.featured_home.clicked.connect(lambda: self._emit_featured_team(self.featured_home))
        duel.addWidget(self.featured_home, 1)
        center = QVBoxLayout()
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.featured_score = QLabel("VS", objectName="competitionScore")
        self.featured_time = QLabel("", objectName="competitionTime")
        self.featured_stage = QLabel("", objectName="competitionStage")
        center.addWidget(self.featured_score, alignment=Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.featured_time, alignment=Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.featured_stage, alignment=Qt.AlignmentFlag.AlignCenter)
        duel.addLayout(center)
        self.featured_away = self._club_button("—", 0, large=True)
        self.featured_away.clicked.connect(lambda: self._emit_featured_team(self.featured_away))
        duel.addWidget(self.featured_away, 1)
        hero_layout.addLayout(duel, 1)
        self.featured_note = QLabel("", objectName="heroFooter")
        hero_layout.addWidget(self.featured_note)
        hero_row.addWidget(self.featured_card, 3)

        self.next_card = QFrame(objectName="nextCard")
        self.next_card.setMinimumWidth(250)
        next_layout = QVBoxLayout(self.next_card)
        next_layout.setContentsMargins(16, 16, 16, 15)
        next_layout.setSpacing(11)
        next_head = QHBoxLayout()
        next_head.addWidget(QLabel("Lo que viene", objectName="cardTitle"))
        next_head.addStretch(1)
        self.next_hint = QLabel("PRÓXIMO", objectName="cardMeta")
        next_head.addWidget(self.next_hint)
        next_layout.addLayout(next_head)
        self.next_fixture = QVBoxLayout()
        self.next_fixture.setSpacing(8)
        next_layout.addLayout(self.next_fixture, 1)
        self.next_footer = QLabel("", objectName="nextFooter")
        self.next_footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_layout.addWidget(self.next_footer)
        hero_row.addWidget(self.next_card, 1)
        column.addLayout(hero_row)

        section_head = QHBoxLayout()
        section_copy = QVBoxLayout()
        section_copy.setSpacing(2)
        section_copy.addWidget(QLabel("PANORAMA DE LA COMPETICIÓN", objectName="railKicker"))
        section_copy.addWidget(QLabel("Posiciones y próximos cruces", objectName="sectionTitle"))
        section_head.addLayout(section_copy, 1)
        self.season_summary = QLabel("", objectName="seasonSummary")
        self.season_summary.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        section_head.addWidget(self.season_summary)
        column.addLayout(section_head)

        data_row = QHBoxLayout()
        self.data_row = data_row
        data_row.setSpacing(12)
        self.standings_card = QFrame(objectName="competitionCard")
        standings_layout = QVBoxLayout(self.standings_card)
        standings_layout.setContentsMargins(0, 0, 0, 0)
        standings_head = QHBoxLayout()
        standings_head.setContentsMargins(14, 11, 14, 9)
        standings_head.addWidget(QLabel("Tabla de posiciones", objectName="cardTitle"))
        standings_head.addStretch(1)
        self.table_caption = QLabel("", objectName="cardMeta")
        standings_head.addWidget(self.table_caption)
        standings_layout.addLayout(standings_head)
        self.standings_table = QTableWidget(0, 7)
        self.standings_table.setObjectName("leagueStandings")
        self.standings_table.setHorizontalHeaderLabels(["#", "Club", "Pts", "PJ", "GF", "GC", "DG"])
        self.standings_table.horizontalHeader().setStretchLastSection(False)
        self.standings_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.standings_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for index in range(2, 7):
            self.standings_table.horizontalHeader().setSectionResizeMode(
                index, QHeaderView.ResizeMode.ResizeToContents
            )
        self.standings_table.verticalHeader().setVisible(False)
        self.standings_table.verticalHeader().setDefaultSectionSize(35)
        self.standings_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.standings_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.standings_table.setShowGrid(False)
        self.standings_table.itemClicked.connect(self._open_standing_team)
        standings_layout.addWidget(self.standings_table, 1)
        data_row.addWidget(self.standings_card, 5)

        self.fixtures_card = QFrame(objectName="competitionCard")
        fixture_layout = QVBoxLayout(self.fixtures_card)
        fixture_layout.setContentsMargins(0, 0, 0, 0)
        fixture_head = QHBoxLayout()
        fixture_head.setContentsMargins(14, 11, 14, 9)
        fixture_head.addWidget(QLabel("Próximos partidos", objectName="cardTitle"))
        fixture_head.addStretch(1)
        self.fixture_caption = QLabel("", objectName="cardMeta")
        fixture_head.addWidget(self.fixture_caption)
        fixture_layout.addLayout(fixture_head)
        self.fixtures_list = QVBoxLayout()
        self.fixtures_list.setContentsMargins(12, 0, 12, 8)
        self.fixtures_list.setSpacing(0)
        fixture_layout.addLayout(self.fixtures_list, 1)
        data_row.addWidget(self.fixtures_card, 4)
        column.addLayout(data_row, 1)
        self._apply_responsive_layout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        compact = self.width() < 1200
        self.league_rail.setFixedWidth(156 if compact else 190)
        direction = (
            QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        )
        self.hero_row.setDirection(direction)
        self.data_row.setDirection(direction)

    def refresh(self, *_args) -> None:
        try:
            with self.session_factory() as session:
                service = CompetitionService(session)
                all_competitions = service.get_competitions()
                self.competitions = self._ordered_leagues(all_competitions)
                if not self.competitions:
                    self._show_empty_dashboard("No hay ligas activas con datos en la base local.")
                    return
                if self.active_code not in {item.code for item in self.competitions}:
                    self.active_code = self.competitions[0].code
                competition = next(
                    item for item in self.competitions if item.code == self.active_code
                )
                seasons = service.get_seasons(competition.code)
                season = seasons[0] if seasons else None
                matches = service.get_matches(season.competition_season_id) if season else []
                standings = service.get_standings(season.competition_season_id) if season else []
        except SQLAlchemyError as error:
            self._show_empty_dashboard(
                f"No se pudo consultar PostgreSQL ({error.__class__.__name__})."
            )
            return

        self._build_league_buttons()
        self._crest_urls = {
            team_id: crest_url
            for match in matches
            for team_id, crest_url in (
                (match.home_team_id, match.home_crest_url),
                (match.away_team_id, match.away_crest_url),
            )
        }
        self._crest_urls.update({standing.team_id: standing.crest_url for standing in standings})
        now = datetime.now(UTC)
        self._render_competition_header(competition, season)
        self._render_featured(matches, now)
        self._render_next_match(matches, now)
        self._render_standings(standings)
        self._render_upcoming(matches, now)

    @staticmethod
    def _ordered_leagues(competitions):
        known = {code: index for index, code in enumerate(COMPETITION_ORDER)}
        leagues = [
            item
            for item in competitions
            if item.latest_season and (item.competition_type or "").upper() == "LEAGUE"
        ]
        return sorted(
            leagues, key=lambda item: (known.get(item.code, len(known)), item.name.casefold())
        )

    def _build_league_buttons(self) -> None:
        while self.league_buttons_layout.count():
            item = self.league_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for competition in self.competitions:
            button = QPushButton(competition.name)
            button.setObjectName("leagueButton")
            button.setProperty("competitionCode", competition.code)
            button.setProperty("crestSize", 30)
            button.setCheckable(True)
            button.setChecked(competition.code == self.active_code)
            button.setToolTip(f"Abrir {competition.name}")
            self.crest_loader.apply(
                button, COMPETITION_LOGO_URLS.get(competition.code), competition.name
            )
            button.clicked.connect(
                lambda _checked=False, code=competition.code: self._select_competition(code)
            )
            self.league_buttons_layout.addWidget(button)

    def _select_competition(self, code: str) -> None:
        if code == self.active_code:
            return
        self.active_code = code
        self.refresh()

    def _render_competition_header(self, competition, season) -> None:
        today = datetime.now(self.timezone)
        weekdays = ("LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO")
        months = (
            "ENE",
            "FEB",
            "MAR",
            "ABR",
            "MAY",
            "JUN",
            "JUL",
            "AGO",
            "SEP",
            "OCT",
            "NOV",
            "DIC",
        )
        self.date_kicker.setText(
            f"{weekdays[today.weekday()]} · {today.day} {months[today.month - 1]}"
        )
        self.dashboard_title.setText(competition.name)
        self.featured_heading.setText(competition.name.upper())
        self.crest_loader.apply(
            self.featured_heading, COMPETITION_LOGO_URLS.get(competition.code), competition.name
        )
        if season is None:
            self.season_label.setText("Sin temporada cargada")
            self.competition_meta.setText("No hay una temporada disponible para esta competición.")
            self.season_summary.setText("")
            self.table_caption.setText("Sin temporada")
            return
        self.season_label.setText(f"Temporada {season.label}")
        synced = (
            season.last_synced_at.astimezone(self.timezone).strftime(
                "Última actualización · %d/%m/%Y %H:%M"
            )
            if season.last_synced_at
            else "Sin sincronización registrada"
        )
        region = competition.country_name or competition.region_name or "Competición"
        self.competition_meta.setText(f"{season.label} · {region}")
        self.season_summary.setText(synced)
        self.table_caption.setText(f"{season.team_count} equipos · {season.label}")

    def _render_featured(self, matches, now: datetime) -> None:
        candidates = [match for match in matches if match.status not in {"CANCELLED", "UNKNOWN"}]
        live = [match for match in candidates if match.status in {"LIVE", "PAUSED"}]
        upcoming = [
            match
            for match in candidates
            if match.kickoff_utc >= now and match.status in {"SCHEDULED", "POSTPONED"}
        ]
        today = now.astimezone(self.timezone).date()
        upcoming_today = [
            match
            for match in upcoming
            if match.kickoff_utc.astimezone(self.timezone).date() == today
        ]
        played_today = [
            match
            for match in candidates
            if match.kickoff_utc < now
            and match.kickoff_utc.astimezone(self.timezone).date() == today
        ]
        played = [match for match in candidates if match.kickoff_utc < now]
        selected = next(iter(live or upcoming_today or upcoming or played_today or played), None)
        if selected is None:
            self._set_empty_match("No hay partidos disponibles para esta temporada.")
            return
        self.featured_home.setText(selected.home_team)
        self.featured_home.setProperty("teamId", selected.home_team_id)
        self.featured_home.setEnabled(True)
        self.crest_loader.apply(self.featured_home, selected.home_crest_url, selected.home_team)
        self.featured_away.setText(selected.away_team)
        self.featured_away.setProperty("teamId", selected.away_team_id)
        self.featured_away.setEnabled(True)
        self.crest_loader.apply(self.featured_away, selected.away_crest_url, selected.away_team)
        has_score = selected.home_score_ft is not None and selected.away_score_ft is not None
        self.featured_score.setText(
            f"{selected.home_score_ft}  —  {selected.away_score_ft}" if has_score else "VS"
        )
        local_time = selected.kickoff_utc.astimezone(self.timezone)
        self.featured_time.setText(
            match_clock_label(selected.kickoff_utc, selected.status, self.timezone).upper()
        )
        stage = match_stage_label(selected.stage, selected.matchday, selected.season_label)
        self.featured_stage.setText(f"{stage} · {local_time.strftime('%d/%m/%Y')}")
        self.featured_status.setText(self._status_label(selected.status).upper())
        self.featured_status.setProperty("live", selected.status in {"LIVE", "PAUSED"})
        self.featured_status.style().unpolish(self.featured_status)
        self.featured_status.style().polish(self.featured_status)
        self.featured_note.setText("Seleccioná un escudo o nombre para abrir la ficha del equipo.")

    def _render_next_match(self, matches, now: datetime) -> None:
        self._clear_layout(self.next_fixture)
        upcoming = self._upcoming(matches, now)
        if not upcoming:
            self.next_fixture.addWidget(
                QLabel("No hay próximos partidos confirmados.", objectName="emptyHint")
            )
            self.next_footer.setText("")
            self.next_hint.setText("CALENDARIO")
            return
        match = upcoming[0]
        for team_name, team_id, crest_url in (
            (match.home_team, match.home_team_id, match.home_crest_url),
            (match.away_team, match.away_team_id, match.away_crest_url),
        ):
            button = self._club_button(team_name, team_id, crest_url=crest_url)
            button.setObjectName("nextClub")
            self.next_fixture.addWidget(button)
        self.next_hint.setText("PRÓXIMO")
        local_time = match.kickoff_utc.astimezone(self.timezone)
        stage = match_stage_label(match.stage, match.matchday, "Fecha por confirmar")
        clock = match_clock_label(match.kickoff_utc, match.status, self.timezone)
        self.next_footer.setText(f"{local_time.strftime('%a %d %b')} · {clock}  ·  {stage}")

    def _render_standings(self, standings) -> None:
        table = self.standings_table
        table.clearSpans()
        table.setRowCount(0)
        for standing in standings:
            row = table.rowCount()
            table.insertRow(row)
            values = (
                standing.position,
                standing.team_name,
                standing.points,
                standing.played,
                standing.goals_for,
                standing.goals_against,
                standing.goal_difference,
            )
            for column, value in enumerate(values):
                if column == 1:
                    button = self._club_button(
                        standing.team_name, standing.team_id, crest_url=standing.crest_url
                    )
                    table.setCellWidget(row, column, button)
                    continue
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 2:
                    item.setForeground(QColor("#edf5f8"))
                    item.setBackground(QColor("#173548"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                elif column == 4:
                    item.setForeground(QColor("#70d8a5"))
                elif column == 5:
                    item.setForeground(QColor("#ff847c"))
                elif column == 6:
                    item.setForeground(
                        QColor("#70d8a5" if value > 0 else "#ff847c" if value < 0 else "#edf5f8")
                    )
                table.setItem(row, column, item)
        if not standings:
            self._show_empty_table("No hay una tabla publicada para esta temporada.")

    def _render_upcoming(self, matches, now: datetime) -> None:
        self._clear_layout(self.fixtures_list)
        upcoming = self._upcoming(matches, now)[:5]
        self.fixture_caption.setText(
            f"{len(upcoming)} partidos" if upcoming else "Sin fechas confirmadas"
        )
        if not upcoming:
            self.fixtures_list.addWidget(
                QLabel(
                    "No hay próximos cruces confirmados en la base local.", objectName="emptyHint"
                )
            )
            return
        for match in upcoming:
            row = QFrame(objectName="upcomingFixture")
            grid = QHBoxLayout(row)
            grid.setContentsMargins(8, 9, 8, 9)
            grid.setSpacing(7)
            local_time = match.kickoff_utc.astimezone(self.timezone)
            date_text = QLabel(local_time.strftime("%d %b"), objectName="upcomingDate")
            grid.addWidget(date_text)
            home = self._club_button(
                match.home_team, match.home_team_id, crest_url=match.home_crest_url
            )
            away = self._club_button(
                match.away_team, match.away_team_id, crest_url=match.away_crest_url
            )
            grid.addWidget(home, 1)
            grid.addWidget(QLabel("vs", objectName="upcomingVersus"))
            grid.addWidget(away, 1)
            time = QLabel(local_time.strftime("%H:%M"), objectName="upcomingTime")
            grid.addWidget(time)
            self.fixtures_list.addWidget(row)

    @staticmethod
    def _upcoming(matches, now: datetime):
        return sorted(
            (
                match
                for match in matches
                if match.kickoff_utc >= now and match.status in {"SCHEDULED", "POSTPONED"}
            ),
            key=lambda match: match.kickoff_utc,
        )

    def _club_button(
        self, name: str, team_id: int, *, large: bool = False, crest_url: str | None = None
    ) -> QPushButton:
        button = QPushButton(name)
        button.setObjectName("heroClub" if large else "clubLink")
        button.setProperty("teamId", team_id)
        button.setProperty("crestSize", 46 if large else 21)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(f"Abrir ficha de {name}")
        self.crest_loader.apply(
            button, crest_url if crest_url is not None else self._crest_urls.get(team_id), name
        )
        if team_id:
            button.clicked.connect(
                lambda _checked=False, selected_id=team_id: self.team_selected.emit(selected_id)
            )
        else:
            button.setEnabled(False)
        return button

    def _show_empty_dashboard(self, message: str) -> None:
        self.dashboard_title.setText("Data90")
        self.competition_meta.setText(message)
        self._set_empty_match(message)
        self._show_empty_table(message)
        self._clear_layout(self.fixtures_list)
        self.fixtures_list.addWidget(QLabel(message, objectName="emptyHint"))
        self.season_label.setText("Datos locales")
        self.season_summary.setText("")

    def _set_empty_match(self, message: str) -> None:
        self.featured_home.setText(message)
        self.featured_home.setProperty("teamId", None)
        self.featured_home.setEnabled(False)
        self.featured_away.setText("")
        self.featured_away.setProperty("teamId", None)
        self.featured_away.setEnabled(False)
        self.featured_score.setText("—")
        self.featured_time.setText("")
        self.featured_stage.setText("")
        self.featured_status.setText("SIN DATOS")
        self.featured_note.setText("No se muestran partidos inventados.")

    def _show_empty_table(self, message: str) -> None:
        self.standings_table.setRowCount(1)
        self.standings_table.setSpan(0, 0, 1, self.standings_table.columnCount())
        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.standings_table.setItem(0, 0, item)

    def _open_standing_team(self, item: QTableWidgetItem) -> None:
        if item.column() == 1 and item.data(Qt.ItemDataRole.UserRole) is not None:
            self.team_selected.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _open_competition(self) -> None:
        if self.active_code:
            self.competition_selected.emit(self.active_code)

    def _emit_featured_team(self, button: QPushButton) -> None:
        team_id = button.property("teamId")
        if team_id is not None:
            self.team_selected.emit(int(team_id))

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _status_label(status: str) -> str:
        return HomePage._status_label(status)


class CalendarPage(BlueprintSurface):
    """Home screen: a six-day horizontal calendar of loaded fixtures."""

    team_selected = Signal(int)
    competition_selected = Signal(str)

    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.setObjectName("calendarPage")
        self.session_factory = session_factory
        self.timezone = ZoneInfo(get_settings().app_timezone)
        self._crest_urls: dict[int, str | None] = {}
        self.competitions = []
        self._calendar_matches_by_day: dict[date, list] = {}
        self._current_positions: dict[str, dict[str, tuple[int, int]]] = {}
        self._featured_policy: dict = {}
        self._day_selectors: dict[date, QPushButton] = {}
        self._selected_fixture_day = datetime.now(self.timezone).date()
        self.crest_loader = CrestLoader(self)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        rail = QFrame(objectName="panel")
        rail.setFixedWidth(210)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(10, 12, 10, 12)
        rail_layout.addWidget(QLabel("COMPETICIONES", objectName="pageKicker"))
        self.competition_buttons = QVBoxLayout()
        self.competition_buttons.setSpacing(5)
        rail_layout.addLayout(self.competition_buttons)
        rail_layout.addStretch(1)
        root.addWidget(rail)
        content = QWidget()
        root.addWidget(content, 1)
        root = QVBoxLayout(content)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(10)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(QLabel("INICIO · CALENDARIO", objectName="pageKicker"))
        titles.addWidget(QLabel("Próximos seis días", objectName="dashboardTitle"))
        titles.addWidget(
            QLabel("Partidos reales de las competiciones cargadas.", objectName="muted")
        )
        head.addLayout(titles)
        head.addStretch(1)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("ddd · dd MMM yyyy")
        self.date_edit.setDate(datetime.now(self.timezone).date())
        self.date_edit.dateChanged.connect(self.refresh)
        head.addWidget(self.date_edit)
        root.addLayout(head)
        self.date_summary = QLabel("", objectName="calendarDateSummary")
        root.addWidget(self.date_summary)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content_layout = QHBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(9)
        self.content_layout.setSizeConstraint(QHBoxLayout.SizeConstraint.SetMinimumSize)
        self.scroll.setWidget(self.content)
        self.scroll.setMaximumHeight(315)
        root.addWidget(self.scroll, 0, Qt.AlignmentFlag.AlignTop)
        fixture_heading = QHBoxLayout()
        fixture_heading.addWidget(QLabel("FIXTURE DEL DÍA", objectName="calendarFixtureHeading"))
        fixture_heading.addStretch(1)
        self.fixture_date_label = QLabel("", objectName="calendarFixtureDate")
        fixture_heading.addWidget(self.fixture_date_label)
        root.addLayout(fixture_heading)
        self.fixture_scroll = QScrollArea()
        self.fixture_scroll.setWidgetResizable(True)
        self.fixture_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.fixture_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.fixture_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.fixture_content = QWidget()
        self.fixture_layout = QGridLayout(self.fixture_content)
        self.fixture_layout.setContentsMargins(0, 0, 0, 0)
        self.fixture_layout.setSpacing(8)
        self.fixture_layout.setSizeConstraint(QGridLayout.SizeConstraint.SetMinimumSize)
        for column in range(3):
            self.fixture_layout.setColumnStretch(column, 1)
        self.fixture_scroll.setWidget(self.fixture_content)
        self.fixture_scroll.setMinimumHeight(270)
        root.addWidget(self.fixture_scroll, 1)

    def refresh(self, *_args) -> None:
        selected = self.date_edit.date().toPython()
        days = [selected + timedelta(days=offset) for offset in range(6)]
        try:
            with self.session_factory() as session:
                service = MatchService(session)
                competition_service = CompetitionService(session)
                all_competitions = competition_service.get_competitions()
                self._featured_policy = featured_match_policy(session)
                self.competitions = [
                    competition
                    for competition in all_competitions
                    if competition.code in FEATURED_COMPETITION_PRIORITY
                    and competition.latest_season
                    and competition.match_count
                ]
                self._render_competition_rail()
                self._current_positions = {}
                for competition in all_competitions:
                    if (
                        competition.code not in FEATURED_COMPETITION_PRIORITY[2:]
                        or (competition.competition_type or "").upper() != "LEAGUE"
                    ):
                        continue
                    seasons = competition_service.get_seasons(competition.code)
                    if not seasons:
                        continue
                    standings = competition_service.get_standings(
                        seasons[0].competition_season_id
                    )
                    if standings:
                        self._current_positions[competition.code] = {
                            normalize_team_name(row.team_name): (row.position, len(standings))
                            for row in standings
                        }
                if not self.competitions:
                    self._show_empty_dashboard("No hay ligas activas con datos en la base local.")
                    return
                five_day_calendar = [
                    (calendar_day, service.get_matches(calendar_day)) for calendar_day in days
                ]
        except SQLAlchemyError as error:
            self.date_summary.setText(
                f"No se pudo consultar PostgreSQL ({error.__class__.__name__})."
            )
            return
        self._calendar_matches_by_day = dict(five_day_calendar)
        self._selected_fixture_day = selected
        self._crest_urls = {
            team_id: crest_url
            for _day, day_matches in five_day_calendar
            for match in day_matches
            for team_id, crest_url in (
                (match.home_team_id, match.home_crest_url),
                (match.away_team_id, match.away_crest_url),
            )
        }
        self._clear_content()
        match_total = sum(len(day_matches) for _day, day_matches in five_day_calendar)
        month_names = (
            "ENE",
            "FEB",
            "MAR",
            "ABR",
            "MAY",
            "JUN",
            "JUL",
            "AGO",
            "SEP",
            "OCT",
            "NOV",
            "DIC",
        )
        self.date_summary.setText(
            f"{days[0].day:02d} {month_names[days[0].month - 1]} — "
            f"{days[-1].day:02d} {month_names[days[-1].month - 1]} · "
            f"{match_total} PARTIDOS EN SEIS DÍAS"
        )
        self._day_selectors = {}
        for day, matches in five_day_calendar:
            card = QFrame(objectName="calendarDayCard")
            card.setMinimumWidth(202)
            card.setMaximumWidth(258)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(6, 6, 6, 6)
            card_layout.setSpacing(4)
            title = QPushButton(
                f"{self._weekday_name(day)}  {day.day:02d} · {len(matches)} PARTIDOS"
            )
            title.setObjectName("calendarDayActive" if day == selected else "calendarDaySelect")
            title.setCursor(Qt.CursorShape.PointingHandCursor)
            title.clicked.connect(
                lambda _checked=False, chosen=day: self._select_fixture_day(chosen)
            )
            self._day_selectors[day] = title
            card_layout.addWidget(title)
            featured = self._featured_match(matches)
            if featured is not None:
                spotlight = self._build_spotlight(featured)
                card_layout.addWidget(spotlight)
            card_layout.addStretch(1)
            self.content_layout.addWidget(card, 1, Qt.AlignmentFlag.AlignTop)
        self.content_layout.addStretch(1)
        self.content.adjustSize()
        self.content.setMinimumHeight(260)
        self._render_daily_fixture(selected)

    def _select_fixture_day(self, selected: date) -> None:
        self._selected_fixture_day = selected
        for day, button in self._day_selectors.items():
            button.setObjectName("calendarDayActive" if day == selected else "calendarDaySelect")
            button.style().unpolish(button)
            button.style().polish(button)
        self._render_daily_fixture(selected)

    def _render_daily_fixture(self, selected: date) -> None:
        HomePage._clear_layout(self.fixture_layout)
        month_names = (
            "ENE",
            "FEB",
            "MAR",
            "ABR",
            "MAY",
            "JUN",
            "JUL",
            "AGO",
            "SEP",
            "OCT",
            "NOV",
            "DIC",
        )
        self.fixture_date_label.setText(
            f"{self._weekday_name(selected)} · {selected.day:02d} {month_names[selected.month - 1]}"
        )
        matches_by_competition: dict[str, list] = {}
        for match in self._calendar_matches_by_day.get(selected, []):
            if match.competition_code in FEATURED_COMPETITION_PRIORITY:
                matches_by_competition.setdefault(match.competition_code, []).append(match)

        loaded_competitions = {
            competition.code: competition
            for competition in self.competitions
            if competition.code in FEATURED_COMPETITION_PRIORITY
        }
        prioritized_competitions = [
            loaded_competitions[code]
            for code in FEATURED_COMPETITION_PRIORITY
            if code in loaded_competitions
        ]
        competitions_with_matches = []
        for competition in prioritized_competitions:
            competition_matches = sorted(
                matches_by_competition.get(competition.code, []),
                key=lambda match: match.kickoff_utc,
            )
            if competition_matches:
                competitions_with_matches.append((competition, competition_matches))

        if not competitions_with_matches:
            empty_message = QLabel("Sin partidos para mostrar", objectName="emptyHint")
            empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_message.setMinimumHeight(190)
            self.fixture_layout.addWidget(empty_message, 0, 0, 1, 3)
            return

        for competition, competition_matches in competitions_with_matches:
            self._build_daily_league_card(competition, competition_matches)

    def _build_daily_league_card(self, competition: object, matches: list) -> None:
        league_card = QFrame(objectName="dailyLeagueCard")
        league_card.setMinimumWidth(0)
        league_card.setMinimumHeight(260)
        card_layout = QVBoxLayout(league_card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(6)
        league_header = QFrame(objectName="calendarLeagueHeader")
        league_header_layout = QHBoxLayout(league_header)
        league_header_layout.setContentsMargins(9, 5, 9, 5)
        title = QPushButton(competition.name.upper(), objectName="calendarLeagueTitle")
        title.setProperty("crestSize", 22)
        title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        title.setCursor(Qt.CursorShape.ArrowCursor)
        self.crest_loader.apply(
            title,
            COMPETITION_LOGO_URLS.get(competition.code),
            competition.name,
        )
        league_header_layout.addWidget(title)
        league_header_layout.addStretch(1)
        league_header_layout.addWidget(
            QLabel(f"{len(matches)} PARTIDOS", objectName="calendarLeagueCount")
        )
        card_layout.addWidget(league_header)

        matches_scroll = QScrollArea()
        matches_scroll.setWidgetResizable(True)
        matches_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        matches_scroll.setFrameShape(QFrame.Shape.NoFrame)
        matches_content = QWidget()
        matches_layout = QVBoxLayout(matches_content)
        matches_layout.setContentsMargins(0, 0, 0, 0)
        matches_layout.setSpacing(5)
        matches_scroll.setWidget(matches_content)
        card_layout.addWidget(matches_scroll, 1)

        for match in matches:
            row = QFrame(objectName="dailyFixtureRow")
            row.setProperty("europeanFixture", match.competition_code in {"UCL", "UEL"})
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(7, 5, 7, 5)
            row_layout.setSpacing(2)
            meta = QHBoxLayout()
            kickoff = QLabel(
                match_clock_label(match.kickoff_utc, match.status, self.timezone),
                objectName="fixtureTime",
            )
            kickoff.setMinimumWidth(76)
            meta.addWidget(kickoff)
            meta.addStretch(1)
            stage = match_stage_label(match.stage, match.matchday, "Partido")
            meta.addWidget(QLabel(stage, objectName="fixtureMeta"))
            row_layout.addLayout(meta)
            clubs = QHBoxLayout()
            clubs.setSpacing(3)
            home = self._club_button(match.home_team, match.home_team_id, match.home_crest_url)
            home.setObjectName("dailyFixtureClub")
            home.setProperty("europeanFixture", match.competition_code in {"UCL", "UEL"})
            home.setProperty("europeanSide", "home")
            home.setProperty("crestSize", 16)
            home.setIconSize(QSize(16, 16))
            home.setMinimumSize(0, 22)
            home.setToolTip(f"Local: {match.home_team} · Abrir ficha")
            clubs.addWidget(home, 1)
            has_result = match.home_score_ft is not None and match.away_score_ft is not None
            if has_result:
                score = QWidget()
                score.setObjectName("fixtureScore")
                score_layout = QHBoxLayout(score)
                score_layout.setContentsMargins(0, 0, 0, 0)
                score_layout.setSpacing(3)

                home_score = int(match.home_score_ft)
                away_score = int(match.away_score_ft)
                if match.status in {"LIVE", "PAUSED", "FINISHED"}:
                    if home_score == away_score:
                        home_outcome = away_outcome = "fixtureScoreDraw"
                    elif home_score > away_score:
                        home_outcome, away_outcome = "fixtureScoreWin", "fixtureScoreLoss"
                    else:
                        home_outcome, away_outcome = "fixtureScoreLoss", "fixtureScoreWin"
                else:
                    home_outcome = away_outcome = "fixtureScoreNeutral"

                home_value = QLabel(str(home_score), objectName=home_outcome)
                separator = QLabel("–", objectName="fixtureScoreSeparator")
                away_value = QLabel(str(away_score), objectName=away_outcome)
                for value in (home_value, separator, away_value):
                    value.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    score_layout.addWidget(value)
                score.setMinimumWidth(34)
            else:
                score = QLabel("VS", objectName="fixtureVersus")
                score.setMinimumWidth(34)
                score.setAlignment(Qt.AlignmentFlag.AlignCenter)
            clubs.addWidget(score, 0, Qt.AlignmentFlag.AlignCenter)
            away = self._club_button(match.away_team, match.away_team_id, match.away_crest_url)
            away.setObjectName("dailyFixtureClub")
            away.setProperty("europeanFixture", match.competition_code in {"UCL", "UEL"})
            away.setProperty("europeanSide", "away")
            if match.competition_code in {"UCL", "UEL"}:
                away.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            away.setProperty("crestSize", 16)
            away.setIconSize(QSize(16, 16))
            away.setMinimumSize(0, 22)
            away.setToolTip(f"Visitante: {match.away_team} · Abrir ficha")
            clubs.addWidget(away, 1)
            row_layout.addLayout(clubs)
            matches_layout.addWidget(row)
        matches_layout.addStretch(1)
        card_index = self.fixture_layout.count()
        self.fixture_layout.addWidget(league_card, card_index // 3, card_index % 3)

    @staticmethod
    def _weekday_name(day: date) -> str:
        return ("LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM")[day.weekday()]

    def _featured_match(self, matches):
        if not matches:
            return None
        return min(
            matches,
            key=lambda match: featured_match_key(
                match, self._current_positions, self._featured_policy
            ),
        )

    def _build_spotlight(self, match) -> CalendarSpotlight:
        spotlight = CalendarSpotlight()
        layout = QVBoxLayout(spotlight)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        competition = QLabel(
            match.competition_name.upper(), objectName="calendarSpotlightCompetition"
        )
        competition.setWordWrap(True)
        layout.addWidget(competition)
        home = self._club_button(match.home_team, match.home_team_id, match.home_crest_url)
        home.setObjectName("calendarFeaturedClub")
        home.setProperty("crestSize", 30)
        home.setIconSize(QSize(30, 30))
        home.setMinimumHeight(28)
        home.setToolTip(f"Abrir ficha de {match.home_team}")
        middle = QVBoxLayout()
        middle.setSpacing(2)
        middle.addWidget(HomePage._score_label(match), alignment=Qt.AlignmentFlag.AlignCenter)
        middle.addWidget(
            QLabel(
                match_clock_label(match.kickoff_utc, match.status, self.timezone),
                objectName="calendarSpotlightTime",
            ),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        away = self._club_button(match.away_team, match.away_team_id, match.away_crest_url)
        away.setObjectName("calendarFeaturedClub")
        away.setProperty("crestSize", 30)
        away.setIconSize(QSize(30, 30))
        away.setMinimumHeight(28)
        away.setToolTip(f"Abrir ficha de {match.away_team}")
        layout.addWidget(home)
        layout.addLayout(middle)
        layout.addWidget(away)
        stage = match_stage_label(match.stage, match.matchday, "Partido")
        layout.addWidget(
            QLabel(
                f"{stage} · {HomePage._status_label(match.status)}",
                objectName="calendarSpotlightMeta",
            )
        )
        return spotlight

    def _clear_content(self) -> None:
        HomePage._clear_layout(self.content_layout)

    def _render_competition_rail(self) -> None:
        HomePage._clear_layout(self.competition_buttons)
        for competition in self.competitions:
            button = QPushButton(competition.name)
            self.crest_loader.apply(
                button, COMPETITION_LOGO_URLS.get(competition.code), competition.name
            )
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"Abrir ficha de {competition.name}")
            button.clicked.connect(
                lambda _checked=False, code=competition.code: self.competition_selected.emit(code)
            )
            self.competition_buttons.addWidget(button)

    def _club_button(self, name: str, team_id: int, crest_url: str | None = None) -> QPushButton:
        button = QPushButton(name, objectName="clubLink")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Abrir ficha del equipo")
        self.crest_loader.apply(
            button, crest_url if crest_url is not None else self._crest_urls.get(team_id), name
        )
        button.clicked.connect(lambda _checked=False: self.team_selected.emit(team_id))
        return button
