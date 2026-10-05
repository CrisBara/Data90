"""Launch the local desktop application."""

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from sqlalchemy import text
from sqlalchemy.engine import Engine

from football_analytics.config.paths import environment_file, project_root
from football_analytics.config.settings import get_settings
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.ui.setup_wizard import SetupWizard
from football_analytics.ui.window import MainWindow


def _connect_database() -> Engine | None:
    config_path = environment_file()
    template_path = project_root() / ".env.example"
    while True:
        engine: Engine | None = None
        try:
            engine = make_engine()
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return engine
        except Exception as error:
            if engine is not None:
                engine.dispose()

            dialog = QMessageBox()
            dialog.setWindowTitle("Base de datos local no disponible")
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setText("No se pudo conectar con PostgreSQL local.")
            dialog.setInformativeText(
                "Iniciá el servicio PostgreSQL y revisá la configuración local. "
                f"Archivo de configuración: {config_path}\n"
                f"Error: {error.__class__.__name__}"
            )
            retry_button = dialog.addButton("Reintentar", QMessageBox.ButtonRole.AcceptRole)
            config_button = dialog.addButton("Ver configuración", QMessageBox.ButtonRole.ActionRole)
            dialog.addButton("Salir", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            clicked = dialog.clickedButton()

            if clicked is retry_button:
                continue
            if clicked is config_button:
                path_to_open = config_path if config_path.is_file() else template_path
                if path_to_open.is_file():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path_to_open)))
                else:
                    QMessageBox.information(
                        None,
                        "Configuración no encontrada",
                        "No se encontró .env ni .env.example en la carpeta de la aplicación.",
                    )
                continue
            return None


def _database_needs_setup(engine: Engine) -> bool:
    required_tables = ("competitions", "data_sources", "matches", "sync_runs")
    with engine.connect() as connection:
        present = connection.execute(
            text(
                "SELECT "
                + ", ".join(
                    f"to_regclass('public.{table}') IS NOT NULL" for table in required_tables
                )
            )
        ).one()
        if not all(present):
            return True
        competition_count, source_count, run_count = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM competitions), "
                "(SELECT count(*) FROM data_sources), "
                "(SELECT count(*) FROM sync_runs)"
            )
        ).one()
    return competition_count < 8 or source_count < 3 or run_count == 0


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Football Analytics Pro")
    app.setStyle("Fusion")
    stylesheet = Path(__file__).parent / "ui" / "theme" / "app.qss"
    if stylesheet.is_file():
        app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))
    engine = _connect_database()
    if engine is None:
        return 1
    try:
        if _database_needs_setup(engine):
            wizard = SetupWizard(
                token_configured=bool(get_settings().football_data_token.get_secret_value())
            )
            if wizard.exec() != QDialog.DialogCode.Accepted:
                engine.dispose()
                return 1
            if _database_needs_setup(engine):
                QMessageBox.critical(
                    None,
                    "Configuración incompleta",
                    "PostgreSQL todavía no tiene los catálogos y datos iniciales. "
                    "Revisá el resultado del asistente y volvé a intentarlo.",
                )
                engine.dispose()
                return 1
    except Exception as error:
        QMessageBox.critical(
            None,
            "No se pudo comprobar la configuración",
            f"Falló la verificación de la base local ({error.__class__.__name__}).",
        )
        engine.dispose()
        return 1
    window = MainWindow(make_session_factory(engine))
    window.show()
    app.aboutToQuit.connect(engine.dispose)
    return app.exec()


if __name__ == "__main__":
    main()
