import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from football_analytics.database.base import Base
from football_analytics.ui.window import MainWindow


def test_main_window_and_all_pages_open_with_an_empty_local_database() -> None:
    app = QApplication.instance() or QApplication([])
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    window = MainWindow(sessionmaker(bind=engine, expire_on_commit=False))

    assert window.windowTitle() == "Football Analytics Pro"
    assert window.page_stack.count() == 5
    for page_index in range(window.page_stack.count()):
        window.select_page(page_index)
        assert window.page_stack.currentIndex() == page_index

    window.close()
    window.deleteLater()
    app.processEvents()
    engine.dispose()
