"""Competition browser with season fixtures and standings from the local database."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from data90.config.settings import get_settings
from data90.repositories.players import (
    player_roster_for_competition_season,
    player_statistics_for_competition_season,
    scorer_snapshot_player_ids,
)
from data90.services.competition_service import CompetitionService
from data90.services.statistics_service import StatisticsService
from data90.sync.normalizer import normalize_name
from data90.ui.home import COMPETITION_LOGO_URLS, CrestLoader, match_stage_label
from data90.ui.match_labels import match_datetime_label


COMPETITION_THEMES = {
    # Each competition gets a distinct accent and its own dark-tinted surfaces.
    "PL": {
        "accent": "#A56EFF", "surface": "#120D1B", "header": "#261A38",
        "alternate": "#1B1427", "line": "#4E3971", "selected": "#3A2854",
    },
    "BUNDESLIGA": {
        "accent": "#F04452", "surface": "#160D10", "header": "#32171D",
        "alternate": "#251318", "line": "#6A3039", "selected": "#4A222B",
    },
    "EREDIVISIE": {
        "accent": "#FF9138", "surface": "#171007", "header": "#35210F",
        "alternate": "#29190D", "line": "#704A27", "selected": "#4C2C15",
    },
    "LALIGA": {
        "accent": "#F06B83", "surface": "#170C13", "header": "#351821",
        "alternate": "#28131B", "line": "#703744", "selected": "#4A2632",
    },
    "SERIE_A": {
        "accent": "#39A9F5", "surface": "#09131E", "header": "#112942",
        "alternate": "#102033", "line": "#285782", "selected": "#1D4161",
    },
    "LIGUE_1": {
        "accent": "#45C6A2", "surface": "#091511", "header": "#112C25",
        "alternate": "#10231D", "line": "#2A5D4C", "selected": "#1D493A",
    },
    "UCL": {
        "accent": "#66CDE8", "surface": "#0C1B27", "header": "#102634",
        "alternate": "#0C1D29", "line": "#1B3546", "selected": "#153143",
    },
    "UEL": {
        "accent": "#E2B94F", "surface": "#171206", "header": "#33280F",
        "alternate": "#291F0D", "line": "#6A5428", "selected": "#4A3A18",
    },
}


def _same_player_name_variant(left: str, right: str) -> bool:
    """Match full-name variants conservatively when the club also matches."""
    left_tokens = normalize_name(left).split()
    right_tokens = normalize_name(right).split()
    if left_tokens == right_tokens:
        return True
    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    if len(shorter) >= 2:
        index = 0
        for token in longer:
            if token == shorter[index]:
                index += 1
                if index == len(shorter):
                    return True
    return (
        len(left_tokens) >= 2
        and len(right_tokens) >= 2
        and left_tokens[-1] == right_tokens[-1]
        and (
            left_tokens[0] == right_tokens[0]
            or (len(left_tokens[0]) == 1 and right_tokens[0].startswith(left_tokens[0]))
            or (len(right_tokens[0]) == 1 and left_tokens[0].startswith(right_tokens[0]))
        )
    )


class CompetitionsPage(QWidget):
    team_selected = Signal(int)
    player_selected = Signal(int)

    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.timezone = ZoneInfo(get_settings().app_timezone)
        self.current_code: str | None = None
        self.current_is_league = False
        self.current_has_expanded_stats = False
        self.current_has_knockouts = False
        self.crest_loader = CrestLoader(self)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self.list_page = QWidget()
        list_layout = QVBoxLayout(self.list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_header = QHBoxLayout()
        list_header.addWidget(QLabel("Competiciones", objectName="sectionTitle"))
        list_header.addStretch(1)
        reload_button = QPushButton("Actualizar")
        reload_button.clicked.connect(self.refresh)
        list_header.addWidget(reload_button)
        list_layout.addLayout(list_header)

        panel = QFrame(objectName="panel")
        panel_layout = QVBoxLayout(panel)
        self.competition_table = self._table(
            ["Competición", "País / región", "Temporada", "Equipos", "Partidos", "Detalle"]
        )
        self.competition_table.setColumnWidth(5, 92)
        self.competition_table.cellDoubleClicked.connect(self._open_selected)
        panel_layout.addWidget(self.competition_table)
        list_layout.addWidget(panel, 1)
        self.stack.addWidget(self.list_page)

        self.detail_page = QWidget()
        self.detail_page.setObjectName("competitionDetailPage")
        detail_layout = QVBoxLayout(self.detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_header = QHBoxLayout()
        self.back_button = QPushButton("← Competiciones")
        self.back_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.list_page))
        detail_header.addWidget(self.back_button)
        self.competition_logo_button = QPushButton()
        self.competition_logo_button.setObjectName("detailCompetitionLogo")
        self.competition_logo_button.setProperty("crestSize", 32)
        self.competition_logo_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.competition_logo_button.setCursor(Qt.CursorShape.ArrowCursor)
        detail_header.addWidget(self.competition_logo_button)
        self.detail_title = QLabel("", objectName="sectionTitle")
        detail_header.addWidget(self.detail_title)
        detail_header.addStretch(1)
        detail_header.addWidget(QLabel("Temporada"))
        self.season_filter = QComboBox()
        self.season_filter.currentIndexChanged.connect(self._refresh_season)
        detail_header.addWidget(self.season_filter)
        detail_layout.addLayout(detail_header)
        self.season_summary = QLabel("", objectName="muted")
        self.season_summary.setObjectName("competitionSeasonSummary")
        detail_layout.addWidget(self.season_summary)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("competitionTabs")
        self.matches_table = self._table(
            ["Fecha y hora", "Jornada", "Local", "Resultado", "Visitante", "Estado"]
        )
        self.matches_table.horizontalHeader().setStretchLastSection(False)
        for column in range(6):
            self.matches_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.matches_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.matches_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.matches_table.horizontalHeader().setMinimumSectionSize(150)
        self.matches_table.setColumnWidth(2, 245)
        self.matches_table.setColumnWidth(4, 245)
        self.matches_table.itemClicked.connect(self._open_match_team)
        self.standings_table = self._table(
            ["Pos.", "Equipo", "Pts", "PJ", "G", "E", "P", "GF", "GC", "DG"]
        )
        self.standings_table.horizontalHeader().setStretchLastSection(False)
        for column in range(10):
            self.standings_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.standings_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.standings_table.itemClicked.connect(self._open_standing_team)
        self.standings_table.verticalHeader().setDefaultSectionSize(42)

        self.team_stats_table = self._table(
            ["Equipo", "PJ", "V", "E", "D", "GF", "GC", "DG", "GF/PJ", "GC/PJ", "% V", "VI"]
        )
        self.team_stats_table.installEventFilter(self)
        self.team_stats_table.viewport().installEventFilter(self)
        stats_header = self.team_stats_table.horizontalHeader()
        stats_header.setStretchLastSection(False)
        stats_header.setMinimumSectionSize(42)
        stats_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for column in range(1, self.team_stats_table.columnCount() - 1):
            stats_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        stats_header.setSectionResizeMode(
            self.team_stats_table.columnCount() - 1,
            QHeaderView.ResizeMode.Stretch,
        )
        self.team_stats_table.itemClicked.connect(self._open_team_stats_team)

        self.standings_page = self._table_page(
            self.standings_table,
            "Pos.: posición · Pts: puntos · PJ: partidos jugados · "
            "G: ganados · E: empatados · P: perdidos · GF: goles a favor · "
            "GC: goles en contra · DG: diferencia de goles (GF − GC).",
        )
        self.team_stats_page = self._table_page(
            self.team_stats_table,
            "PJ: partidos jugados · V/E/D: victorias, empates y derrotas · "
            "GF/GC: goles a favor/en contra · DG: diferencia de goles · "
            "GF/PJ y GC/PJ: goles a favor/en contra por partido jugado · "
            "% V: porcentaje de victorias · VI: vallas invictas (partidos sin recibir goles).",
        )
        self.team_stats_legend = self.team_stats_page.findChild(QLabel, "tableLegend")

        self.upcoming_table = self._table(
            ["Fecha y hora", "Jornada", "Local", "Visitante", "Estado"]
        )
        self.upcoming_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.upcoming_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.upcoming_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.upcoming_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.upcoming_table.horizontalHeader().setMinimumSectionSize(150)
        self.upcoming_table.setColumnWidth(2, 245)
        self.upcoming_table.setColumnWidth(3, 245)
        self.upcoming_table.itemClicked.connect(self._open_upcoming_team)

        self.scorers_table = self._table(
            ["Pos.", "Jugador", "Equipo", "PJ", "Goles", "Asistencias"]
        )
        self.scorers_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.scorers_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.scorers_table.itemClicked.connect(self._open_scorer_link)

        self.knockout_table = self._table(
            ["Fecha y hora", "Ronda", "Local", "Resultado", "Visitante", "Estado"]
        )
        for column in range(6):
            self.knockout_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.knockout_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.knockout_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.knockout_table.setColumnWidth(2, 245)
        self.knockout_table.setColumnWidth(4, 245)

        self._set_tabs_for_competition(None)
        detail_layout.addWidget(self.tabs, 1)
        self.stack.addWidget(self.detail_page)

    @staticmethod
    def _table_page(table: QTableWidget, legend_text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        legend = QLabel(legend_text, objectName="tableLegend")
        legend.setWordWrap(True)
        layout.addWidget(table, 1)
        layout.addWidget(legend)
        return page

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(42)
        return table

    def refresh(self) -> None:
        try:
            with self.session_factory() as session:
                competitions = CompetitionService(session).get_competitions()
        except SQLAlchemyError as error:
            self._database_error(error)
            return
        self.competition_table.setRowCount(0)
        for competition in competitions:
            row = self.competition_table.rowCount()
            self.competition_table.insertRow(row)
            country = competition.country_name or competition.region_name or "Europa"
            values = [
                competition.name,
                country,
                competition.latest_season or "Sin temporada",
                str(competition.team_count),
                str(competition.match_count),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, competition.code)
                self.competition_table.setItem(row, column, item)
            button = QPushButton("Abrir")
            button.setMinimumWidth(78)
            button.setMinimumHeight(30)
            button.clicked.connect(
                lambda _checked=False, code=competition.code: self.open_detail(code)
            )
            self.competition_table.setCellWidget(row, 5, button)
        if not competitions:
            self._empty_table(self.competition_table, "No hay competiciones en el catálogo local.")

    def _open_selected(self, row: int, _column: int) -> None:
        item = self.competition_table.item(row, 0)
        code = item.data(Qt.ItemDataRole.UserRole) if item else None
        if code:
            self.open_detail(code)

    def open_detail(self, code: str) -> None:
        try:
            with self.session_factory() as session:
                service = CompetitionService(session)
                competitions = service.get_competitions()
                competition = next(item for item in competitions if item.code == code)
                seasons = service.get_seasons(code)
        except (SQLAlchemyError, StopIteration, ValueError) as error:
            self._database_error(error)
            return
        self.current_code = code
        self._apply_competition_theme(code)
        self.current_is_league = competition.competition_type == "LEAGUE"
        self.current_has_knockouts = code in {"UCL", "UEL"}
        self.current_has_expanded_stats = self.current_is_league or self.current_has_knockouts
        self._set_tabs_for_competition(code)
        self.detail_title.setText(competition.name)
        self.crest_loader.apply(
            self.competition_logo_button,
            COMPETITION_LOGO_URLS.get(code),
            competition.name,
        )
        self.season_filter.blockSignals(True)
        self.season_filter.clear()
        for season in seasons:
            self.season_filter.addItem(season.label, season.competition_season_id)
        self.season_filter.blockSignals(False)
        self.stack.setCurrentWidget(self.detail_page)
        if seasons:
            self._refresh_season()
        else:
            self.season_summary.setText("Todavía no hay temporadas cargadas.")
            self.matches_table.setRowCount(0)
            self.standings_table.setRowCount(0)
            self.team_stats_table.setRowCount(0)
            self.upcoming_table.setRowCount(0)
            self.scorers_table.setRowCount(0)
            self.knockout_table.setRowCount(0)

    def _apply_competition_theme(self, code: str) -> None:
        theme = COMPETITION_THEMES.get(code)
        if theme is None:
            self.detail_page.setStyleSheet("")
            self.tabs.setStyleSheet("")
            self.season_filter.setStyleSheet("")
            self.competition_logo_button.setStyleSheet("")
            self.detail_title.setStyleSheet("")
            return
        accent = theme["accent"]
        surface = theme["surface"]
        header = theme["header"]
        alternate = theme["alternate"]
        line = theme["line"]
        selected = theme["selected"]
        # Keep the competition identity colors in the data area, while the
        # page heading stays on the application's original neutral dark style.
        self.detail_page.setStyleSheet("")
        self.tabs.setStyleSheet(
            f"""
            QTabWidget#competitionTabs::pane {{
                background: {surface};
                border: 1px solid {line};
                border-top: 3px solid {accent};
                border-radius: 5px;
                top: -1px;
            }}
            QTabWidget#competitionTabs QTabBar::tab {{
                background: {surface};
                border-bottom: 2px solid {line};
                color: #a9bac6;
            }}
            QTabWidget#competitionTabs QTabBar::tab:selected {{
                color: {accent};
                background: {selected};
                border-bottom: 3px solid {accent};
            }}
            QTabWidget#competitionTabs QTabBar::tab:hover {{
                background: {header};
                color: #f0f6fa;
            }}
            QTabWidget#competitionTabs QTableWidget {{
                background: {surface};
                alternate-background-color: {alternate};
                selection-background-color: {selected};
                gridline-color: {line};
            }}
            QTabWidget#competitionTabs QTableWidget::item {{
                border-bottom: 1px solid {line};
            }}
            QTabWidget#competitionTabs QTableWidget::item:selected {{
                background: {selected};
                color: #f5f8fb;
            }}
            QTabWidget#competitionTabs QHeaderView::section {{
                background: {header};
                color: {accent};
                border-bottom: 2px solid {accent};
            }}
            QTabWidget#competitionTabs QLabel#tableLegend {{
                background: {surface};
                border-top: 1px solid {line};
                border-left: 3px solid {accent};
                padding: 6px 8px;
            }}
            """
        )
        self.season_filter.setStyleSheet("")
        self.competition_logo_button.setStyleSheet("")
        self.detail_title.setStyleSheet("")
        self.season_summary.setStyleSheet("")

    def _set_tabs_for_competition(self, code: str | None) -> None:
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.team_stats_table.clearSpans()
        if self.current_has_expanded_stats:
            self.team_stats_table.setHorizontalHeaderLabels(
                [
                    "Equipo",
                    "GF/PJ",
                    "GC/PJ",
                    "% V",
                    "VI",
                    "Métrica",
                    "Total",
                    "Local",
                    "Visitante",
                    "Prom./PJ",
                    "% partidos",
                    "PJ",
                ]
            )
            stats_header = self.team_stats_table.horizontalHeader()
            stats_header.setStretchLastSection(False)
            self.team_stats_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            for column in range(self.team_stats_table.columnCount()):
                stats_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self._fit_expanded_team_stats_columns()
            if self.team_stats_legend:
                self.team_stats_legend.setText(
                    "GF/PJ y GC/PJ: goles a favor y en contra por partido · "
                    "% V: porcentaje de victorias · VI: vallas invictas · "
                    "Total/Local/Visitante: sumas de la temporada · "
                    "Prom./PJ: promedio por partido con dato · "
                    "% partidos: partidos con al menos un evento · "
                    "PJ: partidos con estadística publicada · Posesión: promedio de la temporada · "
                    "Penales: convertidos o cometidos/intentados según Promiedos. "
                    "Los agregados de Promiedos no incluyen desglose local/visitante."
                )
        else:
            self.team_stats_table.setHorizontalHeaderLabels(
                ["Equipo", "PJ", "V", "E", "D", "GF", "GC", "DG", "GF/PJ", "GC/PJ", "% V", "VI"]
            )
            if self.team_stats_legend:
                self.team_stats_legend.setText(
                    "PJ: partidos jugados · V/E/D: victorias, empates y derrotas · "
                    "GF/GC: goles a favor/en contra · DG: diferencia de goles · "
                    "GF/PJ y GC/PJ: goles a favor/en contra por partido jugado · "
                    "% V: porcentaje de victorias · VI: vallas invictas "
                    "(partidos sin recibir goles)."
                )
        self.tabs.addTab(self.standings_page, "Tabla de posiciones")
        self.tabs.addTab(self.team_stats_page, "Estadísticas de equipos")
        self.tabs.addTab(self.upcoming_table, "Próximos partidos")
        self.tabs.addTab(self.scorers_table, "Goleadores")
        if self.current_has_knockouts:
            self.tabs.addTab(self.knockout_table, "Fase eliminatoria")

    def _club_button(self, name: str, team_id: int, crest_url: str | None) -> QPushButton:
        button = QPushButton(name, objectName="clubLink")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Abrir ficha del equipo")
        self.crest_loader.apply(button, crest_url, name)
        button.clicked.connect(
            lambda _checked=False, selected_id=team_id: self.team_selected.emit(selected_id)
        )
        return button

    def _refresh_season(self, _index: int = -1) -> None:
        season_id = self.season_filter.currentData()
        if season_id is None or self.current_code is None:
            return
        try:
            with self.session_factory() as session:
                service = CompetitionService(session)
                season = next(
                    item
                    for item in service.get_seasons(self.current_code)
                    if item.competition_season_id == season_id
                )
                matches = service.get_matches(season_id)
                standings = service.get_standings(season_id)
                team_statistics = StatisticsService(session).for_competition_season(season_id)
                match_stat_summaries = (
                    StatisticsService(session).match_stat_summaries_for_competition_season(
                        season_id
                    )
                    if self.current_has_expanded_stats
                    else {}
                )
                scorers = player_statistics_for_competition_season(session, season_id)
                roster = player_roster_for_competition_season(session, season_id)
                snapshot_player_ids = (
                    scorer_snapshot_player_ids(session, season_id, "fotmob")
                    if self.current_code
                    in {"BUNDESLIGA", "EREDIVISIE", "LIGUE_1", "SERIE_A", "LALIGA"}
                    else set()
                )
        except (SQLAlchemyError, StopIteration, ValueError) as error:
            self._database_error(error)
            return
        synced = (
            season.last_synced_at.astimezone(self.timezone).strftime("%d/%m/%Y %H:%M")
            if season.last_synced_at
            else "sin sincronización registrada"
        )
        self.season_summary.setText(
            f"{season.team_count} equipos · {season.match_count} partidos · "
            f"Última actualización: {synced}"
        )
        self.matches_table.setRowCount(0)
        for match in matches:
            row = self.matches_table.rowCount()
            self.matches_table.insertRow(row)
            kickoff = match_datetime_label(match.kickoff_utc, match.status, self.timezone)
            stage = match_stage_label(match.stage, match.matchday)
            score = (
                f"{match.home_score_ft} – {match.away_score_ft}"
                if match.home_score_ft is not None and match.away_score_ft is not None
                else "—"
            )
            values = [
                kickoff,
                stage,
                match.home_team,
                score,
                match.away_team,
                self._status(match.status),
            ]
            for column, value in enumerate(values):
                if column in (2, 4):
                    team_id = match.home_team_id if column == 2 else match.away_team_id
                    crest_url = match.home_crest_url if column == 2 else match.away_crest_url
                    self.matches_table.setCellWidget(
                        row, column, self._club_button(value, team_id, crest_url)
                    )
                    continue
                item = QTableWidgetItem(value)
                self.matches_table.setItem(row, column, item)
        if not matches:
            self._empty_table(self.matches_table, "No hay partidos cargados para esta temporada.")

        self.standings_table.setRowCount(0)
        for standing in standings:
            row = self.standings_table.rowCount()
            self.standings_table.insertRow(row)
            values = [
                standing.position,
                standing.team_name,
                standing.points,
                standing.played,
                standing.won,
                standing.drawn,
                standing.lost,
                standing.goals_for,
                standing.goals_against,
                standing.goal_difference,
            ]
            for column, value in enumerate(values):
                if column == 1:
                    self.standings_table.setCellWidget(
                        row,
                        column,
                        self._club_button(standing.team_name, standing.team_id, standing.crest_url),
                    )
                    continue
                item = QTableWidgetItem(str(value))
                if column != 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._style_standing_cell(item, column, value)
                self.standings_table.setItem(row, column, item)
        if not standings:
            self._empty_table(
                self.standings_table,
                "Esta fuente no publicó una tabla de posiciones para la temporada.",
            )

        self._populate_team_statistics(team_statistics, match_stat_summaries)
        self._fit_expanded_team_stats_columns()
        self._populate_upcoming_matches(matches)
        self._populate_scorers(scorers, roster, snapshot_player_ids)
        if self.current_has_knockouts:
            self._populate_knockouts(matches)

    def _populate_team_statistics(
        self, teams: list[object], match_stat_summaries: dict[int, object]
    ) -> None:
        ordered = sorted(
            teams,
            key=lambda row: (
                row.metrics.points,
                row.metrics.goal_difference,
                row.metrics.goals_for,
                row.team_name.casefold(),
            ),
            reverse=True,
        )
        self.team_stats_table.clearSpans()
        self.team_stats_table.setRowCount(0)
        if self.current_has_expanded_stats:
            self._populate_league_team_statistics(ordered, match_stat_summaries)
            return
        team_column_width = 0
        for row_number, team in enumerate(ordered):
            metrics = team.metrics
            values = (
                team.team_name,
                str(metrics.played),
                str(metrics.won),
                str(metrics.drawn),
                str(metrics.lost),
                str(metrics.goals_for),
                str(metrics.goals_against),
                str(metrics.goal_difference),
                self._decimal(metrics.average_goals_for),
                self._decimal(metrics.average_goals_against),
                self._percent(metrics.win_percent),
                str(metrics.clean_sheets),
            )
            self.team_stats_table.insertRow(row_number)
            for column, value in enumerate(values):
                if column == 0:
                    button = self._club_button(team.team_name, team.team_id, team.crest_url)
                    team_column_width = max(team_column_width, button.sizeHint().width())
                    self.team_stats_table.setCellWidget(
                        row_number,
                        column,
                        button,
                    )
                    button.setToolTip(f"{team.team_name} · Abrir ficha del equipo")
                    continue
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 5:
                    item.setForeground(QColor("#47d49a"))
                elif column == 6:
                    item.setForeground(QColor("#ff7b83"))
                elif column == 7:
                    item.setForeground(
                        QColor("#47d49a")
                        if metrics.goal_difference > 0
                        else QColor("#ff7b83")
                        if metrics.goal_difference < 0
                        else QColor("#e8edf2")
                    )
                self.team_stats_table.setItem(row_number, column, item)
        self.team_stats_table.setColumnWidth(0, max(190, team_column_width + 8))
        if not ordered:
            self._empty_table(
                self.team_stats_table,
                "No hay partidos finalizados para calcular estadísticas de equipos.",
            )

    def _populate_league_team_statistics(
        self, teams: list[object], summaries_by_team: dict[int, object]
    ) -> None:
        definitions = (
            ("yellow_cards", "Tarjetas amarillas"),
            ("red_cards", "Tarjetas rojas"),
            ("corners", "Córners"),
            ("possession_percentage", "Posesión"),
            ("penalties_scored", "Penales convertidos"),
            ("penalties_conceded", "Penales cometidos"),
        )
        for team in teams:
            team_summaries = summaries_by_team.get(team.team_id)
            first_row = self.team_stats_table.rowCount()
            self.team_stats_table.setRowCount(first_row + len(definitions))
            metrics = team.metrics
            button = self._club_button(team.team_name, team.team_id, team.crest_url)
            button.setToolTip(f"{team.team_name} · Abrir ficha del equipo")
            self.team_stats_table.setCellWidget(first_row, 0, button)
            self.team_stats_table.setSpan(first_row, 0, len(definitions), 1)

            general_values = (
                self._decimal(metrics.average_goals_for),
                self._decimal(metrics.average_goals_against),
                self._percent(metrics.win_percent),
                str(metrics.clean_sheets),
            )
            for column, value in enumerate(general_values, start=1):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 1:
                    item.setForeground(QColor("#47d49a"))
                elif column == 2:
                    item.setForeground(QColor("#ff7b83"))
                self.team_stats_table.setItem(first_row, column, item)
                self.team_stats_table.setSpan(first_row, column, len(definitions), 1)

            for offset, (field, label) in enumerate(definitions):
                row = first_row + offset
                summary = getattr(team_summaries, field, None) if team_summaries else None
                aggregate_value = "—"
                if team.promiedos is not None:
                    aggregate = team.promiedos
                    if (
                        field == "possession_percentage"
                        and aggregate.possession_percentage is not None
                    ):
                        aggregate_value = self._percent(float(aggregate.possession_percentage))
                    elif field == "penalties_scored" and aggregate.penalties_scored is not None:
                        attempts = self._display_value(aggregate.penalties_attempted)
                        aggregate_value = f"{aggregate.penalties_scored}/{attempts}"
                    elif field == "penalties_conceded" and aggregate.penalties_conceded is not None:
                        attempts = self._display_value(aggregate.penalties_conceded_attempted)
                        aggregate_value = f"{aggregate.penalties_conceded}/{attempts}"
                if summary is not None:
                    values = (
                        label,
                        self._display_value(summary.total),
                        self._display_value(summary.home_total),
                        self._display_value(summary.away_total),
                        self._decimal(summary.average_per_match),
                        self._percent(summary.matches_with_event_percent),
                        self._display_value(summary.matches_with_data),
                    )
                    tooltip = summary.source
                elif field in {"possession_percentage", "penalties_scored", "penalties_conceded"}:
                    values = (label, aggregate_value, "—", "—", "—", "—", "—")
                    tooltip = (
                        "Promiedos · dato agregado de la temporada; la fuente no publica "
                        "desglose por local y visitante."
                        if team.promiedos is not None and aggregate_value != "—"
                        else "Promiedos no publica este dato para esta competición."
                    )
                else:
                    values = (label, "—", "—", "—", "—", "—", "—")
                    tooltip = None
                for column, value in enumerate(values, start=5):
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if tooltip:
                        item.setToolTip(tooltip)
                    self.team_stats_table.setItem(row, column, item)
        if not teams:
            self._empty_table(
                self.team_stats_table,
                "No hay datos de equipos para esta temporada.",
            )

    def _fit_expanded_team_stats_columns(self) -> None:
        if not self.current_has_expanded_stats:
            return
        viewport_width = self.team_stats_table.viewport().width()
        if viewport_width <= 0:
            return

        # Equipo, GF/PJ, GC/PJ, %V, VI, Métrica, Total, Local, Visitante,
        # Prom./PJ, % partidos, PJ. Keep long labels readable without
        # sacrificing enough room for percentages and match counts.
        proportions = (20, 6, 6, 6, 4, 16, 7, 7, 8, 8, 9, 3)
        minimums = (200, 56, 56, 56, 42, 170, 58, 58, 70, 70, 90, 42)
        widths = [
            max(minimum, round(viewport_width * proportion / 100))
            for proportion, minimum in zip(proportions, minimums, strict=True)
        ]
        # Correct rounding drift in the club column to fill the whole viewport.
        # Minimums may exceed a narrow viewport; the horizontal bar then remains
        # available instead of clipping the data.
        rounding_delta = viewport_width - sum(widths)
        if widths[0] + rounding_delta >= minimums[0]:
            widths[0] += rounding_delta
        header = self.team_stats_table.horizontalHeader()
        for column, width in enumerate(widths):
            self.team_stats_table.setColumnWidth(column, width)
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        actual_width = sum(
            header.sectionSize(column)
            for column in range(self.team_stats_table.columnCount())
        )
        if actual_width <= viewport_width:
            self.team_stats_table.setColumnWidth(
                0, header.sectionSize(0) + viewport_width - actual_width
            )

    def eventFilter(self, watched, event) -> bool:
        if (
            watched in (self.team_stats_table, self.team_stats_table.viewport())
            and event.type() == QEvent.Type.Resize
            and self.current_has_expanded_stats
        ):
            QTimer.singleShot(0, self._fit_expanded_team_stats_columns)
        return super().eventFilter(watched, event)

    def _populate_upcoming_matches(self, matches: list[object]) -> None:
        self.upcoming_table.setColumnHidden(1, self.current_is_league)
        now = datetime.now(UTC)
        upcoming = [
            match
            for match in matches
            if match.kickoff_utc >= now and match.status in {"SCHEDULED", "POSTPONED"}
        ]
        self.upcoming_table.setRowCount(0)
        for row, match in enumerate(upcoming):
            self.upcoming_table.insertRow(row)
            stage = match_stage_label(match.stage, match.matchday)
            values = (
                match_datetime_label(match.kickoff_utc, match.status, self.timezone),
                stage,
                match.home_team,
                match.away_team,
                self._status(match.status),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (1, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column in (2, 3):
                    team_id = match.home_team_id if column == 2 else match.away_team_id
                    crest_url = match.home_crest_url if column == 2 else match.away_crest_url
                    self.upcoming_table.setCellWidget(
                        row, column, self._club_button(value, team_id, crest_url)
                    )
                    continue
                self.upcoming_table.setItem(row, column, item)
        if not upcoming:
            self._empty_table(
                self.upcoming_table,
                "No hay próximos partidos con fecha confirmada para esta temporada.",
            )

    def _populate_knockouts(self, matches: list[object]) -> None:
        knockout_matches = sorted(
            (match for match in matches if self._is_knockout_match(match)),
            key=lambda match: match.kickoff_utc,
        )
        self.knockout_table.setRowCount(0)
        for row, match in enumerate(knockout_matches):
            self.knockout_table.insertRow(row)
            round_name = match.round_label or match_stage_label(match.stage, match.matchday)
            score = (
                f"{match.home_score_ft} – {match.away_score_ft}"
                if match.home_score_ft is not None and match.away_score_ft is not None
                else "—"
            )
            values = (
                match_datetime_label(match.kickoff_utc, match.status, self.timezone),
                round_name,
                match.home_team,
                score,
                match.away_team,
                self._status(match.status),
            )
            for column, value in enumerate(values):
                if column in (2, 4):
                    team_id = match.home_team_id if column == 2 else match.away_team_id
                    crest_url = match.home_crest_url if column == 2 else match.away_crest_url
                    self.knockout_table.setCellWidget(
                        row, column, self._club_button(value, team_id, crest_url)
                    )
                    continue
                item = QTableWidgetItem(value)
                if column in (1, 3, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.knockout_table.setItem(row, column, item)
        if not knockout_matches:
            self._empty_table(
                self.knockout_table,
                "La fase eliminatoria aparecerá aquí cuando comience y se publiquen sus partidos.",
            )

    @staticmethod
    def _is_knockout_match(match: object) -> bool:
        phase = (
            " ".join(str(value or "") for value in (match.stage, match.round_label))
            .casefold()
            .replace("_", " ")
        )
        phase = re.sub(r"[–—-]", " ", phase)
        explicit_knockout_markers = (
            "knockout",
            "play off",
            "playoff",
            "round of",
            "round-of",
            "last 16",
            "last 8",
            "quarter final",
            "semi final",
            "final",
            "octavos",
            "dieciseisavos",
            "cuartos",
            "semifinal",
            "repechaje",
            "eliminatoria",
            "1/8",
            "1/4",
            "1/16",
        )
        return any(marker in phase for marker in explicit_knockout_markers)

    def _populate_scorers(
        self,
        players: list[object],
        roster: list[object],
        snapshot_player_ids: set[int] | None = None,
    ) -> None:
        roster_by_player: dict[int, list[object]] = {}
        for member in roster:
            roster_by_player.setdefault(member.player_id, []).append(member)

        if snapshot_player_ids:
            snapshot_targets: dict[int, list[tuple[int, str]]] = {}
            for player in players:
                if player.player_id not in snapshot_player_ids or player.source_code != "fotmob":
                    continue
                key = normalize_name(player.display_name)
                for member in roster_by_player.get(player.player_id, []):
                    if member.source_code != "fotmob":
                        continue
                    snapshot_targets.setdefault(member.team_id, []).append((player.player_id, key))

            supplemental_by_target: dict[int, dict[int, list[object]]] = {}
            for player in players:
                if player.player_id in snapshot_player_ids or player.source_code == "fotmob":
                    continue
                if player.assists is None:
                    continue
                matching_targets = set()
                for member in roster_by_player.get(player.player_id, []):
                    for target_id, target_name in snapshot_targets.get(member.team_id, []):
                        if _same_player_name_variant(player.display_name, target_name):
                            matching_targets.add(target_id)
                if len(matching_targets) == 1:
                    target_id = next(iter(matching_targets))
                    supplemental_by_target.setdefault(target_id, {}).setdefault(
                        player.assists, []
                    ).append(player)
            supplemental = []
            for target_id, assist_values in supplemental_by_target.items():
                if len(assist_values) == 1:
                    player = next(iter(assist_values.values()))[0]
                    supplemental.append(
                        replace(player, player_id=target_id, goals=None, appearances=None)
                    )
            players = [
                player
                for player in players
                if player.player_id in snapshot_player_ids
                and (player.source_code != "fotmob" or player.goals is not None)
            ] + supplemental

        merged: dict[int, dict[str, object]] = {}
        for player in players:
            values = merged.setdefault(
                player.player_id,
                {
                    "name": player.display_name,
                    "goals": None,
                    "assists": None,
                    "appearances": None,
                    "appearance_source": None,
                    "source": player.source_code,
                },
            )
            if (
                player.player_id in (snapshot_player_ids or set())
                and player.source_code == "fotmob"
            ):
                values["name"] = player.display_name
            for field in ("goals", "assists", "appearances"):
                value = getattr(player, field)
                if field == "goals" and player.player_id in (snapshot_player_ids or set()):
                    if player.source_code == "fotmob":
                        values[field] = value
                        values["source"] = player.source_code
                        continue
                    if values["source"] == "fotmob":
                        continue
                if field == "appearances" and value is not None:
                    if player.source_code == "fpl-premier-league":
                        values[field] = value
                        values["appearance_source"] = player.source_code
                        continue
                    if player.source_code == "fotmob":
                        if values["appearance_source"] != "fpl-premier-league":
                            values[field] = value
                            values["appearance_source"] = player.source_code
                        continue
                    if values["appearance_source"] in {"fpl-premier-league", "fotmob"}:
                        continue
                if value is not None and (values[field] is None or value > values[field]):
                    values[field] = value
                    if field == "appearances":
                        values["appearance_source"] = player.source_code
                    if field == "goals":
                        values["source"] = player.source_code

        scorers_sorted = sorted(
            (
                (player_id, data)
                for player_id, data in merged.items()
                if data["goals"] is not None and data["goals"] > 0
            ),
            key=lambda entry: (-entry[1]["goals"], str(entry[1]["name"]).casefold()),
        )
        self.scorers_table.setRowCount(0)
        for rank, (player_id, player) in enumerate(scorers_sorted, start=1):
            members = roster_by_player.get(player_id, [])
            member = next(
                (item for item in members if item.source_code == player["source"]),
                members[0] if members else None,
            )
            self.scorers_table.insertRow(rank - 1)
            values = (
                str(rank),
                str(player["name"]),
                member.team_name if member else "Equipo no informado",
                self._display_value(player["appearances"]),
                self._display_value(player["goals"]),
                self._display_value(player["assists"]),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, player_id)
                    item.setForeground(QColor("#69aef7"))
                    font = item.font()
                    font.setUnderline(True)
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip("Abrir ficha del jugador")
                elif column == 2 and member:
                    self.scorers_table.setCellWidget(
                        rank - 1,
                        column,
                        self._club_button(member.team_name, member.team_id, member.crest_url),
                    )
                    continue
                self.scorers_table.setItem(rank - 1, column, item)
        if not scorers_sorted:
            self._empty_table(
                self.scorers_table,
                "No hay goleadores publicados para esta temporada.",
            )

    @staticmethod
    def _display_value(value: object) -> str:
        return str(value) if value is not None else "—"

    @staticmethod
    def _percent(value: float | None) -> str:
        return f"{value:.1f}%" if value is not None else "—"

    @staticmethod
    def _decimal(value: float | None) -> str:
        return f"{value:.2f}" if value is not None else "—"

    def _open_team_stats_team(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            team_id = item.data(Qt.ItemDataRole.UserRole)
            if team_id is not None:
                self.team_selected.emit(int(team_id))

    def _open_upcoming_team(self, item: QTableWidgetItem) -> None:
        if item.column() in (2, 3):
            team_id = item.data(Qt.ItemDataRole.UserRole)
            if team_id is not None:
                self.team_selected.emit(int(team_id))

    def _open_scorer_link(self, item: QTableWidgetItem) -> None:
        linked_id = item.data(Qt.ItemDataRole.UserRole)
        if linked_id is None:
            return
        if item.column() == 1:
            self.player_selected.emit(int(linked_id))
        elif item.column() == 2:
            self.team_selected.emit(int(linked_id))

    @staticmethod
    def _style_standing_cell(item: QTableWidgetItem, column: int, value: object) -> None:
        if column == 2:
            font = item.font()
            font.setBold(True)
            font.setPointSize(font.pointSize() + 1)
            item.setFont(font)
            item.setForeground(QColor("#ffffff"))
            item.setBackground(QColor("#153143"))
        elif column == 7:
            item.setForeground(QColor("#47d49a"))
        elif column == 8:
            item.setForeground(QColor("#ff7b83"))
        elif column == 9:
            difference = int(value)
            item.setForeground(
                QColor("#47d49a")
                if difference > 0
                else QColor("#ff7b83")
                if difference < 0
                else QColor("#e8edf2")
            )

    @staticmethod
    def _mark_team_link(item: QTableWidgetItem, team_id: int) -> None:
        item.setData(Qt.ItemDataRole.UserRole, team_id)
        item.setForeground(QColor("#69aef7"))
        font = item.font()
        font.setUnderline(True)
        font.setBold(True)
        item.setFont(font)
        item.setToolTip("Abrir ficha del equipo")

    def _open_match_team(self, item: QTableWidgetItem) -> None:
        if item.column() in (2, 4):
            team_id = item.data(Qt.ItemDataRole.UserRole)
            if team_id is not None:
                self.team_selected.emit(int(team_id))

    def _open_standing_team(self, item: QTableWidgetItem) -> None:
        if item.column() == 1:
            team_id = item.data(Qt.ItemDataRole.UserRole)
            if team_id is not None:
                self.team_selected.emit(int(team_id))

    @staticmethod
    def _status(status: str) -> str:
        return {
            "SCHEDULED": "Programado",
            "LIVE": "En vivo",
            "PAUSED": "Pausado",
            "FINISHED": "Finalizado",
            "POSTPONED": "Aplazado",
            "SUSPENDED": "Suspendido",
            "CANCELLED": "Cancelado",
            "UNKNOWN": "Sin confirmar",
        }.get(status, status)

    @staticmethod
    def _empty_table(table: QTableWidget, message: str) -> None:
        table.setRowCount(1)
        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(0, 0, item)
        table.setSpan(0, 0, 1, table.columnCount())

    def _database_error(self, error: Exception) -> None:
        message = f"Base local no disponible ({error.__class__.__name__}). Revisá PostgreSQL."
        if self.stack.currentWidget() == self.list_page:
            self._empty_table(self.competition_table, message)
        else:
            self._empty_table(self.matches_table, message)
            self._empty_table(self.standings_table, message)
