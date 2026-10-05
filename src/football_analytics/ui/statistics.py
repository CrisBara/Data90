"""Competition-level statistical leaders calculated from completed local matches."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from football_analytics.services.competition_service import CompetitionService
from football_analytics.services.statistics_service import StatisticsService


class StatisticsPage(QWidget):
    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Estadísticas", objectName="sectionTitle"))
        heading.addStretch(1)
        heading.addWidget(QLabel("Competición"))
        self.competition_filter = QComboBox()
        self.competition_filter.currentIndexChanged.connect(self._load_seasons)
        heading.addWidget(self.competition_filter)
        heading.addWidget(QLabel("Temporada"))
        self.season_filter = QComboBox()
        self.season_filter.currentIndexChanged.connect(self.refresh)
        heading.addWidget(self.season_filter)
        layout.addLayout(heading)
        self.note = QLabel(
            "Cálculos hechos bajo demanda con partidos finalizados que tienen resultado completo.",
            objectName="muted",
        )
        layout.addWidget(self.note)
        panel = QFrame(objectName="panel")
        panel_layout = QVBoxLayout(panel)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Área", "Métrica", "Equipo líder", "Valor"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        panel_layout.addWidget(self.table)
        layout.addWidget(panel, 1)
        self._load_competitions()

    def _load_competitions(self) -> None:
        try:
            with self.session_factory() as session:
                competitions = CompetitionService(session).get_competitions()
        except SQLAlchemyError as error:
            self._database_error(error)
            return
        self.competition_filter.blockSignals(True)
        self.competition_filter.clear()
        for competition in competitions:
            if competition.latest_season:
                self.competition_filter.addItem(competition.name, competition.code)
        self.competition_filter.blockSignals(False)
        self._load_seasons()

    def _load_seasons(self, _index: int = -1) -> None:
        code = self.competition_filter.currentData()
        self.season_filter.blockSignals(True)
        self.season_filter.clear()
        if code:
            try:
                with self.session_factory() as session:
                    seasons = CompetitionService(session).get_seasons(code)
            except SQLAlchemyError as error:
                self.season_filter.blockSignals(False)
                self._database_error(error)
                return
            for season in seasons:
                self.season_filter.addItem(season.label, season.competition_season_id)
        self.season_filter.blockSignals(False)
        self.refresh()

    def refresh(self, _index: int = -1) -> None:
        season_id = self.season_filter.currentData()
        self.table.setRowCount(0)
        if season_id is None:
            self._empty("No hay temporadas cargadas para las competiciones.")
            return
        try:
            with self.session_factory() as session:
                teams = StatisticsService(session).for_competition_season(season_id)
        except SQLAlchemyError as error:
            self._database_error(error)
            return
        played = [team for team in teams if team.metrics.played > 0]
        if not played:
            self._empty(
                "No hay suficientes partidos finalizados con resultado para calcular métricas."
            )
            return

        rows = [
            (
                "Rendimiento general",
                "Más puntos",
                max(played, key=lambda row: row.metrics.points),
                lambda r: str(r.metrics.points),
            ),
            (
                "Rendimiento general",
                "Mayor % victorias",
                max(played, key=lambda row: row.metrics.win_percent or 0),
                lambda r: self._percent(r.metrics.win_percent),
            ),
            (
                "Ataque",
                "Más goles a favor",
                max(played, key=lambda row: row.metrics.goals_for),
                lambda r: str(r.metrics.goals_for),
            ),
            (
                "Ataque",
                "Mejor promedio de gol",
                max(played, key=lambda row: row.metrics.average_goals_for or 0),
                lambda r: self._decimal(r.metrics.average_goals_for),
            ),
            (
                "Defensa",
                "Menos goles recibidos",
                min(played, key=lambda row: row.metrics.goals_against),
                lambda r: str(r.metrics.goals_against),
            ),
            (
                "Defensa",
                "Más vallas invictas",
                max(played, key=lambda row: row.metrics.clean_sheets),
                lambda r: str(r.metrics.clean_sheets),
            ),
            (
                "Forma",
                "Mayor racha invicto",
                max(played, key=lambda row: row.metrics.unbeaten_streak),
                lambda r: str(r.metrics.unbeaten_streak),
            ),
            (
                "Forma",
                "Mayor racha de victorias",
                max(played, key=lambda row: row.metrics.win_streak),
                lambda r: str(r.metrics.win_streak),
            ),
        ]
        home_teams = [team for team in played if team.metrics.home_played]
        away_teams = [team for team in played if team.metrics.away_played]
        if home_teams:
            rows.append(
                (
                    "Local / visitante",
                    "Más puntos como local",
                    max(home_teams, key=lambda row: row.metrics.home_points),
                    lambda r: str(r.metrics.home_points),
                )
            )
        if away_teams:
            rows.append(
                (
                    "Local / visitante",
                    "Más puntos como visitante",
                    max(away_teams, key=lambda row: row.metrics.away_points),
                    lambda r: str(r.metrics.away_points),
                )
            )

        self.table.setRowCount(len(rows))
        for index, (area, metric, team, value) in enumerate(rows):
            for column, text in enumerate((area, metric, team.team_name, value(team))):
                item = QTableWidgetItem(text)
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(index, column, item)
        self.note.setText(f"{len(played)} equipos con partidos finalizados · cálculos bajo demanda")

    @staticmethod
    def _percent(value: float | None) -> str:
        return f"{value:.1f}%" if value is not None else "—"

    @staticmethod
    def _decimal(value: float | None) -> str:
        return f"{value:.2f}" if value is not None else "—"

    def _empty(self, message: str) -> None:
        self.table.setRowCount(1)
        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(0, 0, item)
        self.table.setSpan(0, 0, 1, self.table.columnCount())

    def _database_error(self, error: Exception) -> None:
        self._empty(f"Base local no disponible ({error.__class__.__name__}). Revisá PostgreSQL.")
