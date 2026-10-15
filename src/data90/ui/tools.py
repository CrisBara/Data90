"""Local database health, sync history, source attribution, and data-quality view."""

from datetime import datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from data90.config.settings import get_settings
from data90.services.sync_service import SyncSummary, run_daily_sync
from data90.services.tools_service import (
    SourceStatusItem,
    SyncErrorItem,
    SyncHistoryItem,
    ToolsService,
)


class SyncWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            summary = run_daily_sync()
            self.completed.emit(summary)
        except Exception as error:
            self.failed.emit(error.__class__.__name__)


class ToolsPage(QWidget):
    def __init__(self, session_factory: object) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.worker: SyncWorker | None = None
        self.timezone = ZoneInfo(get_settings().app_timezone)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Herramientas", objectName="sectionTitle"))
        heading.addStretch(1)
        self.refresh_button = QPushButton("Actualizar información")
        self.refresh_button.clicked.connect(self.refresh)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)

        self.tabs = QTabWidget()
        self.local_info = QLabel("")
        self.local_info.setWordWrap(True)
        self.local_info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        local_page = QWidget()
        local_layout = QVBoxLayout(local_page)
        local_layout.addWidget(self.local_info)
        local_layout.addStretch(1)
        self.tabs.addTab(local_page, "Base local")

        sync_page = QWidget()
        sync_layout = QVBoxLayout(sync_page)
        sync_controls = QHBoxLayout()
        self.sync_button = QPushButton("Actualizar ahora")
        self.sync_button.clicked.connect(self.start_sync)
        sync_controls.addWidget(self.sync_button)
        sync_controls.addWidget(QLabel(f"Horario configurado: {get_settings().sync_time}"))
        sync_controls.addStretch(1)
        sync_layout.addLayout(sync_controls)
        self.sync_message = QLabel(
            "La sincronización usa los proveedores configurados.", objectName="muted"
        )
        sync_layout.addWidget(self.sync_message)
        self.sync_table = self._table(
            ["Inicio", "Modo", "Estado", "Solicitudes", "Nuevos", "Actualizados", "Errores"]
        )
        sync_layout.addWidget(self.sync_table, 1)
        self.tabs.addTab(sync_page, "Sincronización")

        sources_page = QWidget()
        sources_layout = QVBoxLayout(sources_page)
        self.sources_table = self._table(
            [
                "Fuente",
                "Prioridad",
                "Requiere token",
                "Último acceso correcto",
                "Atribución / licencia",
            ]
        )
        sources_layout.addWidget(self.sources_table)
        self.tabs.addTab(sources_page, "Fuentes")

        quality_page = QWidget()
        quality_layout = QVBoxLayout(quality_page)
        self.quality_summary = QLabel("")
        self.quality_summary.setObjectName("muted")
        quality_layout.addWidget(self.quality_summary)
        self.errors_table = self._table(["Fecha", "Competición", "Fuente", "Error", "Detalle"])
        quality_layout.addWidget(self.errors_table, 1)
        self.tabs.addTab(quality_page, "Calidad y errores")
        layout.addWidget(self.tabs, 1)
        self.refresh()

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        return table

    def refresh(self) -> None:
        try:
            with self.session_factory() as session:
                snapshot = ToolsService(session).get_snapshot()
        except SQLAlchemyError as error:
            self.local_info.setText(
                f"PostgreSQL desconectado · {error.__class__.__name__}\n"
                "Verificá que el servicio local esté iniciado."
            )
            return

        latest = (
            self._local_time(snapshot.latest_data_update)
            if snapshot.latest_data_update
            else "Sin sincronizaciones"
        )
        self.local_info.setText(
            f"PostgreSQL: conectado\nBase local: {snapshot.database_name}\n"
            f"Versión: {snapshot.database_version}\n"
            f"Última actualización de competiciones: {latest}\n"
            f"Horario diario configurado: {get_settings().sync_time} · "
            "la tarea automática requiere que Windows esté encendido y tu sesión iniciada."
        )
        self._fill_sync_history(snapshot.sync_history)
        self._fill_sources(snapshot.sources)
        self.quality_summary.setText(
            f"Problemas de calidad abiertos: {snapshot.open_quality_issues}"
        )
        self._fill_errors(snapshot.recent_errors)

    def _fill_sync_history(self, runs: list[SyncHistoryItem]) -> None:
        self.sync_table.setRowCount(0)
        for run in runs:
            row = self.sync_table.rowCount()
            self.sync_table.insertRow(row)
            values = [
                self._local_time(run.started_at),
                run.mode,
                run.status,
                str(run.requests_count),
                str(run.inserted_count),
                str(run.updated_count),
                str(run.error_count),
            ]
            for column, value in enumerate(values):
                self.sync_table.setItem(row, column, QTableWidgetItem(value))

    def _fill_sources(self, rows: list[SourceStatusItem]) -> None:
        self.sources_table.setRowCount(0)
        for source in rows:
            row = self.sources_table.rowCount()
            self.sources_table.insertRow(row)
            values = [
                source.name,
                str(source.priority),
                "Sí" if source.requires_api_key else "No",
                self._local_time(source.last_success_at)
                if source.last_success_at
                else "Sin acceso correcto",
                source.attribution or "—",
            ]
            for column, value in enumerate(values):
                self.sources_table.setItem(row, column, QTableWidgetItem(value))

    def _fill_errors(self, rows: list[SyncErrorItem]) -> None:
        self.errors_table.setRowCount(0)
        for error in rows:
            row = self.errors_table.rowCount()
            self.errors_table.insertRow(row)
            values = [
                self._local_time(error.started_at),
                error.competition_code or "—",
                error.source_name,
                error.error_code or "Error",
                error.message or "—",
            ]
            for column, value in enumerate(values):
                self.errors_table.setItem(row, column, QTableWidgetItem(value))

    def start_sync(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.sync_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.sync_message.setText("Sincronización en curso… podés seguir usando la ventana.")
        self.worker = SyncWorker()
        self.worker.completed.connect(self._sync_completed)
        self.worker.failed.connect(self._sync_failed)
        self.worker.start()

    def _sync_completed(self, summary: SyncSummary) -> None:
        self.sync_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.sync_message.setText(
            f"Resultado: {summary.status.value} · {summary.inserted_count} nuevos · "
            f"{summary.updated_count} actualizados · {summary.error_count} errores"
        )
        self.refresh()

    def _sync_failed(self, error_name: str) -> None:
        self.sync_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.sync_message.setText(f"No se pudo completar la sincronización: {error_name}")
        self.refresh()

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(
                self,
                "Sincronización en curso",
                "Esperá a que termine la sincronización antes de cerrar la ventana.",
            )
            event.ignore()
            return
        super().closeEvent(event)

    def _local_time(self, value: datetime) -> str:
        return value.astimezone(self.timezone).strftime("%d/%m/%Y %H:%M")
