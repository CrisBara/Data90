"""Competition browser with season fixtures and standings from the local database."""

from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt
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

from football_analytics.config.settings import get_settings
from football_analytics.services.competition_service import CompetitionService


class CompetitionsPage(QWidget):
    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.timezone = ZoneInfo(get_settings().app_timezone)
        self.current_code: str | None = None
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
        self.competition_table.cellDoubleClicked.connect(self._open_selected)
        panel_layout.addWidget(self.competition_table)
        list_layout.addWidget(panel, 1)
        self.stack.addWidget(self.list_page)

        self.detail_page = QWidget()
        detail_layout = QVBoxLayout(self.detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_header = QHBoxLayout()
        self.back_button = QPushButton("← Competiciones")
        self.back_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.list_page))
        detail_header.addWidget(self.back_button)
        self.detail_title = QLabel("", objectName="sectionTitle")
        detail_header.addWidget(self.detail_title)
        detail_header.addStretch(1)
        detail_header.addWidget(QLabel("Temporada"))
        self.season_filter = QComboBox()
        self.season_filter.currentIndexChanged.connect(self._refresh_season)
        detail_header.addWidget(self.season_filter)
        detail_layout.addLayout(detail_header)
        self.season_summary = QLabel("", objectName="muted")
        detail_layout.addWidget(self.season_summary)

        self.tabs = QTabWidget()
        self.matches_table = self._table(
            ["Fecha y hora", "Jornada / fase", "Local", "Resultado", "Visitante", "Estado"]
        )
        self.standings_table = self._table(
            ["Pos.", "Equipo", "PJ", "G", "E", "P", "GF", "GC", "DG", "Pts", "Forma"]
        )
        self.tabs.addTab(self.matches_table, "Partidos")
        self.tabs.addTab(self.standings_table, "Tabla")
        detail_layout.addWidget(self.tabs, 1)
        self.stack.addWidget(self.detail_page)

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
        self.detail_title.setText(competition.name)
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
            kickoff = match.kickoff_utc.astimezone(self.timezone).strftime("%d/%m/%Y %H:%M")
            stage = match.stage or (f"Jornada {match.matchday}" if match.matchday else "—")
            score = (
                f"{match.home_score_ft} – {match.away_score_ft}"
                if match.home_score_ft is not None and match.away_score_ft is not None
                else "—"
            )
            for column, value in enumerate(
                [
                    kickoff,
                    stage,
                    match.home_team,
                    score,
                    match.away_team,
                    self._status(match.status),
                ]
            ):
                self.matches_table.setItem(row, column, QTableWidgetItem(value))
        if not matches:
            self._empty_table(self.matches_table, "No hay partidos cargados para esta temporada.")

        self.standings_table.setRowCount(0)
        for standing in standings:
            row = self.standings_table.rowCount()
            self.standings_table.insertRow(row)
            values = [
                standing.position,
                standing.team_name,
                standing.played,
                standing.won,
                standing.drawn,
                standing.lost,
                standing.goals_for,
                standing.goals_against,
                standing.goal_difference,
                standing.points,
                standing.form or "—",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column != 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.standings_table.setItem(row, column, item)
        if not standings:
            self._empty_table(
                self.standings_table,
                "Esta fuente no publicó una tabla de posiciones para la temporada.",
            )

    @staticmethod
    def _status(status: str) -> str:
        return {
            "SCHEDULED": "Programado",
            "LIVE": "En juego",
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
