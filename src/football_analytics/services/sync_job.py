"""Run the application's canonical full sync from its local interface."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock, Thread

from football_analytics.config.paths import is_frozen_build, project_root
from football_analytics.config.settings import get_settings


class SyncJob:
    def __init__(self) -> None:
        self._lock = RLock()
        self._thread: Thread | None = None
        self._state: dict[str, object] = {
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "message": "La actualización completa todavía no se ejecutó desde esta sesión.",
            "lines": _state_deque(),
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            snapshot = dict(self._state)
            snapshot["lines"] = list(self._state["lines"])
            return json.loads(json.dumps(snapshot, ensure_ascii=False))

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._state["status"] == "running":
                raise RuntimeError("La actualización completa ya está en curso.")
            if not get_settings().sync_enabled:
                raise RuntimeError(
                    "La sincronización está deshabilitada en la configuración local."
                )
            root = project_root()
            script = root / "scripts" / "sync_daily.py"
            if not script.is_file():
                raise RuntimeError(
                    "No encuentro el proceso completo de actualización en la carpeta del proyecto."
                )
            python = root / ".venv" / "Scripts" / "python.exe"
            if python.is_file():
                executable = str(python)
            elif not is_frozen_build():
                executable = sys.executable
            else:
                raise RuntimeError(
                    "No encuentro el Python del proyecto. La actualización diaria programada "
                    "sigue disponible desde el proyecto instalado."
                )

            self._state = {
                "status": "running",
                "started_at": datetime.now(UTC).isoformat(),
                "finished_at": None,
                "exit_code": None,
                "message": "Iniciando actualización completa de todas las fuentes configuradas…",
                "lines": _state_deque(),
            }
            self._thread = Thread(
                target=self._run,
                args=(root, executable, script),
                name="football-analytics-full-sync",
                daemon=True,
            )
            self._thread.start()
            return self.snapshot()

    def _append(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        with self._lock:
            lines = self._state["lines"]
            assert isinstance(lines, deque)
            lines.append(line[-700:])
            self._state["message"] = line[-240:]

    def _run(self, root: Path, executable: str, script: Path) -> None:
        log_directory = root / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        log_path = log_directory / "manual_sync.log"
        command = [executable, "-u", str(script), "--refresh-player-statistics"]
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"\n[{datetime.now(UTC).isoformat()}] "
                    "Actualización completa iniciada desde la aplicación.\n"
                )
                log.flush()
                process = subprocess.Popen(
                    command,
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    self._append(line)
                exit_code = process.wait()
            status = (
                "completed"
                if exit_code == 0
                else "partial"
                if exit_code == 2
                else "busy"
                if exit_code == 3
                else "failed"
            )
            message = (
                "Actualización completa terminada sin errores reportados."
                if exit_code == 0
                else (
                    "Ya había una actualización ejecutándose; no se inició otra en paralelo. "
                    "Revisá logs/scheduled_sync.log para seguir la tarea diaria."
                )
                if exit_code == 3
                else (
                    "El proceso terminó con fuentes pendientes o errores. "
                    "Revisá el detalle para ver qué se completó."
                )
                if exit_code == 2
                else (
                    "No se pudo completar la actualización. "
                    "Revisá el último detalle y el registro local."
                )
            )
        except Exception as error:  # Keep failure visible in the UI and persistent log.
            exit_code = 1
            status = "failed"
            message = f"No se pudo ejecutar la actualización: {error}"
            try:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(f"[{datetime.now(UTC).isoformat()}] {message}\n")
            except OSError:
                pass
            self._append(message)

        with self._lock:
            current_lines = self._state["lines"]
            lines = list(current_lines) if isinstance(current_lines, deque) else current_lines
            self._state.update(
                status=status,
                finished_at=datetime.now(UTC).isoformat(),
                exit_code=exit_code,
                message=message,
                lines=lines,
            )


def _state_deque() -> deque[str]:
    return deque(maxlen=80)
