"""Open the current web companion inside a desktop-style preview window.

This is an isolated visual preview. It does not replace the native desktop app,
change database contents, or start a second web server.
"""

from __future__ import annotations

import sys
from threading import Thread
from urllib.error import URLError
from urllib.request import urlopen
from wsgiref.simple_server import make_server

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PySide6.QtWebEngineWidgets import QWebEngineView

PREVIEW_URL = "http://127.0.0.1:8765/"


def ensure_web_server():
    """Reuse the web preview server, or host its read-only WSGI app locally."""
    try:
        with urlopen(PREVIEW_URL, timeout=2) as response:
            if response.status == 200:
                return None
    except (OSError, URLError):
        pass

    from football_analytics.web import application

    server = make_server("127.0.0.1", 8765, application)
    Thread(target=server.serve_forever, name="fap-web-preview", daemon=True).start()
    return server


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Football Analytics Pro · Vista de prueba")

    try:
        server = ensure_web_server()
    except (OSError, URLError):
        QMessageBox.critical(
            None,
            "Vista web no disponible",
            "No se pudo iniciar la vista web local en el puerto 8765.\n\n"
            "Cerrá otra vista web que esté usando ese puerto y volvé a abrir esta prueba.",
        )
        return 1

    window = QMainWindow()
    window.setWindowTitle("Football Analytics Pro · Vista de prueba HTML")
    window.setMinimumSize(1024, 680)
    window._web_preview_server = server
    browser = QWebEngineView(window)
    browser.setUrl(QUrl(PREVIEW_URL))
    window.setCentralWidget(browser)
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
