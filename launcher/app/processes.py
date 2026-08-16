from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunningProcess:
    tool_id: str
    pid: int
    port: int | None
    log_path: Path
    process: subprocess.Popen[str]


class ProcessManager:
    """Keeps ComfyUI separate and allows one heavy standalone process at a time."""

    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._running: RunningProcess | None = None
        self._lock = threading.RLock()

    def current(self) -> dict[str, object] | None:
        with self._lock:
            running = self._running
            if running and running.process.poll() is not None:
                self._running = None
                return None
            if not running:
                return None
            return {"tool_id": running.tool_id, "pid": running.pid, "port": running.port}

    def start(
        self,
        tool_id: str,
        command: str,
        *,
        cwd: Path,
        env: dict[str, str],
        port: int | None,
    ) -> RunningProcess:
        with self._lock:
            current = self.current()
            if current:
                raise RuntimeError(
                    f"Сначала остановите {current['tool_id']}: одновременно разрешён один standalone-инструмент."
                )
            log_path = self.logs_dir / f"process-{tool_id}.log"
            stream = log_path.open("a", encoding="utf-8")
            process = subprocess.Popen(
                ["bash", "-c", command],
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            stream.close()
            self._running = RunningProcess(tool_id, process.pid, port, log_path, process)
            return self._running

    def stop(self, tool_id: str | None = None) -> None:
        with self._lock:
            running = self._running
            if not running:
                return
            if tool_id and running.tool_id != tool_id:
                raise RuntimeError(f"Сейчас запущен {running.tool_id}, а не {tool_id}")
            if running.process.poll() is None:
                os.killpg(running.pid, signal.SIGTERM)
                try:
                    running.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(running.pid, signal.SIGKILL)
                    running.process.wait(timeout=5)
            self._running = None

    def log(self, tool_id: str) -> str:
        path = self.logs_dir / f"process-{tool_id}.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
