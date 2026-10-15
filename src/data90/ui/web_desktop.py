"""Desktop host for the current responsive web interface."""

from __future__ import annotations

from threading import Thread
from wsgiref.simple_server import WSGIServer, make_server

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow


class WebDesktopWindow(QMainWindow):
    """Show the same UI as the HTML companion in a native desktop window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Data90")
        self.setMinimumSize(1024, 680)

        from data90.web import application

        self._web_server: WSGIServer = make_server("127.0.0.1", 0, application)
        port = self._web_server.server_port
        self._web_thread = Thread(
            target=self._web_server.serve_forever,
            name="data90-web",
            daemon=True,
        )
        self._web_thread.start()

        browser = QWebEngineView(self)
        browser.setUrl(QUrl(f"http://127.0.0.1:{port}/"))
        self.setCentralWidget(browser)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        self._web_server.shutdown()
        self._web_server.server_close()
        super().closeEvent(event)
