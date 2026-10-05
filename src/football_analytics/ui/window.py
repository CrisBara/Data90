"""Main window and compact primary navigation."""

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from football_analytics.ui.competitions import CompetitionsPage
from football_analytics.ui.home import HomePage
from football_analytics.ui.statistics import StatisticsPage
from football_analytics.ui.teams import TeamsPage
from football_analytics.ui.tools import ToolsPage


class MainWindow(QMainWindow):
    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.setWindowTitle("Football Analytics Pro")
        self.resize(1200, 780)
        self.setMinimumSize(900, 600)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        navigation = QFrame(objectName="navigation")
        nav_layout = QHBoxLayout(navigation)
        nav_layout.setContentsMargins(8, 6, 8, 6)
        self.page_stack = QStackedWidget()
        self._buttons: dict[str, QPushButton] = {}
        pages = (
            HomePage(session_factory),
            CompetitionsPage(session_factory),
            TeamsPage(session_factory),
            StatisticsPage(session_factory),
            ToolsPage(session_factory),
        )
        for page in pages:
            self.page_stack.addWidget(page)
        labels = ("Inicio", "Competiciones", "Equipos", "Estadísticas", "Herramientas")
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _checked=False, page=index: self.select_page(page))
            nav_layout.addWidget(button)
            self._buttons[label] = button
        nav_layout.addStretch(1)
        layout.addWidget(navigation)
        layout.addWidget(self.page_stack, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Base local preparada")

    def select_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        for page_index, button in enumerate(self._buttons.values()):
            button.setChecked(page_index == index)

    def closeEvent(self, event) -> None:
        tools_page = self.page_stack.widget(4)
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
