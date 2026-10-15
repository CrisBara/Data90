"""Team search, season-independent metrics, and local match history."""

from datetime import date
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from data90.analytics.team_metrics import TeamMetrics
from data90.config.constants import COMPETITIONS
from data90.config.settings import get_settings
from data90.services.team_service import TeamService
from data90.ui.home import CrestLoader
from data90.ui.match_labels import match_datetime_label


class TeamsPage(QWidget):
    team_selected = Signal(int)
    player_selected = Signal(int)
    back_requested = Signal()

    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.timezone = ZoneInfo(get_settings().app_timezone)
        self.crest_loader = CrestLoader(self)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh)
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
        self.search_box.textChanged.connect(lambda _text: self._search_timer.start())
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
        self.teams_table.setColumnWidth(5, 90)
        self.teams_table.cellClicked.connect(self._open_selected)
        panel_layout.addWidget(self.teams_table)
        list_layout.addWidget(panel, 1)
        self.stack.addWidget(self.list_page)

        self.detail_page = QWidget()
        detail_layout = QVBoxLayout(self.detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        back = QPushButton("← Equipos")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        self.detail_crest_button = QPushButton()
        self.detail_crest_button.setObjectName("detailTeamCrest")
        self.detail_crest_button.setProperty("crestSize", 36)
        self.detail_crest_button.clicked.connect(self._open_current_team)
        header.addWidget(self.detail_crest_button)
        self.detail_title = QLabel("", objectName="sectionTitle")
        header.addWidget(self.detail_title, 1)
        self.season_selector = QComboBox()
        self.season_selector.currentIndexChanged.connect(self._season_changed)
        header.addWidget(self.season_selector)
        header.addStretch(1)
        detail_layout.addLayout(header)
        self.detail_tabs = QTabWidget()
        summary_page = QWidget()
        self._summary_page = summary_page
        summary_layout = QVBoxLayout(summary_page)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        summary_layout.addWidget(self.summary_label)
        summary_layout.addStretch(1)
        self.detail_tabs.addTab(summary_page, "Resumen")

        squad_panel = QFrame(objectName="panel")
        squad_layout = QVBoxLayout(squad_panel)
        self.squad_table = self._table(["Jugador", "Nacionalidad", "Posición", "Edad"])
        self.squad_table.itemClicked.connect(self._open_player)
        squad_layout.addWidget(self.squad_table)
        self.detail_tabs.addTab(squad_panel, "Plantel")

        history_panel = QFrame(objectName="panel")
        history_layout = QVBoxLayout(history_panel)
        self.history_table = self._table(
            ["Fecha", "Competición", "Temporada", "Localía", "Rival", "Resultado", "Estado"]
        )
        self.history_table.itemClicked.connect(self._open_opponent)
        history_layout.addWidget(self.history_table)
        self.detail_tabs.addTab(history_panel, "Partidos")

        schedule_page = QWidget()
        schedule_layout = QVBoxLayout(schedule_page)
        schedule_layout.addWidget(QLabel("Últimos 5 partidos", objectName="sectionTitle"))
        self.recent_table = self._table(["Fecha", "Jornada", "Rival", "Localía", "Resultado"])
        self.recent_table.itemClicked.connect(self._open_schedule_team)
        schedule_layout.addWidget(self.recent_table, 1)
        schedule_layout.addWidget(QLabel("Próximos 5 partidos", objectName="sectionTitle"))
        self.upcoming_table = self._table(["Fecha", "Jornada", "Rival", "Localía", "Estado"])
        self.upcoming_table.itemClicked.connect(self._open_schedule_team)
        schedule_layout.addWidget(self.upcoming_table, 1)
        self.detail_tabs.addTab(schedule_page, "Calendario")

        performance_page = QWidget()
        performance_layout = QVBoxLayout(performance_page)
        performance_layout.setContentsMargins(14, 14, 14, 14)
        performance_layout.setSpacing(12)
        self.performance_dashboard = QWidget()
        dashboard_layout = QVBoxLayout(self.performance_dashboard)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setSpacing(12)
        dashboard_header = QHBoxLayout()
        dashboard_titles = QVBoxLayout()
        dashboard_title = QLabel("Rendimiento de temporada", objectName="performanceHeading")
        dashboard_subtitle = QLabel(
            "Resultados, forma reciente y balance de localía",
            objectName="muted",
        )
        dashboard_titles.addWidget(dashboard_title)
        dashboard_titles.addWidget(dashboard_subtitle)
        dashboard_header.addLayout(dashboard_titles)
        dashboard_header.addStretch(1)
        self.performance_played_label = QLabel("— partidos", objectName="performanceCount")
        dashboard_header.addWidget(self.performance_played_label)
        dashboard_layout.addLayout(dashboard_header)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self.performance_kpis: dict[str, tuple[QLabel, QLabel]] = {}
        for key, title, tone in (
            ("win", "Victorias", "win"),
            ("draw", "Empates", "draw"),
            ("loss", "Derrotas", "loss"),
        ):
            card, value_label, detail_label = self._performance_card(title, tone)
            self.performance_kpis[key] = (value_label, detail_label)
            kpi_row.addWidget(card, 1)
        dashboard_layout.addLayout(kpi_row)

        detail_grid = QGridLayout()
        detail_grid.setHorizontalSpacing(10)
        detail_grid.setVerticalSpacing(10)
        goals_card, _, _ = self._performance_card("Goles por partido", "neutral")
        goals_layout = goals_card.layout()
        goals_row = QHBoxLayout()
        self.performance_goals_for = QLabel("—", objectName="performanceValueFor")
        self.performance_goals_against = QLabel("—", objectName="performanceValueAgainst")
        goals_row.addWidget(self._performance_stat_column("A favor", self.performance_goals_for))
        divider = QFrame()
        divider.setObjectName("performanceDivider")
        divider.setFixedWidth(1)
        goals_row.addWidget(divider)
        goals_row.addWidget(
            self._performance_stat_column("En contra", self.performance_goals_against)
        )
        goals_layout.addLayout(goals_row)
        detail_grid.addWidget(goals_card, 0, 0)

        form_card, _, _ = self._performance_card("Forma reciente", "neutral")
        form_layout = form_card.layout()
        self.performance_form_badges: dict[str, list[QLabel]] = {}
        for key, label, badge_count in (("last5", "Últimos 5", 5), ("last10", "Últimos 10", 10)):
            form_row = QHBoxLayout()
            form_row.addWidget(QLabel(label, objectName="performanceMeta"))
            form_row.addStretch(1)
            badges: list[QLabel] = []
            for _ in range(badge_count):
                badge = QLabel("—", objectName="formBadge")
                badge.setProperty("outcome", "empty")
                badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                badge.setFixedSize(25, 26)
                badges.append(badge)
                form_row.addWidget(badge)
            self.performance_form_badges[key] = badges
            form_layout.addLayout(form_row)
        detail_grid.addWidget(form_card, 0, 1)

        venue_card, _, _ = self._performance_card("Balance por localía", "neutral")
        venue_layout = venue_card.layout()
        venue_row = QHBoxLayout()
        self.performance_venue: dict[str, QLabel] = {}
        for key, title in (("home", "Local"), ("away", "Visitante")):
            column = QVBoxLayout()
            column.addWidget(QLabel(title, objectName="performanceMeta"))
            summary = QLabel("—", objectName="performanceVenueSummary")
            points = QLabel("—", objectName="performanceMeta")
            column.addWidget(summary)
            column.addWidget(points)
            self.performance_venue[key] = summary
            self.performance_venue[f"{key}_points"] = points
            venue_row.addLayout(column, 1)
        venue_layout.addLayout(venue_row)
        detail_grid.addWidget(venue_card, 0, 2)
        dashboard_layout.addLayout(detail_grid)

        streak_grid = QGridLayout()
        streak_grid.setHorizontalSpacing(10)
        self.performance_streaks: dict[str, QLabel] = {}
        for column, (key, title) in enumerate(
            (
                ("clean", "Vallas invictas"),
                ("failed", "Sin marcar"),
                ("unbeaten", "Racha invicto"),
                ("wins", "Victorias seguidas"),
                ("scoring", "Partidos marcando"),
            )
        ):
            card, value_label, _ = self._performance_card(title, "compact")
            value_label.setObjectName("performanceCompactValue")
            self.performance_streaks[key] = value_label
            streak_grid.addWidget(card, 0, column)
        dashboard_layout.addLayout(streak_grid)
        performance_layout.addWidget(self.performance_dashboard)
        performance_layout.addStretch(1)
        self.detail_tabs.addTab(performance_page, "Rendimiento")

        match_stats_panel = QFrame(objectName="panel")
        match_stats_layout = QVBoxLayout(match_stats_panel)
        self.match_stats_table = self._table(["Estadística", "Promedio por partido", "Partidos"])
        match_stats_layout.addWidget(self.match_stats_table)
        self.totals_table = self._table(["Estadística", "Total", "Local", "Visitante", "Cobertura"])
        match_stats_layout.addWidget(self.totals_table)
        self.combined_match_stats_table = self._table(
            ["Estadística", "Promedio", "Total", "Local", "Visitante", "PJ con datos"]
        )
        self.match_stats_table.hide()
        self.totals_table.hide()
        self._configure_combined_match_stats_table()
        match_stats_layout.addWidget(self.combined_match_stats_table)
        self.detail_tabs.addTab(match_stats_panel, "Estadísticas de partidos")

        self.classics_page = QWidget()
        classics_layout = QVBoxLayout(self.classics_page)
        classics_layout.setContentsMargins(4, 8, 4, 4)
        self.classics_scroll = QScrollArea()
        self.classics_scroll.setWidgetResizable(True)
        self.classics_content = QWidget()
        self.classics_content_layout = QVBoxLayout(self.classics_content)
        self.classics_content_layout.setContentsMargins(4, 4, 4, 4)
        self.classics_content_layout.setSpacing(12)
        self.classics_content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.classics_scroll.setWidget(self.classics_content)
        classics_layout.addWidget(self.classics_scroll)
        self.detail_tabs.addTab(self.classics_page, "Clásicos")
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
                    item.setForeground(QColor("#79cdeb"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.teams_table.setItem(row, column, item)
            button = QPushButton("Abrir")
            button.setMinimumWidth(76)
            button.setMinimumHeight(30)
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

    def open_detail(self, team_id: int, competition_season_id: int | None = None) -> None:
        self._team_id = team_id
        self._loading_profile = True
        try:
            with self.session_factory() as session:
                service = TeamService(session)
                profile = service.get_profile(team_id, competition_season_id)
                selected_competition_code = next(
                    (
                        option.competition_code
                        for option in profile.competitions
                        if option.competition_season_id == profile.selected_competition_season_id
                    ),
                    None,
                )
                supports_classics = selected_competition_code in {
                    "PL",
                    "LALIGA",
                    "SERIE_A",
                    "BUNDESLIGA",
                    "LIGUE_1",
                    "EREDIVISIE",
                }
                rivalry_sections = (
                    service.get_rivalry_sections(team_id, selected_competition_code)
                    if supports_classics and selected_competition_code
                    else ()
                )
        except SQLAlchemyError as error:
            self._database_error(error)
            return

        self.detail_title.setText(
            f"{profile.name} · Entrenador: {profile.coach_name or 'No informado'}"
        )
        self.crest_loader.apply(self.detail_crest_button, profile.crest_url, profile.name)
        uses_league_team_layout = any(
            option.competition_season_id == profile.selected_competition_season_id
            and option.competition_code
            in {
                "PL",
                "BUNDESLIGA",
                "EREDIVISIE",
                "LALIGA",
                "LIGUE_1",
                "SERIE_A",
                "UCL",
                "UEL",
            }
            for option in profile.competitions
        )
        self.performance_dashboard.setVisible(True)
        self.match_stats_table.hide()
        self.totals_table.hide()
        self.combined_match_stats_table.show()
        self._configure_match_columns(uses_league_team_layout)
        summary_index = self.detail_tabs.indexOf(self._summary_page)
        if uses_league_team_layout and summary_index >= 0:
            self.detail_tabs.removeTab(summary_index)
        elif not uses_league_team_layout and summary_index < 0:
            self.detail_tabs.insertTab(0, self._summary_page, "Resumen")
        self.season_selector.blockSignals(True)
        self.season_selector.clear()
        for option in profile.competitions:
            self.season_selector.addItem(option.label, option.competition_season_id)
        selected_index = self.season_selector.findData(profile.selected_competition_season_id)
        self.season_selector.setCurrentIndex(selected_index)
        self.season_selector.setVisible(bool(profile.competitions))
        self.season_selector.blockSignals(False)
        classics_index = self.detail_tabs.indexOf(self.classics_page)
        self.detail_tabs.setTabVisible(classics_index, supports_classics)
        self._fill_classics(
            rivalry_sections,
            team_id=profile.team_id,
            team_name=profile.name,
            team_crest_url=profile.crest_url,
        )
        self._loading_profile = False
        self.detail_tabs.setCurrentIndex(0)
        self.summary_label.setText(
            self._summary_text(
                profile.metrics,
                profile.country_name,
                profile.standing_position,
                profile.standing_points,
            )
        )
        self._update_performance_dashboard(profile.metrics)
        self.squad_table.setRowCount(0)
        if profile.squad:
            self.squad_table.setRowCount(len(profile.squad))
            for row, player in enumerate(profile.squad):
                values = (
                    player.player_name,
                    player.nationality or "No informada",
                    player.position or "No informada",
                    str(self._age(player.birth_date)) if player.birth_date else "No informada",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, player.player_id)
                        item.setForeground(QColor("#69aef7"))
                        font = item.font()
                        font.setUnderline(True)
                        font.setBold(True)
                        item.setFont(font)
                        item.setToolTip("Abrir ficha del jugador")
                    self.squad_table.setItem(row, column, item)
        else:
            self._empty_table(
                self.squad_table,
                "No hay jugadores de plantel cargados para este equipo y temporada.",
            )
        self.match_stats_table.setRowCount(0)
        for label, average, count in profile.match_stat_averages:
            row = self.match_stats_table.rowCount()
            self.match_stats_table.insertRow(row)
            displayed_average = f"{average:.1f}%" if label == "Posesión (%)" else f"{average:.1f}"
            for column, value in enumerate((label, displayed_average, str(count))):
                self.match_stats_table.setItem(row, column, QTableWidgetItem(value))
        if not profile.match_stat_averages:
            self._empty_table(
                self.match_stats_table,
                "Todavía no hay estadísticas de partidos para la temporada actual.",
            )
        self.totals_table.setRowCount(0)
        for metric in profile.stat_totals:
            row = self.totals_table.rowCount()
            self.totals_table.insertRow(row)
            coverage = (
                f"{metric.matches_covered}/{metric.matches_played} partidos"
                if metric.matches_played
                else "Sin partidos finalizados"
            )
            values = (
                metric.metric,
                self._unknown(metric.total),
                self._unknown(metric.home),
                self._unknown(metric.away),
                coverage,
            )
            for column, value in enumerate(values):
                self.totals_table.setItem(row, column, QTableWidgetItem(value))
        self._fill_combined_match_stats_table(profile.match_stat_averages, profile.stat_totals)
        self.history_table.setRowCount(0)
        for match in profile.match_history:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            local_date = match_datetime_label(match.kickoff_utc, match.status, self.timezone)
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
                if column == 4:
                    button = QPushButton(match.opponent, objectName="clubLink")
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    self.crest_loader.apply(button, match.opponent_crest_url, match.opponent)
                    button.clicked.connect(
                        lambda _checked=False, selected_id=match.opponent_id: (
                            self.team_selected.emit(selected_id)
                        )
                    )
                    self.history_table.setCellWidget(row, column, button)
                    continue
                item = QTableWidgetItem(value)
                if column == 4:
                    item.setData(Qt.ItemDataRole.UserRole, match.opponent_id)
                    item.setForeground(QColor("#69aef7"))
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                    item.setToolTip("Abrir ficha del equipo")
                if column == 5:
                    self._style_result_item(item, match, uses_league_team_layout)
                self.history_table.setItem(row, column, item)
        if not profile.match_history:
            self._empty_table(
                self.history_table, "Todavía no hay partidos asociados a este equipo."
            )
        self._fill_schedule(
            self.recent_table,
            profile.recent_matches,
            upcoming=False,
            use_league_layout=uses_league_team_layout,
        )
        self._fill_schedule(
            self.upcoming_table,
            profile.upcoming_matches,
            upcoming=True,
            use_league_layout=uses_league_team_layout,
        )
        self.stack.setCurrentWidget(self.detail_page)

    def _configure_combined_match_stats_table(self) -> None:
        """Fit averages and home/away card and corner totals into one table."""
        header = self.combined_match_stats_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        widths = {1: 110, 2: 85, 3: 85, 4: 100, 5: 110}
        for column, width in widths.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.combined_match_stats_table.setColumnWidth(column, width)
        self.combined_match_stats_table.verticalHeader().setDefaultSectionSize(34)
        self.combined_match_stats_table.setWordWrap(False)

    def _fill_combined_match_stats_table(self, averages, totals) -> None:
        """Render competition match metrics and card/corner breakdowns together."""
        table = self.combined_match_stats_table
        table.setRowCount(0)
        rows: dict[str, int] = {}
        for label, average, count in averages:
            row = table.rowCount()
            table.insertRow(row)
            rows[label] = row
            average_text = f"{average:.1f}%" if label == "Posesión (%)" else f"{average:.1f}"
            values = (label, average_text, "—", "—", "—", str(count))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, column, item)

        aliases = {
            "Amarillas": "Tarjetas amarillas",
            "Rojas": "Tarjetas rojas",
            "Córners": "Córners",
        }
        for metric in totals:
            label = aliases.get(metric.metric, metric.metric)
            row = rows.get(label)
            if row is None:
                row = table.rowCount()
                table.insertRow(row)
                rows[label] = row
                table.setItem(row, 0, QTableWidgetItem(label))
                table.setItem(row, 1, QTableWidgetItem("—"))
            covered = (
                f"{metric.matches_covered}/{metric.matches_played}"
                if metric.matches_played
                else "—"
            )
            totals_values = (
                self._unknown(metric.total),
                self._unknown(metric.home),
                self._unknown(metric.away),
                covered,
            )
            for column, value in enumerate(totals_values, start=2):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, column, item)

        if not table.rowCount():
            self._empty_table(table, "Todavía no hay estadísticas de partidos para esta temporada.")

    def _fill_classics(
        self,
        rivalries,
        *,
        team_id: int,
        team_name: str,
        team_crest_url: str | None,
    ) -> None:
        while self.classics_content_layout.count():
            item = self.classics_content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not rivalries:
            empty = QLabel(
                "No hay derbis o clásicos verificados para este equipo "
                "en la competición seleccionada. "
                "Los historiales se muestran cuando existen partidos cargados en la base.",
                objectName="muted",
            )
            empty.setWordWrap(True)
            self.classics_content_layout.addWidget(empty)
            self.classics_content_layout.addStretch(1)
            return

        for rivalry in rivalries:
            card = QFrame(objectName="panel")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 14)
            card_layout.setSpacing(9)

            identity_row = QHBoxLayout()
            category = QLabel(rivalry.category, objectName="rivalryCategory")
            identity_row.addWidget(category)
            identity_row.addSpacing(8)
            rival_button = QPushButton(rivalry.rival_name, objectName="clubLink")
            rival_button.setProperty("crestSize", 32)
            rival_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.crest_loader.apply(rival_button, rivalry.rival_crest_url, rivalry.rival_name)
            if rivalry.rival_id is not None:
                rival_button.clicked.connect(
                    lambda _checked=False, selected_id=rivalry.rival_id: (
                        self.team_selected.emit(selected_id)
                    )
                )
            else:
                rival_button.setEnabled(False)
            identity_row.addWidget(rival_button)
            identity_row.addStretch(1)
            identity_row.addWidget(QLabel("Últimos cinco enfrentamientos", objectName="muted"))
            card_layout.addLayout(identity_row)

            history_table = self._rivalry_history_table(
                rivalry.matches,
                team_id=team_id,
                team_name=team_name,
                team_crest_url=team_crest_url,
            )
            card_layout.addWidget(history_table)
            self.classics_content_layout.addWidget(card)
        self.classics_content_layout.addStretch(1)

    def _rivalry_history_table(
        self,
        matches,
        *,
        team_id: int,
        team_name: str,
        team_crest_url: str | None,
    ) -> QTableWidget:
        table = self._table(["Fecha", "Jornada", "Local", "Resultado", "Visitante"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setDefaultSectionSize(32)
        table.setMinimumHeight(150)
        table.setWordWrap(False)
        for match in matches:
            row = table.rowCount()
            table.insertRow(row)
            kickoff = match.kickoff_utc.astimezone(self.timezone).strftime("%d/%m/%Y")
            round_name = match.round_label or (
                f"Jornada {match.matchday}" if match.matchday else match.stage or "—"
            )
            home_goals, away_goals = (
                (match.goals_for, match.goals_against)
                if match.venue == "Local"
                else (match.goals_against, match.goals_for)
            )
            result = (
                f"{home_goals} – {away_goals}"
                if home_goals is not None and away_goals is not None
                else "—"
            )
            home_team = (
                (team_id, team_name, team_crest_url)
                if match.venue == "Local"
                else (match.opponent_id, match.opponent, match.opponent_crest_url)
            )
            away_team = (
                (match.opponent_id, match.opponent, match.opponent_crest_url)
                if match.venue == "Local"
                else (team_id, team_name, team_crest_url)
            )
            for column, (club_id, club_name, crest_url) in (
                (2, home_team),
                (4, away_team),
            ):
                button = QPushButton(club_name, objectName="clubLink")
                button.setProperty("crestSize", 22)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                self.crest_loader.apply(button, crest_url, club_name)
                button.clicked.connect(
                    lambda _checked=False, selected_id=club_id: self.team_selected.emit(selected_id)
                )
                table.setCellWidget(row, column, button)

            values = (kickoff, round_name, "", result, "")
            for column, value in enumerate(values):
                if column in (2, 4):
                    continue
                item = QTableWidgetItem(value)
                if column in (0, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 3:
                    self._style_result_item(item, match, True)
                table.setItem(row, column, item)
        if not matches:
            table.setRowCount(1)
            item = QTableWidgetItem("No hay enfrentamientos guardados en la base.")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(0, 0, item)
            table.setSpan(0, 0, 1, 5)
        return table

    def _configure_match_columns(self, use_league_layout: bool) -> None:
        """Use the league-style match layout for leagues and UEFA competitions."""
        history_header = self.history_table.horizontalHeader()
        schedule_headers = (
            self.recent_table.horizontalHeader(),
            self.upcoming_table.horizontalHeader(),
        )
        if not use_league_layout:
            for column in (0, 2, 3, 5, 6):
                history_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            history_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            history_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
            for header in schedule_headers:
                header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
                header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
                header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
                header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
                header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            return

        # Keep metadata compact and give the full opponent name the remaining room.
        history_widths = {0: 145, 1: 155, 2: 100, 3: 90, 5: 90, 6: 105}
        for column, width in history_widths.items():
            history_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.history_table.setColumnWidth(column, width)
        history_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        schedule_widths = {0: 145, 1: 112, 3: 100, 4: 110}
        for table, header in zip(
            (self.recent_table, self.upcoming_table), schedule_headers, strict=True
        ):
            for column, width in schedule_widths.items():
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(column, width)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

    def _season_changed(self, _index: int) -> None:
        if getattr(self, "_loading_profile", False) or not getattr(self, "_team_id", None):
            return
        season_id = self.season_selector.currentData()
        if season_id is not None:
            self.open_detail(self._team_id, int(season_id))

    def _open_current_team(self) -> None:
        if getattr(self, "_team_id", None) is not None:
            self.team_selected.emit(int(self._team_id))

    def _open_opponent(self, item: QTableWidgetItem) -> None:
        if item.column() == 4:
            team_id = item.data(Qt.ItemDataRole.UserRole)
            if team_id is not None:
                self.team_selected.emit(int(team_id))

    def _open_player(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            player_id = item.data(Qt.ItemDataRole.UserRole)
            if player_id is not None:
                self.player_selected.emit(int(player_id))

    def _open_schedule_team(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            team_id = item.data(Qt.ItemDataRole.UserRole)
            if team_id is not None:
                self.team_selected.emit(int(team_id))

    def _fill_schedule(
        self,
        table: QTableWidget,
        matches: list,
        *,
        upcoming: bool,
        use_league_layout: bool,
    ) -> None:
        table.setRowCount(0)
        for match in matches:
            row = table.rowCount()
            table.insertRow(row)
            values = (
                match_datetime_label(match.kickoff_utc, match.status, self.timezone),
                match.round_label or (f"Jornada {match.matchday}" if match.matchday else "—"),
                match.opponent,
                match.venue,
                self._status(match.status)
                if upcoming
                else (
                    f"{match.goals_for} – {match.goals_against}"
                    if match.goals_for is not None and match.goals_against is not None
                    else "—"
                ),
            )
            for column, value in enumerate(values):
                if column == 2:
                    button = QPushButton(match.opponent, objectName="clubLink")
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    self.crest_loader.apply(button, match.opponent_crest_url, match.opponent)
                    button.clicked.connect(
                        lambda _checked=False, selected_id=match.opponent_id: (
                            self.team_selected.emit(selected_id)
                        )
                    )
                    table.setCellWidget(row, column, button)
                    continue
                cell = QTableWidgetItem(value)
                if column == 2:
                    cell.setData(Qt.ItemDataRole.UserRole, match.opponent_id)
                    cell.setForeground(QColor("#69aef7"))
                    font = cell.font()
                    font.setUnderline(True)
                    cell.setFont(font)
                if column == 4 and not upcoming:
                    self._style_result_item(cell, match, use_league_layout)
                table.setItem(row, column, cell)
        if not matches:
            self._empty_table(table, "No hay partidos disponibles para esta temporada.")

    @staticmethod
    def _style_result_item(item: QTableWidgetItem, match: object, use_league_layout: bool) -> None:
        if (
            not use_league_layout
            or match.status.upper() != "FINISHED"
            or match.goals_for is None
            or match.goals_against is None
        ):
            return
        if match.goals_for > match.goals_against:
            background, foreground, outcome = "#123d31", "#9ce4bd", "Victoria"
        elif match.goals_for < match.goals_against:
            background, foreground, outcome = "#48262f", "#ff9aa7", "Derrota"
        else:
            background, foreground, outcome = "#4a401c", "#f4d46a", "Empate"
        item.setBackground(QColor(background))
        item.setForeground(QColor(foreground))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setToolTip(f"{outcome} del equipo")

    @staticmethod
    def _age(birth_date: date) -> int:
        today = date.today()
        return (
            today.year
            - birth_date.year
            - ((today.month, today.day) < (birth_date.month, birth_date.day))
        )

    @staticmethod
    def _unknown(value: int | None) -> str:
        return str(value) if value is not None else "No informado"

    @staticmethod
    def _performance_card(title: str, tone: str) -> tuple[QFrame, QLabel, QLabel]:
        card = QFrame(objectName="performanceCard")
        card.setProperty("tone", tone)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 11, 14, 12)
        card_layout.setSpacing(5)
        title_label = QLabel(title, objectName="performanceCardTitle")
        value_label = QLabel("—", objectName="performanceValue")
        detail_label = QLabel("", objectName="performanceMeta")
        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)
        card_layout.addWidget(detail_label)
        return card, value_label, detail_label

    @staticmethod
    def _performance_stat_column(title: str, value_label: QLabel) -> QWidget:
        column = QWidget(objectName="performanceStatColumn")
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(2)
        column_layout.addWidget(QLabel(title, objectName="performanceMeta"))
        column_layout.addWidget(value_label)
        return column

    def _update_performance_dashboard(self, metrics: TeamMetrics) -> None:
        def percent(value: float | None) -> str:
            return f"{value:.1f}%" if value is not None else "—"

        self.performance_played_label.setText(
            f"{metrics.played} partidos analizados · {metrics.points} puntos"
        )
        for key, value, count, title in (
            ("win", metrics.win_percent, metrics.won, "victorias"),
            ("draw", metrics.draw_percent, metrics.drawn, "empates"),
            ("loss", metrics.loss_percent, metrics.lost, "derrotas"),
        ):
            value_label, detail_label = self.performance_kpis[key]
            value_label.setText(percent(value))
            detail_label.setText(f"{count} {title} · {metrics.played} partidos")

        average_for = (
            f"{metrics.average_goals_for:.2f}" if metrics.average_goals_for is not None else "—"
        )
        average_against = (
            f"{metrics.average_goals_against:.2f}"
            if metrics.average_goals_against is not None
            else "—"
        )
        self.performance_goals_for.setText(average_for)
        self.performance_goals_against.setText(average_against)

        for key, form in (("last5", metrics.form_last_5), ("last10", metrics.form_last_10)):
            badges = self.performance_form_badges[key]
            outcomes = list(form[-len(badges) :])
            for index, badge in enumerate(badges):
                if outcomes:
                    badge.setVisible(index >= len(badges) - len(outcomes))
                else:
                    badge.setVisible(index == 0)
                outcome = (
                    outcomes[index - (len(badges) - len(outcomes))]
                    if (outcomes and index >= len(badges) - len(outcomes))
                    else None
                )
                badge.setText({"W": "V", "D": "E", "L": "D"}.get(outcome, "—"))
                badge.setProperty(
                    "outcome",
                    {"W": "win", "D": "draw", "L": "loss"}.get(outcome, "empty"),
                )
                badge.style().unpolish(badge)
                badge.style().polish(badge)
                badge.setToolTip(
                    {"W": "Victoria", "D": "Empate", "L": "Derrota"}.get(
                        outcome, "Sin partidos registrados"
                    )
                )

        for venue, played, won, drawn, lost, points in (
            (
                "home",
                metrics.home_played,
                metrics.home_won,
                metrics.home_drawn,
                metrics.home_lost,
                metrics.home_points,
            ),
            (
                "away",
                metrics.away_played,
                metrics.away_won,
                metrics.away_drawn,
                metrics.away_lost,
                metrics.away_points,
            ),
        ):
            self.performance_venue[venue].setText(f"{won} V  ·  {drawn} E  ·  {lost} D")
            self.performance_venue[f"{venue}_points"].setText(
                f"{points} puntos · {played} partidos"
            )
        for key, value in (
            ("clean", metrics.clean_sheets),
            ("failed", metrics.failed_to_score),
            ("unbeaten", metrics.unbeaten_streak),
            ("wins", metrics.win_streak),
            ("scoring", metrics.scoring_streak),
        ):
            self.performance_streaks[key].setText(str(value))

    @staticmethod
    def _summary_text(
        metrics: TeamMetrics,
        country: str | None,
        position: int | None,
        standing_points: int | None,
    ) -> str:
        position_text = str(position) if position is not None else "No informada"
        points_text = str(standing_points) if standing_points is not None else str(metrics.points)
        return (
            f"{country or 'País no registrado'}\n\n"
            f"Posición actual: {position_text} · Puntos: {points_text}\n"
            f"Partidos jugados: {metrics.played}\n"
            f"Ganados: {metrics.won} · Empatados: {metrics.drawn} · Perdidos: {metrics.lost}\n"
            f"Goles a favor: {metrics.goals_for} · Goles en contra: {metrics.goals_against}\n"
            f"Diferencia de gol: {metrics.goal_difference}"
        )

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
        table = (
            self.teams_table if self.stack.currentWidget() == self.list_page else self.history_table
        )
        self._empty_table(table, message)
