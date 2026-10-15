"""Main window with tactical side navigation and global search."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from data90.ui.competitions import CompetitionsPage
from data90.ui.home import CalendarPage
from data90.ui.players import PlayersPage
from data90.ui.search import GlobalSearchPage
from data90.ui.statistics import StatisticsPage
from data90.ui.teams import TeamsPage
from data90.ui.tools import ToolsPage


class MainWindow(QMainWindow):
    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.setWindowTitle("Data90")
        mark_path = Path(__file__).resolve().parent / "assets" / "data90-mark.png"
        self._brand_full_logo_path = Path(__file__).resolve().parent / "assets" / "data90-logo-full.png"
        self.setWindowIcon(QIcon(str(mark_path)))
        self.resize(1440, 900)
        self.setMinimumSize(1024, 680)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(0)

        header = QFrame(objectName="appHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 9, 14, 9)
        header_layout.setSpacing(12)
        self._brand_mark_path = mark_path
        brand_mark = QPushButton(objectName="brandMark")
        brand_mark.setFixedSize(50, 50)
        brand_mark.setIcon(QIcon(str(mark_path)))
        brand_mark.setIconSize(QSize(50, 50))
        brand_mark.setAccessibleName("Ver el logo Data90 ampliado")
        brand_mark.setToolTip("Ver logo ampliado")
        brand_mark.setCursor(Qt.CursorShape.PointingHandCursor)
        brand_mark.clicked.connect(self._show_brand_preview)
        header_layout.addWidget(brand_mark)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QPushButton("Data90", objectName="appTitle")
        title.setToolTip("Volver al inicio y al calendario")
        title.setAccessibleName("Data90: volver al inicio")
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.clicked.connect(lambda: self.select_page(0))
        subtitle = QLabel("LIGAS · CLUBES · CALENDARIO", objectName="brandSubtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header_layout.addLayout(brand)
        header_layout.addStretch(1)
        self.global_search = QLineEdit()
        self.global_search.setObjectName("globalSearch")
        self.global_search.setPlaceholderText("Buscar equipo o jugador…")
        self.global_search.setClearButtonEnabled(True)
        self.global_search.setMinimumWidth(250)
        self.global_search.returnPressed.connect(self._perform_search)
        header_layout.addWidget(self.global_search)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.global_search.setFocus)
        search_button = QPushButton("Buscar")
        search_button.setObjectName("searchButton")
        search_button.clicked.connect(self._perform_search)
        header_layout.addWidget(search_button)
        layout.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 10, 0, 0)
        body.setSpacing(0)
        self.page_stack = QStackedWidget()
        self._buttons: dict[str, QPushButton] = {}
        self.teams_page = TeamsPage(session_factory)
        self.players_page = PlayersPage(session_factory)
        self.search_page = GlobalSearchPage(session_factory)
        self.calendar_page = CalendarPage(session_factory)
        self.competitions_page = CompetitionsPage(session_factory)
        self.statistics_page = StatisticsPage(session_factory)
        pages = (
            self.calendar_page,
            self.competitions_page,
            self.teams_page,
            self.statistics_page,
            ToolsPage(session_factory),
            self.search_page,
        )
        for page in pages:
            self.page_stack.addWidget(page)
        self._last_main_page = 0
        self._tool_page_index = 4
        self._search_page_index = 5
        body.addWidget(self.page_stack, 1)
        layout.addLayout(body, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Base local preparada")
        self.calendar_page.team_selected.connect(self._open_team_result)
        self.calendar_page.competition_selected.connect(self._open_competition_result)
        self.competitions_page.team_selected.connect(self._open_team_result)
        self.competitions_page.player_selected.connect(self._open_player_result)
        self.teams_page.team_selected.connect(self._open_team_result)
        self.teams_page.back_requested.connect(lambda: self.select_page(0))
        self.statistics_page.team_selected.connect(self._open_team_result)
        self.search_page.team_selected.connect(self._open_team_result)
        self.search_page.player_selected.connect(self._open_player_result)
        self.teams_page.player_selected.connect(self._open_player_result)
        self.players_page.team_selected.connect(self._open_team_result)
        self.search_page.back_requested.connect(lambda: self.select_page(self._last_main_page))

    def _show_brand_preview(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("brandPreviewDialog")
        dialog.setWindowTitle("Data90 · Logo completo")
        dialog.setModal(True)
        dialog.setFixedSize(390, 450)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        image = QLabel(objectName="brandPreviewImage")
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setPixmap(
            QPixmap(str(self._brand_full_logo_path)).scaled(
                330,
                348,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        image.setAccessibleName("Logo Data90 completo ampliado")
        layout.addWidget(image, 1)
        close_button = QPushButton("Cerrar")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _perform_search(self) -> None:
        query = self.global_search.text().strip()
        if not query:
            self.statusBar().showMessage("Escribí el nombre de un equipo o jugador", 3000)
            return
        current_index = self.page_stack.currentIndex()
        if current_index != self._search_page_index:
            self._last_main_page = current_index
        self.search_page.search(query)
        self.select_page(self._search_page_index)

    def _open_team_result(self, team_id: int) -> None:
        self.teams_page.open_detail(team_id)
        self.select_page(2)

    def _open_player_result(self, player_id: int) -> None:
        self.players_page.open_player_detail(player_id)

    def _open_competition_result(self, competition_code: str) -> None:
        self.competitions_page.open_detail(competition_code)
        self.select_page(1)

    def select_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        if index != self._search_page_index:
            self._last_main_page = index
        for label, button in self._buttons.items():
            button.setChecked(self._navigation_indices[label] == index)

    def toggle_sidebar(self) -> None:
        # The home calendar now owns competition navigation; no global sidebar.
        return

    def closeEvent(self, event) -> None:
        tools_page = self.page_stack.widget(self._tool_page_index)
        worker = getattr(tools_page, "worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(
                self,
                "Sincronización en curso",
                "Esperá a que termine la sincronización antes de cerrar la ventana.",
            )
            event.ignore()
            return
        super().closeEvent(event)
