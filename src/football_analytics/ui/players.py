"""Player directory, season statistics, and individual profiles."""

from datetime import date
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from football_analytics.config.settings import get_settings
from football_analytics.repositories.players import (
    PlayerProfileDetail,
    PlayerSeasonDetail,
    goal_events_for_competition_season,
    player_directory,
    player_profile_detail,
    player_roster_for_competition_season,
    player_statistics_for_competition_season,
)
from football_analytics.services.competition_service import CompetitionService
from football_analytics.ui.home import CrestLoader


class PlayersPage(QWidget):
    player_selected = Signal(int)
    team_selected = Signal(int)
    back_requested = Signal()

    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.crest_loader = CrestLoader(self)
        self.timezone = ZoneInfo(get_settings().app_timezone)
        self._profile_dialog: QDialog | None = None
        self._pending_team_id: int | None = None
        self._directory_count = 0
        self._loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QHBoxLayout()
        back = QPushButton("← Volver")
        back.clicked.connect(self.back_requested.emit)
        heading.addWidget(back)
        heading.addWidget(QLabel("Jugadores", objectName="sectionTitle"))
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
            "Perfiles y estadísticas disponibles. Los campos faltantes figuran como no informados.",
            objectName="muted",
        )
        layout.addWidget(self.note)
        panel = QFrame(objectName="panel")
        panel_layout = QVBoxLayout(panel)
        self.tabs = QTabWidget()
        directory_page = QWidget()
        directory_layout = QVBoxLayout(directory_page)
        self.directory_search = QLineEdit()
        self.directory_search.setPlaceholderText("Filtrar jugadores o equipos…")
        self.directory_search.setClearButtonEnabled(True)
        self.directory_search.textChanged.connect(self.refresh_directory)
        directory_layout.addWidget(self.directory_search)
        self.directory_table = self._table(
            [
                "Jugador",
                "Nacionalidad",
                "Posición",
                "Edad",
                "Equipo · competición · temporada",
            ]
        )
        self.directory_table.cellClicked.connect(self._open_directory_cell)
        directory_layout.addWidget(self.directory_table, 1)
        self.tabs.addTab(directory_page, "Todos los jugadores")

        self.table = self._table(["Pos.", "Jugador", "Goles", "Asistencias", "Partidos", "Fuente"])
        self.table.cellClicked.connect(self._open_scorer_cell)
        self.event_table = self._table(
            ["Fecha", "Partido", "Minuto", "Equipo", "Jugador", "Evento", "Fuente"]
        )
        self.event_table.cellClicked.connect(self._open_event_cell)
        self.roster_table = self._table(
            [
                "Equipo",
                "Jugador",
                "Posición",
                "Nacionalidad",
                "Nacimiento",
                "Altura",
                "Peso",
                "Partidos",
                "Goles",
                "Asistencias",
            ]
        )
        self.roster_table.cellClicked.connect(self._open_roster_cell)
        self.tabs.addTab(self.table, "Goleadores")
        self.tabs.addTab(self.event_table, "Goles por partido")
        self.tabs.addTab(self.roster_table, "Planteles y perfiles")
        panel_layout.addWidget(self.tabs)
        layout.addWidget(panel, 1)

    def ensure_loaded(self, *, load_data: bool = True) -> None:
        if not self._loaded:
            self._loaded = True
            self._load_competitions(refresh=load_data)
        elif load_data:
            QTimer.singleShot(0, self.refresh)

    def open_player_detail(self, player_id: int) -> None:
        try:
            with self.session_factory() as session:
                profile = player_profile_detail(session, player_id)
        except SQLAlchemyError as error:
            self._database_error(error)
            return
        if profile is None:
            return
        dialog = QDialog(self)
        self._profile_dialog = dialog
        self._pending_team_id = None
        dialog.setWindowTitle(f"Ficha de {profile.display_name}")
        dialog.resize(980, 700)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(profile.display_name, objectName="sectionTitle"))
        birth = profile.birth_date.strftime("%d/%m/%Y") if profile.birth_date else "No informada"
        age = self._age(profile.birth_date) if profile.birth_date else None
        identity = QLabel(
            f"Edad: {age if age is not None else 'No informada'} · Nacimiento: {birth} · "
            f"Nacionalidad: {profile.nationality or 'No informada'} · "
            f"Posición: {profile.position or 'No informada'} · "
            f"Altura: {profile.height or 'No informada'} · "
            f"Peso: {profile.weight or 'No informado'}\n"
            f"Fuente del perfil: {profile.profile_source_code or 'No informada'} · "
            f"Consultado: {self._timestamp(profile.profile_updated_at)}"
        )
        identity.setWordWrap(True)
        layout.addWidget(identity)

        layout.addWidget(QLabel("Equipos y competiciones", objectName="sectionTitle"))
        self._team_buttons: list[QPushButton] = []
        team_scroll = QScrollArea()
        team_scroll.setObjectName("playerTeamsScroll")
        team_scroll.setWidgetResizable(True)
        team_scroll.setFrameShape(QFrame.Shape.NoFrame)
        team_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        team_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        team_content = QWidget()
        team_row = QHBoxLayout(team_content)
        team_row.setContentsMargins(0, 0, 0, 0)
        team_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        if profile.team_competitions:
            for membership in profile.team_competitions:
                label = (
                    f"{membership.team_name} · {membership.competition_name} · "
                    f"{membership.season_label}"
                )
                if membership.position:
                    label += f" · {membership.position}"
                button = QPushButton(label)
                button.setObjectName("clubLink")
                if membership.competition_name.casefold() == "premier league":
                    self.crest_loader.apply(button, membership.crest_url, membership.team_name)
                button.clicked.connect(
                    lambda _checked=False, team_id=membership.team_id: self._open_profile_team(
                        dialog, team_id
                    )
                )
                self._team_buttons.append(button)
                team_row.addWidget(button)
        else:
            team_row.addWidget(QLabel("No hay equipos vinculados."))
        team_scroll.setWidget(team_content)
        layout.addWidget(team_scroll)
        self.profile_team_context = QLabel("Seleccioná una temporada para ver el equipo.")
        self.profile_team_context.setWordWrap(True)
        layout.addWidget(self.profile_team_context)

        season_row = QHBoxLayout()
        season_row.addWidget(QLabel("Competición y temporada"))
        self.profile_season = QComboBox()
        self.profile_season.addItem("Sin estadísticas de temporada", None)
        for statistic in profile.statistics:
            self.profile_season.addItem(
                f"{statistic.competition_name} · {statistic.season_label}",
                statistic.competition_season_id,
            )
        if profile.statistics:
            self.profile_season.setCurrentIndex(1)
        season_row.addWidget(self.profile_season, 1)
        layout.addLayout(season_row)

        self.profile_summary = QLabel("")
        self.profile_summary.setWordWrap(True)
        layout.addWidget(self.profile_summary)
        self.profile_table = self._table(["Estadística", "Valor", "Fuente"])
        layout.addWidget(self.profile_table, 1)
        self.profile_season.currentIndexChanged.connect(
            lambda _index: self._render_player_season(profile)
        )
        self._render_player_season(profile)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()
        self._profile_dialog = None
        if self._pending_team_id is not None:
            self.team_selected.emit(self._pending_team_id)

    def _render_player_season(self, profile: PlayerProfileDetail) -> None:
        season_id = self.profile_season.currentData()
        statistic = next(
            (row for row in profile.statistics if row.competition_season_id == season_id), None
        )
        self.profile_table.setRowCount(0)
        self._set_team_buttons_visible(profile, season_id)
        if statistic is None:
            self.profile_summary.setText("No hay estadísticas disponibles para esta temporada.")
            self.profile_team_context.setText("No hay equipo vinculado a esta temporada.")
            return
        memberships = [
            row for row in profile.team_competitions if row.competition_season_id == season_id
        ]
        self.profile_team_context.setText(
            " · ".join(
                f"{row.team_name} · {row.competition_name} · {row.season_label}"
                for row in memberships
            )
            or "Equipo no informado para esta temporada."
        )
        appearances = statistic.appearances
        general: list[tuple[str, int | float | str | None, str]] = [
            ("Partidos jugados", appearances, "appearances"),
            ("Titularidades", statistic.starts, "starts"),
            (
                "Apariciones como suplente",
                statistic.substitute_appearances,
                "substitute_appearances",
            ),
            ("Minutos", statistic.minutes_played, "minutes_played"),
            ("Goles", statistic.goals, "goals"),
            ("Asistencias", statistic.assists, "assists"),
            ("Tarjetas amarillas", statistic.yellow_cards, "yellow_cards"),
            ("Tarjetas rojas", statistic.red_cards, "red_cards"),
        ]
        if statistic.venue_stats:
            home_details = statistic.venue_appearances_with_detail.get("home", 0)
            away_details = statistic.venue_appearances_with_detail.get("away", 0)
            details = home_details + away_details
            coverage = f" · fichas con participación: {details}"
            if appearances is not None:
                coverage += f" de {appearances} PJ de temporada"
            source_label = ", ".join(statistic.venue_sources) or "Partidos registrados"
            source_label += coverage
            split_rows = (
                ("Partidos (local / visitante)", "appearances", "venue_appearances"),
                ("Titularidades (local / visitante)", "starts", "venue_starts"),
                (
                    "Suplencias (local / visitante)",
                    "substitute_appearances",
                    "venue_substitute_appearances",
                ),
                ("Minutos (local / visitante)", "minutes_played", "venue_minutes_played"),
                ("Goles (local / visitante)", "goals", "venue_goals"),
                ("Asistencias (local / visitante)", "assists", "venue_assists"),
                ("Amarillas (local / visitante)", "yellow_cards", "venue_yellow_cards"),
                ("Rojas (local / visitante)", "red_cards", "venue_red_cards"),
                ("Remates (local / visitante)", "shots_total", "venue_shots_total"),
                (
                    "Remates al arco (local / visitante)",
                    "shots_on_target",
                    "venue_shots_on_target",
                ),
            )
            for label, metric, source_key in split_rows:
                statistic.sources_by_field[source_key] = source_label
                general.append(
                    (
                        label,
                        self._venue_pair(
                            statistic.venue_stats.get(f"home_{metric}"),
                            statistic.venue_stats.get(f"away_{metric}"),
                        ),
                        source_key,
                    )
                )
        selected_positions = {row.position for row in memberships if row.position}
        position = " ".join(selected_positions).casefold() or (profile.position or "").casefold()
        positional: list[tuple[str, int | float | None, str | None]] = []
        if any(word in position for word in ("goalkeeper", "goal keeper", "portero", "arquero")):
            positional.extend(
                [
                    (
                        "Goles recibidos por partido",
                        self._rate(statistic.goals_conceded, appearances),
                        "goals_conceded",
                    ),
                    (
                        "Vallas invictas por partido",
                        self._rate(statistic.clean_sheets, appearances),
                        "clean_sheets",
                    ),
                ]
            )
        elif any(word in position for word in ("defender", "defensa", "defensor")):
            positional.extend(
                [
                    (
                        "Goles recibidos por partido",
                        self._rate(statistic.goals_conceded, appearances),
                        "goals_conceded",
                    ),
                    (
                        "Vallas invictas por partido",
                        self._rate(statistic.clean_sheets, appearances),
                        "clean_sheets",
                    ),
                    ("Remates", statistic.shots_total, "shots_total"),
                    ("Remates al arco", statistic.shots_on_target, "shots_on_target"),
                ]
            )
        elif any(
            word in position
            for word in (
                "midfielder",
                "medio",
                "mediocampista",
                "attacker",
                "forward",
                "delantero",
                "winger",
            )
        ):
            positional.extend(
                [
                    ("Remates", statistic.shots_total, "shots_total"),
                    ("Remates al arco", statistic.shots_on_target, "shots_on_target"),
                    ("Goles por partido", self._rate(statistic.goals, appearances), "goals"),
                    (
                        "Asistencias por partido",
                        self._rate(statistic.assists, appearances),
                        "assists",
                    ),
                ]
            )
        self._append_stat_rows(statistic, general + positional)
        position_label = (
            " / ".join(sorted(selected_positions))
            if selected_positions
            else profile.position or "No informada"
        )
        self.profile_summary.setText(
            f"Posición usada para las métricas: {position_label}. "
            "Promedios por partido: total de la métrica dividido por partidos jugados; "
            "se muestran vacíos si la fuente no los proporciona. "
            "El desglose local/visitante se calcula desde las fichas de partido disponibles."
        )

    def _append_stat_rows(
        self,
        statistic: PlayerSeasonDetail,
        values: list[tuple[str, int | float | str | None, str]],
    ) -> None:
        self.profile_table.setRowCount(len(values))
        for row, (label, value, field) in enumerate(values):
            display = (
                f"{value:.2f}"
                if isinstance(value, float)
                else value
                if isinstance(value, str)
                else self._value(value)
            )
            source = statistic.sources_by_field.get(field, "No informado")
            updated_at = statistic.updated_by_field.get(field)
            if updated_at is not None:
                source += f" · consultado {self._timestamp(updated_at)}"
            for column, text in enumerate((label, display, source)):
                self.profile_table.setItem(row, column, QTableWidgetItem(text))

    def _set_team_buttons_visible(
        self, profile: PlayerProfileDetail, season_id: int | None
    ) -> None:
        selected_team_ids = {
            row.team_id
            for row in profile.team_competitions
            if row.competition_season_id == season_id
        }
        for button, membership in zip(self._team_buttons, profile.team_competitions, strict=False):
            button.setVisible(membership.team_id in selected_team_ids)

    def _open_profile_team(self, dialog: QDialog, team_id: int) -> None:
        self._pending_team_id = team_id
        dialog.accept()

    @staticmethod
    def _rate(value: int | None, appearances: int | None) -> float | None:
        return value / appearances if value is not None and appearances else None

    @staticmethod
    def _age(birth_date: date) -> int:
        today = date.today()
        return (
            today.year
            - birth_date.year
            - ((today.month, today.day) < (birth_date.month, birth_date.day))
        )

    def _timestamp(self, value) -> str:
        if value is None:
            return "No informado"
        if value.tzinfo is None:
            value = value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone).strftime("%d/%m/%Y %H:%M")

    def _open_directory_cell(self, row: int, column: int) -> None:
        item = self.directory_table.item(row, 0)
        if item is not None:
            self.open_player_detail(int(item.data(Qt.ItemDataRole.UserRole)))

    def _open_scorer_cell(self, row: int, column: int) -> None:
        if column == 1:
            item = self.table.item(row, column)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
                self.open_player_detail(int(item.data(Qt.ItemDataRole.UserRole)))

    def _open_event_cell(self, row: int, column: int) -> None:
        if column == 4:
            item = self.event_table.item(row, column)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
                self.open_player_detail(int(item.data(Qt.ItemDataRole.UserRole)))

    def _open_roster_cell(self, row: int, column: int) -> None:
        item = self.roster_table.item(row, column)
        if item is None:
            return
        if column == 0 and item.data(Qt.ItemDataRole.UserRole) is not None:
            self.team_selected.emit(int(item.data(Qt.ItemDataRole.UserRole)))
        elif column == 1 and item.data(Qt.ItemDataRole.UserRole) is not None:
            self.open_player_detail(int(item.data(Qt.ItemDataRole.UserRole)))

    def _load_competitions(self, *, refresh: bool = True) -> None:
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
        self._load_seasons(refresh=refresh)

    def _load_seasons(self, _index: int = -1, *, refresh: bool = True) -> None:
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
        if refresh:
            QTimer.singleShot(0, self.refresh)

    def refresh_directory(self, _text: str = "") -> None:
        try:
            with self.session_factory() as session:
                rows = player_directory(
                    session,
                    competition_season_id=self.season_filter.currentData(),
                    query=self.directory_search.text(),
                )
        except SQLAlchemyError as error:
            self._database_error(error)
            return
        self._directory_count = len(rows)
        self.directory_table.setRowCount(len(rows))
        for row, player in enumerate(rows):
            values = (
                player.display_name,
                player.nationality or "No informada",
                player.position or "No informada",
                str(self._age(player.birth_date)) if player.birth_date else "No informada",
                "\n".join(player.team_labels) or "Equipo no informado",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, player.player_id)
                    self._make_link(item, "Abrir ficha del jugador")
                self.directory_table.setItem(row, column, item)
        if not rows:
            self._empty(self.directory_table, "No hay jugadores para los filtros seleccionados.")

    def refresh(self, _index: int = -1) -> None:
        season_id = self.season_filter.currentData()
        self.table.setRowCount(0)
        self.event_table.setRowCount(0)
        self.roster_table.setRowCount(0)
        self.refresh_directory()
        if season_id is None:
            self._empty(self.table, "No hay temporadas cargadas.")
            self._empty(self.event_table, "No hay temporadas cargadas.")
            self._empty(self.roster_table, "No hay temporadas cargadas.")
            return
        try:
            with self.session_factory() as session:
                players = player_statistics_for_competition_season(session, season_id)
                events = goal_events_for_competition_season(session, season_id)
                roster = player_roster_for_competition_season(session, season_id)
        except SQLAlchemyError as error:
            self._database_error(error)
            return
        if not players:
            self._empty(
                self.table,
                "No hay estadísticas de goleadores para esta competición y temporada.",
            )
        else:
            self.table.setRowCount(len(players))
            for row, player in enumerate(players, start=1):
                values = (
                    str(row),
                    player.display_name,
                    self._value(player.goals),
                    self._value(player.assists),
                    self._value(player.appearances),
                    player.source_code,
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column in (0, 2, 3, 4):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if column == 1:
                        item.setData(Qt.ItemDataRole.UserRole, player.player_id)
                        self._make_link(item, "Abrir ficha del jugador")
                    self.table.setItem(row - 1, column, item)
        if not events:
            self._empty(self.event_table, "No hay eventos de gol disponibles para esta temporada.")
        else:
            self.event_table.setRowCount(len(events))
            event_labels = {"GOAL": "Gol", "PENALTY_GOAL": "Gol de penal", "OWN_GOAL": "Autogol"}
            for row, event in enumerate(events):
                date_text = event.kickoff_utc.astimezone(self.timezone).strftime("%d/%m/%Y %H:%M")
                values = (
                    date_text,
                    f"{event.home_team} — {event.away_team}",
                    f"{event.minute}′" if event.minute is not None else "—",
                    event.team_name or "—",
                    event.player_name or "—",
                    event_labels.get(event.event_type, event.event_type),
                    event.source_code,
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 2:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if column == 4 and event.player_id is not None:
                        item.setData(Qt.ItemDataRole.UserRole, event.player_id)
                        self._make_link(item, "Abrir ficha del jugador")
                    self.event_table.setItem(row, column, item)
        if not roster:
            self._empty(self.roster_table, "No hay planteles publicados para esta temporada.")
        else:
            self.roster_table.setRowCount(len(roster))
            for row, player in enumerate(roster):
                values = (
                    player.team_name,
                    player.player_name,
                    player.position or "No informada",
                    player.nationality or "No informada",
                    player.birth_date.strftime("%d/%m/%Y") if player.birth_date else "No informada",
                    player.height or "No informada",
                    player.weight or "No informado",
                    self._value(player.appearances),
                    self._value(player.goals),
                    self._value(player.assists),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, player.team_id)
                        self._make_link(item, "Abrir ficha del equipo")
                    elif column == 1:
                        item.setData(Qt.ItemDataRole.UserRole, player.player_id)
                        self._make_link(item, "Abrir ficha del jugador")
                    self.roster_table.setItem(row, column, item)
        self.note.setText(
            f"{self._directory_count} jugadores en el directorio · "
            f"{len(players)} goleadores · {len(events)} eventos · "
            f"{len(roster)} jugadores de plantel"
        )

    @staticmethod
    def _make_link(item: QTableWidgetItem, tooltip: str) -> None:
        item.setForeground(QColor("#69aef7"))
        font = item.font()
        font.setUnderline(True)
        item.setFont(font)
        item.setToolTip(tooltip)

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        if len(headers) > 1:
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, len(headers)):
            table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        return table

    @staticmethod
    def _value(value: int | None) -> str:
        return str(value) if value is not None else "No informado"

    @staticmethod
    def _venue_pair(home: int | None, away: int | None) -> str:
        home_value = str(home) if home is not None else "—"
        away_value = str(away) if away is not None else "—"
        return f"{home_value} / {away_value}"

    @staticmethod
    def _empty(table: QTableWidget, message: str) -> None:
        table.setRowCount(1)
        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(0, 0, item)
        table.setSpan(0, 0, 1, table.columnCount())

    def _database_error(self, error: Exception) -> None:
        message = f"Base local no disponible ({error.__class__.__name__}). Revisá PostgreSQL."
        self._empty(self.directory_table, message)
        self._empty(self.table, message)
        self._empty(self.event_table, message)
        self._empty(self.roster_table, message)
