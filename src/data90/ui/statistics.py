"""Competition-level statistical leaders calculated from completed local matches."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from data90.services.competition_service import CompetitionService
from data90.services.statistics_service import StatisticsService
from data90.ui.home import CrestLoader


class StatisticsPage(QWidget):
    team_selected = Signal(int)

    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.crest_loader = CrestLoader(self)
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
            "Temporada seleccionada · estadísticas según partidos con datos publicados.",
            objectName="muted",
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        panel = QFrame(objectName="panel")
        panel_layout = QVBoxLayout(panel)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Área", "Métrica", "Equipo líder", "Valor"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        panel_layout.addWidget(self.table, 2)
        self.match_stats_title = QLabel("Tarjetas y córners", objectName="sectionTitle")
        panel_layout.addWidget(self.match_stats_title)
        self.match_stats_table = QTableWidget(0, 8)
        self.match_stats_table.setHorizontalHeaderLabels(
            [
                "Métrica",
                "Equipo",
                "Total",
                "Local",
                "Visitante",
                "Prom./PJ",
                "% partidos",
                "PJ con dato",
            ]
        )
        match_stats_header = self.match_stats_table.horizontalHeader()
        match_stats_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        match_stats_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 8):
            match_stats_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.match_stats_table.verticalHeader().setVisible(False)
        self.match_stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.match_stats_table.setAlternatingRowColors(True)
        panel_layout.addWidget(self.match_stats_table, 1)
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
        self.match_stats_table.setRowCount(0)
        if season_id is None:
            self._empty("No hay temporadas cargadas para las competiciones.")
            return
        try:
            with self.session_factory() as session:
                service = StatisticsService(session)
                teams = service.for_competition_season(season_id)
                match_stats = service.match_stat_summaries_for_competition_season(
                    season_id
                )
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
        team_column_width = 0
        for index, (area, metric, team, value) in enumerate(rows):
            for column, text in enumerate((area, metric, team.team_name, value(team))):
                if column == 2:
                    button = QPushButton(team.team_name, objectName="clubLink")
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    button.setToolTip("Abrir ficha del equipo")
                    self.crest_loader.apply(button, team.crest_url, team.team_name)
                    button.clicked.connect(
                        lambda _checked=False, team_id=team.team_id: self.team_selected.emit(
                            team_id
                        )
                    )
                    team_column_width = max(
                        team_column_width, button.sizeHint().width()
                    )
                    self.table.setCellWidget(index, column, button)
                    continue
                item = QTableWidgetItem(text)
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(index, column, item)
        self.table.setColumnWidth(2, max(200, team_column_width + 8))
        self.note.setText(f"{len(played)} equipos con partidos finalizados.")
        self._populate_match_stats_table(played, match_stats)

    def _populate_match_stats_table(self, teams, summaries_by_team) -> None:
        definitions = (
            ("yellow_cards", "Tarjetas amarillas"),
            ("red_cards", "Tarjetas rojas"),
            ("corners", "Córners"),
        )
        teams_by_id = {team.team_id: team for team in teams}
        rows = []
        for field, label in definitions:
            available = [
                (teams_by_id[team_id], getattr(summary, field))
                for team_id, summary in summaries_by_team.items()
                if team_id in teams_by_id
                and getattr(summary, field).total is not None
            ]
            if available:
                rows.append(
                    (label, max(available, key=lambda item: item[1].total))
                )

        if not rows:
            self.match_stats_table.setRowCount(1)
            item = QTableWidgetItem("Sin datos publicados")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.match_stats_table.setItem(0, 0, item)
            self.match_stats_table.setSpan(0, 0, 1, self.match_stats_table.columnCount())
            return

        self.match_stats_table.setRowCount(len(rows))
        team_column_width = 0
        for row_index, (label, (team, summary)) in enumerate(rows):
            self.match_stats_table.setItem(row_index, 0, QTableWidgetItem(label))
            button = QPushButton(team.team_name, objectName="clubLink")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip("Abrir ficha del equipo")
            self.crest_loader.apply(button, team.crest_url, team.team_name)
            button.clicked.connect(
                lambda _checked=False, team_id=team.team_id: self.team_selected.emit(
                    team_id
                )
            )
            team_column_width = max(team_column_width, button.sizeHint().width())
            self.match_stats_table.setCellWidget(row_index, 1, button)
            values = (
                str(summary.total),
                str(summary.home_total) if summary.home_total is not None else "—",
                str(summary.away_total) if summary.away_total is not None else "—",
                self._decimal(summary.average_per_match),
                self._percent(summary.matches_with_event_percent),
                str(summary.matches_with_data),
            )
            for column, value in enumerate(values, start=2):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.match_stats_table.setItem(row_index, column, item)
        self.match_stats_table.setColumnWidth(1, max(200, team_column_width + 8))

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
