"""First-run database and provider setup wizard."""

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from football_analytics.config.paths import is_frozen_build, project_root


class SetupWizard(QDialog):
    def __init__(self, *, token_configured: bool) -> None:
        super().__init__()
        self.setWindowTitle("Configuración inicial · Football Analytics Pro")
        self.setMinimumWidth(640)
        self.setMinimumHeight(430)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Puesta en marcha de Football Analytics Pro"))
        layout.addWidget(QLabel("1. PostgreSQL local: conectado"))
        token_message = (
            "2. Token de football-data.org: configurado"
            if token_configured
            else "2. Token de football-data.org: falta en .env. "
            "OpenLigaDB seguirá disponible; football-data.org no podrá sincronizar."
        )
        token_label = QLabel(token_message)
        token_label.setWordWrap(True)
        layout.addWidget(token_label)
        instruction = QLabel(
            "El siguiente paso aplicará las migraciones, preparará los catálogos e intentará "
            "cargar los datos iniciales. Puede tardar varios minutos."
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("El progreso aparecerá aquí.")
        layout.addWidget(self.output, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.start_button = QPushButton("Preparar base y cargar datos")
        self.start_button.clicked.connect(self._start_bootstrap)
        buttons.addWidget(self.start_button)
        self.open_button = QPushButton("Abrir aplicación")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.accept)
        buttons.addWidget(self.open_button)
        self.exit_button = QPushButton("Salir")
        self.exit_button.clicked.connect(self.reject)
        buttons.addWidget(self.exit_button)
        layout.addLayout(buttons)

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(project_root()))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self._append_output)
        self.process.finished.connect(self._bootstrap_finished)
        self.process.errorOccurred.connect(self._process_error)

    def _start_bootstrap(self) -> None:
        self.start_button.setEnabled(False)
        self.exit_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.progress.show()
        self.output.setPlainText("Iniciando la migración y el bootstrap…")
        if is_frozen_build():
            bootstrap = Path(sys.executable).with_name("FootballAnalyticsBootstrap.exe")
            if not bootstrap.is_file():
                self.progress.hide()
                self.exit_button.setEnabled(True)
                self.start_button.setEnabled(True)
                self.output.setPlainText(f"No se encontró el asistente de instalación: {bootstrap}")
                return
            self.process.start(str(bootstrap), [])
        else:
            script = project_root() / "scripts" / "bootstrap.py"
            if not script.is_file():
                self.progress.hide()
                self.exit_button.setEnabled(True)
                self.start_button.setEnabled(True)
                self.output.setPlainText(f"No se encontró el script de inicialización: {script}")
                return
            self.process.start(sys.executable, [str(script)])

    def _append_output(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(chunk)
        self.output.ensureCursorVisible()

    def _bootstrap_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._append_output()
        self.progress.hide()
        self.exit_button.setEnabled(True)
        if exit_code in (0, 2):
            if exit_code == 0:
                self.output.appendPlainText("\nConfiguración completa.")
            else:
                self.output.appendPlainText(
                    "\nLa configuración terminó con disponibilidad parcial. "
                    "Podés abrir la aplicación y revisar Herramientas."
                )
            self.open_button.setEnabled(True)
            self.open_button.setDefault(True)
            return

        self.output.appendPlainText(
            f"\nLa inicialización falló (código {exit_code}). "
            "Revisá el detalle, corregí la configuración y volvé a intentarlo."
        )
        self.start_button.setEnabled(True)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.progress.hide()
            self.exit_button.setEnabled(True)
            self.start_button.setEnabled(True)
            self.output.appendPlainText("\nNo se pudo iniciar Python para el bootstrap.")

    def closeEvent(self, event) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            event.ignore()
            return
        super().closeEvent(event)
