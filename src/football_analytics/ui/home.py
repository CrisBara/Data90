"""Home page: local fixtures for a selected date and competition."""

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from football_analytics.config.constants import COMPETITIONS
from football_analytics.config.settings import get_settings
from football_analytics.services.match_service import MatchService


class HomePage(QWidget):
    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.timezone = ZoneInfo(get_settings().app_timezone)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        title = QLabel("Football Analytics Pro", objectName="appTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dddd, dd MMMM yyyy")
        self.date_edit.setDate(datetime.now(self.timezone).date())
        self.date_edit.dateChanged.connect(self.refresh)
        header_layout.addWidget(self.date_edit)
        self.competition_filter = QComboBox()
        self.competition_filter.addItem("Todas las competiciones", None)
        for competition in COMPETITIONS:
            self.competition_filter.addItem(competition.name, competition.code)
        self.competition_filter.currentIndexChanged.connect(self.refresh)
        header_layout.addWidget(self.competition_filter)
        refresh_button = QPushButton("Actualizar vista")
        refresh_button.clicked.connect(self.refresh)
        header_layout.addWidget(refresh_button)
        layout.addWidget(header)

        section = QHBoxLayout()
        section.addWidget(QLabel("Partidos del día", objectName="sectionTitle"))
        section.addStretch(1)
        self.result_label = QLabel("", objectName="muted")
        section.addWidget(self.result_label)
        layout.addLayout(section)

        panel = QFrame(objectName="panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Hora", "Local", "Resultado", "Visitante", "Estado", "Detalle"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        panel_layout.addWidget(self.table)
        layout.addWidget(panel, 1)

    def refresh(self) -> None:
        selected_date = self.date_edit.date().toPython()
        code = self.competition_filter.currentData()
        competition_codes = (code,) if code else None
        try:
            with self.session_factory() as session:
                matches = MatchService(session).get_matches(selected_date, competition_codes)
        except SQLAlchemyError as error:
            self.table.setRowCount(0)
            self.result_label.setText("No se pudo consultar PostgreSQL")
            self.status_message(f"Error local de base de datos: {error.__class__.__name__}")
            return

        grouped = defaultdict(list)
        for match in matches:
            grouped[(match.competition_code, match.competition_name)].append(match)
        self.table.setRowCount(0)
        for (_code, competition_name), fixtures in grouped.items():
            section_row = self.table.rowCount()
            self.table.insertRow(section_row)
            heading = QTableWidgetItem(competition_name)
            heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
            heading.setBackground(Qt.GlobalColor.lightGray)
            self.table.setItem(section_row, 0, heading)
            self.table.setSpan(section_row, 0, 1, 6)
            for match in fixtures:
                row = self.table.rowCount()
                self.table.insertRow(row)
                local_kickoff = match.kickoff_utc.astimezone(self.timezone)
                self._set_cell(
                    row, 0, local_kickoff.strftime("%H:%M"), Qt.AlignmentFlag.AlignCenter
                )
                self._set_cell(row, 1, match.home_team)
                score = (
                    f"{match.home_score_ft} – {match.away_score_ft}"
                    if match.home_score_ft is not None and match.away_score_ft is not None
                    else "—"
                )
                self._set_cell(row, 2, score, Qt.AlignmentFlag.AlignCenter)
                self._set_cell(row, 3, match.away_team)
                self._set_cell(
                    row, 4, self._status_label(match.status), Qt.AlignmentFlag.AlignCenter
                )
                detail = QPushButton("Ver")
                detail.clicked.connect(lambda _checked=False, item=match: self.show_detail(item))
                self.table.setCellWidget(row, 5, detail)
        if not matches:
            self.table.insertRow(0)
            empty = QTableWidgetItem("No hay partidos registrados para esta fecha y filtro.")
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(0, 0, empty)
            self.table.setSpan(0, 0, 1, 6)
        self.result_label.setText(f"{len(matches)} partidos")
        self.status_message("Datos locales · sin llamadas a proveedores")

    def _set_cell(self, row: int, column: int, text: str, alignment=None) -> None:
        item = QTableWidgetItem(text)
        if alignment is not None:
            item.setTextAlignment(alignment)
        self.table.setItem(row, column, item)

    @staticmethod
    def _status_label(status: str) -> str:
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

    def show_detail(self, match) -> None:
        details = [f"{match.home_team} vs {match.away_team}", match.competition_name]
        if match.matchday is not None:
            details.append(f"Jornada {match.matchday}")
        if match.stage:
            details.append(match.stage)
        details.append(match.kickoff_utc.astimezone(self.timezone).strftime("%d/%m/%Y %H:%M"))
        QMessageBox.information(self, "Detalle del partido", "\n".join(details))

    def status_message(self, message: str) -> None:
        window = self.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(message)
