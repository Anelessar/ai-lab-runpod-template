"""Supervision of the one long-running standalone tool.

State lives in a JSON file rather than in the web process, because the Launcher
is restarted (or simply reloaded) far more often than a model server is. A
`Popen` handle in memory meant a refresh made a running tool invisible while the
process itself kept holding the GPU.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .services import http_probe, last_error_line, port_is_open, tail

STATUS_STARTING = "starting"
STATUS_READY = "ready"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"


@dataclass
class ProcessRecord:
    tool_id: str
    name: str
    pid: int
    port: int | None
    path: str = "/"
    health_type: str = "http"
    health_path: str = "/"
    log_path: str = ""
    started_at: float = 0.0
    startup_timeout_s: int = 300
    status: str = STATUS_STARTING
    ready_at: float | None = None
    error: str = ""
    command: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def health_url(self) -> str:
        if not self.port:
            return ""
        return f"http://127.0.0.1:{self.port}{self.health_path}"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProcessManager:
    """One heavy standalone process at a time, tracked on disk."""

    def __init__(self, logs_dir: Path, state_dir: Path, tool_port: int = 7860):
        self.logs_dir = logs_dir
        self.state_dir = state_dir
        self.tool_port = tool_port
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Children spawned by *this* process have to be reaped here, otherwise
        # they linger as zombies and os.kill(pid, 0) keeps reporting them alive.
        # Processes inherited across a Launcher restart are not in this map and
        # are checked by pid alone; init has already reaped them by then.
        self._owned: dict[int, subprocess.Popen] = {}

    # ---------------------------------------------------------------- storage

    @property
    def state_file(self) -> Path:
        return self.state_dir / "process.json"

    @property
    def route_file(self) -> Path:
        """Read by the tool-port proxy, which runs outside this process."""
        return self.state_dir / "tool-route.json"

    def _read(self) -> ProcessRecord | None:
        if not self.state_file.is_file():
            return None
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not raw:
            return None
        known = {item for item in ProcessRecord.__dataclass_fields__}
        return ProcessRecord(**{key: value for key, value in raw.items() if key in known})

    def _write(self, record: ProcessRecord | None) -> None:
        payload = asdict(record) if record else {}
        self._atomic_write(self.state_file, json.dumps(payload, ensure_ascii=False, indent=2))
        route: dict[str, object] = {}
        if record and record.port and record.status in {STATUS_STARTING, STATUS_READY, STATUS_DEGRADED}:
            route = {
                "tool_id": record.tool_id,
                "name": record.name,
                "port": record.port,
                "status": record.status,
                "path": record.path,
            }
        self._atomic_write(self.route_file, json.dumps(route, ensure_ascii=False))

    def _atomic_write(self, path: Path, text: str) -> None:
        temporary = path.with_suffix(path.suffix + ".next")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    # ----------------------------------------------------------------- health

    def _alive(self, pid: int) -> bool:
        owned = self._owned.get(pid)
        if owned is not None:
            return owned.poll() is None
        return pid_alive(pid)

    def _probe(self, record: ProcessRecord) -> bool:
        if record.health_type == "process":
            return True
        if record.health_type == "port":
            return bool(record.port) and port_is_open(record.port)
        if not record.health_url:
            return False
        healthy, _ = http_probe(record.health_url)
        return healthy

    def _refresh(self, record: ProcessRecord) -> ProcessRecord:
        """Recompute status from reality, never from what the UI last saw."""
        log_path = Path(record.log_path) if record.log_path else None
        if not self._alive(record.pid):
            note = last_error_line(tail(log_path)) if log_path else ""
            record.status = STATUS_FAILED if record.ready_at is None else STATUS_STOPPED
            record.error = note or "Процесс завершился."
            return record

        healthy = self._probe(record)
        if healthy:
            if record.ready_at is None:
                record.ready_at = time.time()
            record.status = STATUS_READY
            record.error = ""
            return record

        if record.ready_at is not None:
            record.status = STATUS_DEGRADED
            record.error = "Процесс жив, но health-check не отвечает."
            return record

        elapsed = time.time() - record.started_at
        if elapsed > record.startup_timeout_s:
            note = last_error_line(tail(log_path)) if log_path else ""
            record.status = STATUS_FAILED
            record.error = (
                f"Не ответил на health-check за {record.startup_timeout_s} с. "
                f"{note}".strip()
            )
            return record

        record.status = STATUS_STARTING
        record.error = f"Запускается… {int(elapsed)} с из {record.startup_timeout_s}"
        return record

    # ------------------------------------------------------------------- API

    def current(self) -> dict[str, object] | None:
        """Current tool, or None. Terminal states are reported once then cleared."""
        with self._lock:
            record = self._read()
            if not record:
                return None
            refreshed = self._refresh(record)
            self._write(refreshed)
            return asdict(refreshed) | {"health_url": refreshed.health_url}

    def occupied_by(self) -> str | None:
        """Tool id that still holds the slot (alive process), else None."""
        current = self.current()
        if not current:
            return None
        if current["status"] in {STATUS_FAILED, STATUS_STOPPED}:
            return None
        return str(current["tool_id"])

    def clear(self, tool_id: str | None = None) -> None:
        with self._lock:
            record = self._read()
            if record and tool_id and record.tool_id != tool_id:
                return
            self._write(None)

    def allocate_port(self, preferred: int | None = None) -> int:
        """Internal port for a tool UI. Never the public tool port itself."""
        candidates = [preferred] if preferred else []
        candidates += list(range(17860, 17960))
        for candidate in candidates:
            if not candidate or candidate == self.tool_port:
                continue
            if not port_is_open(candidate):
                return candidate
        raise RuntimeError("Нет свободного внутреннего порта для UI инструмента")

    def start(
        self,
        tool_id: str,
        command: str,
        *,
        name: str = "",
        cwd: Path,
        env: dict[str, str],
        port: int | None,
        path: str = "/",
        health_type: str = "http",
        health_path: str = "/",
        startup_timeout_s: int = 300,
    ) -> ProcessRecord:
        with self._lock:
            occupied = self.occupied_by()
            if occupied and occupied != tool_id:
                raise RuntimeError(
                    f"Сначала остановите {occupied}: одновременно разрешён один standalone-инструмент."
                )
            if occupied == tool_id:
                raise RuntimeError(f"{tool_id} уже запущен.")
            self._write(None)

            log_path = self.logs_dir / f"process-{tool_id}.log"
            header = (
                f"\n===== {datetime.now(UTC).isoformat()} =====\n"
                f"$ {command}\n"
                f"cwd: {cwd}\n"
            )
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(header)
            cwd.mkdir(parents=True, exist_ok=True)
            stream = log_path.open("a", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    ["bash", "-c", command],
                    cwd=cwd,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            finally:
                stream.close()

            self._owned[process.pid] = process
            record = ProcessRecord(
                tool_id=tool_id,
                name=name or tool_id,
                pid=process.pid,
                port=port,
                path=path,
                health_type=health_type,
                health_path=health_path,
                log_path=str(log_path),
                started_at=time.time(),
                startup_timeout_s=startup_timeout_s,
                status=STATUS_STARTING,
                command=command,
            )
            self._write(record)
            return record

    def stop(self, tool_id: str | None = None) -> None:
        with self._lock:
            record = self._read()
            if not record:
                return
            if tool_id and record.tool_id != tool_id:
                raise RuntimeError(f"Сейчас запущен {record.tool_id}, а не {tool_id}")
            if self._alive(record.pid):
                self._terminate(record.pid)
            self._owned.pop(record.pid, None)
            self._write(None)

    def _terminate(self, pid: int) -> None:
        owned = self._owned.get(pid)
        for sig, attempts in ((signal.SIGTERM, 150), (signal.SIGKILL, 50)):
            try:
                os.killpg(os.getpgid(pid), sig)
            except (ProcessLookupError, PermissionError):
                break
            for _ in range(attempts):
                if owned is not None:
                    try:
                        owned.wait(timeout=0.1)
                        return
                    except subprocess.TimeoutExpired:
                        continue
                if not pid_alive(pid):
                    return
                time.sleep(0.1)
        if owned is not None:
            try:
                owned.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def log(self, tool_id: str, limit: int = 20000) -> str:
        return tail(self.logs_dir / f"process-{tool_id}.log", limit)
