"""Team search, season-independent metrics, and local match history."""

from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from football_analytics.analytics.team_metrics import TeamMetrics
from football_analytics.config.constants import COMPETITIONS
from football_analytics.config.settings import get_settings
from football_analytics.services.team_service import TeamService


class TeamsPage(QWidget):
    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.timezone = ZoneInfo(get_settings().app_timezone)
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
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Equipos", objectName="sectionTitle"))
        heading.addStretch(1)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Buscar equipo…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self.refresh)
        self.search_box.setMaximumWidth(320)
        heading.addWidget(self.search_box)
        self.competition_filter = QComboBox()
        self.competition_filter.addItem("Todas las competiciones", None)
        for competition in COMPETITIONS:
            self.competition_filter.addItem(competition.name, competition.code)
        self.competition_filter.currentIndexChanged.connect(self.refresh)
        heading.addWidget(self.competition_filter)
        list_layout.addLayout(heading)

        panel = QFrame(objectName="panel")
        panel_layout = QVBoxLayout(panel)
        self.teams_table = self._table(
            ["Equipo", "País", "Competiciones", "Partidos", "Último partido", "Detalle"]
        )
        self.teams_table.cellDoubleClicked.connect(self._open_selected)
        panel_layout.addWidget(self.teams_table)
        list_layout.addWidget(panel, 1)
        self.stack.addWidget(self.list_page)

        self.detail_page = QWidget()
        detail_layout = QVBoxLayout(self.detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        back = QPushButton("← Equipos")
        back.clicked.connect(lambda: self.stack.setCurrentWidget(self.list_page))
        header.addWidget(back)
        self.detail_title = QLabel("", objectName="sectionTitle")
        header.addWidget(self.detail_title)
        header.addStretch(1)
        detail_layout.addLayout(header)
        self.detail_tabs = QTabWidget()
        summary_page = QWidget()
        summary_layout = QVBoxLayout(summary_page)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        summary_layout.addWidget(self.summary_label)
        summary_layout.addStretch(1)
        self.detail_tabs.addTab(summary_page, "Resumen")

        history_panel = QFrame(objectName="panel")
        history_layout = QVBoxLayout(history_panel)
        self.history_table = self._table(
            ["Fecha", "Competición", "Temporada", "Localía", "Rival", "Resultado", "Estado"]
        )
        history_layout.addWidget(self.history_table)
        self.detail_tabs.addTab(history_panel, "Partidos")

        performance_page = QWidget()
        performance_layout = QVBoxLayout(performance_page)
        self.performance_label = QLabel("")
        self.performance_label.setWordWrap(True)
        self.performance_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        performance_layout.addWidget(self.performance_label)
        performance_layout.addStretch(1)
        self.detail_tabs.addTab(performance_page, "Rendimiento")
        detail_layout.addWidget(self.detail_tabs, 1)
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
                teams = TeamService(session).search(
                    self.search_box.text(), self.competition_filter.currentData()
                )
        except SQLAlchemyError as error:
            self._database_error(error)
            return
        self.teams_table.setRowCount(0)
        for team in teams:
            row = self.teams_table.rowCount()
            self.teams_table.insertRow(row)
            latest = (
                team.latest_match_at.astimezone(self.timezone).strftime("%d/%m/%Y")
                if team.latest_match_at
                else "—"
            )
            values = [
                team.name,
                team.country_name or "—",
                str(team.competition_count),
                str(team.match_count),
                latest,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (2, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, team.team_id)
                self.teams_table.setItem(row, column, item)
            button = QPushButton("Abrir")
            button.clicked.connect(
                lambda _checked=False, team_id=team.team_id: self.open_detail(team_id)
            )
            self.teams_table.setCellWidget(row, 5, button)
        if not teams:
            self._empty_table(self.teams_table, "No se encontraron equipos.")

    def _open_selected(self, row: int, _column: int) -> None:
        item = self.teams_table.item(row, 0)
        team_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if team_id is not None:
            self.open_detail(team_id)

    def open_detail(self, team_id: int) -> None:
        try:
            with self.session_factory() as session:
                profile = TeamService(session).get_profile(team_id)
        except SQLAlchemyError as error:
            self._database_error(error)
            return

        self.detail_title.setText(profile.name)
        self.summary_label.setText(self._summary_text(profile.metrics, profile.country_name))
        self.performance_label.setText(self._performance_text(profile.metrics))
        self.history_table.setRowCount(0)
        for match in profile.match_history:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            local_date = match.kickoff_utc.astimezone(self.timezone).strftime("%d/%m/%Y %H:%M")
            score = (
                f"{match.goals_for} – {match.goals_against}"
                if match.goals_for is not None and match.goals_against is not None
                else "—"
            )
            values = [
                local_date,
                match.competition_name,
                match.season_label,
                match.venue,
                match.opponent,
                score,
                self._status(match.status),
            ]
            for column, value in enumerate(values):
                self.history_table.setItem(row, column, QTableWidgetItem(value))
        if not profile.match_history:
            self._empty_table(
                self.history_table, "Todavía no hay partidos asociados a este equipo."
            )
        self.stack.setCurrentWidget(self.detail_page)

    @staticmethod
    def _summary_text(metrics: TeamMetrics, country: str | None) -> str:
        return (
            f"{country or 'País no registrado'}\n\n"
            f"Partidos jugados: {metrics.played}\n"
            f"Ganados: {metrics.won} · Empatados: {metrics.drawn} · Perdidos: {metrics.lost}\n"
            f"Goles a favor: {metrics.goals_for} · Goles en contra: {metrics.goals_against}\n"
            f"Diferencia de gol: {metrics.goal_difference} · Puntos: {metrics.points}"
        )

    @staticmethod
    def _performance_text(metrics: TeamMetrics) -> str:
        def percent(value: float | None) -> str:
            return f"{value:.1f}%" if value is not None else "—"

        average_for = (
            f"{metrics.average_goals_for:.2f}" if metrics.average_goals_for is not None else "—"
        )
        average_against = (
            f"{metrics.average_goals_against:.2f}"
            if metrics.average_goals_against is not None
            else "—"
        )
        return (
            f"Victorias: {percent(metrics.win_percent)} · "
            f"Empates: {percent(metrics.draw_percent)} · "
            f"Derrotas: {percent(metrics.loss_percent)}\n"
            f"Promedio de goles a favor: {average_for} · En contra: {average_against}\n"
            f"Forma últimos 5: {metrics.form_last_5 or '—'} · "
            f"Últimos 10: {metrics.form_last_10 or '—'}\n"
            f"Rendimiento local: {metrics.home_points}/{metrics.home_played * 3} puntos · "
            f"Visitante: {metrics.away_points}/{metrics.away_played * 3} puntos\n"
            f"Vallas invictas: {metrics.clean_sheets} · Sin marcar: {metrics.failed_to_score}\n"
            f"Racha invicto: {metrics.unbeaten_streak} · Victorias: {metrics.win_streak} · "
            f"Marcando: {metrics.scoring_streak}"
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
        table = (
            self.teams_table if self.stack.currentWidget() == self.list_page else self.history_table
        )
        self._empty_table(table, message)
