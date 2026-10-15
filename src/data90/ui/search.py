"""Global search results for teams and players."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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

from data90.repositories.global_search import search_teams_and_players
from data90.ui.home import CrestLoader


class GlobalSearchPage(QWidget):
    team_selected = Signal(int)
    player_selected = Signal(int)
    back_requested = Signal()

    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.crest_loader = CrestLoader(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        header = QHBoxLayout()
        back = QPushButton("← Volver")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        header.addWidget(QLabel("Resultados de búsqueda", objectName="sectionTitle"))
        header.addStretch(1)
        layout.addLayout(header)
        self.summary = QLabel(
            "Escribí parte del nombre de un equipo o jugador.", objectName="muted"
        )
        layout.addWidget(self.summary)
        panel = QFrame(objectName="panel")
        panel_layout = QVBoxLayout(panel)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Tipo", "Nombre", "País / nacionalidad", "Liga / equipo", ""]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.cellClicked.connect(self._open_row)
        self.table.cellClicked.connect(self._open_row)
        panel_layout.addWidget(self.table)
        layout.addWidget(panel, 1)

    def search(self, query: str) -> None:
        self.table.setRowCount(0)
        try:
            with self.session_factory() as session:
                results = search_teams_and_players(session, query)
        except SQLAlchemyError as error:
            self.summary.setText(
                f"No se pudo consultar la base local ({error.__class__.__name__})."
            )
            return
        if not results:
            self.summary.setText(f"No hay equipos ni jugadores que coincidan con «{query}».")
            return
        self.summary.setText(
            f"{len(results)} resultados para «{query}». Doble clic o Abrir para ver detalles."
        )
        self.table.setRowCount(len(results))
        for row, result in enumerate(results):
            values = (
                result.kind,
                result.name,
                result.country_or_nationality or "No informada",
                result.leagues_or_teams,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, (result.kind, result.entity_id))
                if column == 1:
                    font = item.font()
                    font.setBold(True)
                    font.setPointSize(font.pointSize() + 1)
                    item.setFont(font)
                    color = (
                        Qt.GlobalColor.white if result.kind == "Jugador" else Qt.GlobalColor.cyan
                    )
                    item.setForeground(color)
                self.table.setItem(row, column, item)
            if result.kind == "Equipo" and "premier league" in result.leagues_or_teams.casefold():
                team_button = QPushButton(result.name, objectName="clubLink")
                team_button.setCursor(Qt.CursorShape.PointingHandCursor)
                self.crest_loader.apply(team_button, result.crest_url, result.name)
                team_button.clicked.connect(
                    lambda _checked=False, entity_id=result.entity_id: self._open(
                        "Equipo", entity_id
                    )
                )
                self.table.setCellWidget(row, 1, team_button)
            if result.kind == "Jugador" and result.team_links:
                affiliations = QWidget()
                affiliation_layout = QHBoxLayout(affiliations)
                affiliation_layout.setContentsMargins(4, 1, 4, 1)
                affiliation_layout.setSpacing(5)
                for (
                    team_id,
                    team_name,
                    league_name,
                    crest_url,
                    competition_code,
                ) in result.team_links:
                    if competition_code == "PL":
                        club = QPushButton(team_name, objectName="clubLink")
                        club.setCursor(Qt.CursorShape.PointingHandCursor)
                        self.crest_loader.apply(club, crest_url, team_name)
                        club.clicked.connect(
                            lambda _checked=False, selected_id=team_id: self.team_selected.emit(
                                selected_id
                            )
                        )
                        affiliation_layout.addWidget(club)
                    else:
                        affiliation_layout.addWidget(QLabel(f"{team_name} · {league_name}"))
                affiliation_layout.addStretch(1)
                self.table.setCellWidget(row, 3, affiliations)
            button = QPushButton("Abrir estadísticas" if result.kind == "Equipo" else "Ver jugador")
            button.clicked.connect(
                lambda _checked=False, kind=result.kind, entity_id=result.entity_id: self._open(
                    kind, entity_id
                )
            )
            self.table.setCellWidget(row, 4, button)

    def _open_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        result = item.data(Qt.ItemDataRole.UserRole) if item else None
        if result:
            self._open(*result)

    def _open(self, kind: str, entity_id: int) -> None:
        if kind == "Equipo":
            self.team_selected.emit(entity_id)
        else:
            self.player_selected.emit(entity_id)
