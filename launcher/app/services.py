"""Health of the fixed infrastructure services (ComfyUI, Jupyter, tool port).

RunPod renders a port as "Initializing" for as long as its HTTP proxy cannot
reach the container port. Nothing in the Pod ever tells the user *why*. These
probes give the Launcher a truthful answer for every port the template
declares, so a dead service reads as dead instead of as "starting".
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

PROBE_TIMEOUT = 2.0
LOG_TAIL_BYTES = 4000


@dataclass
class ServiceState:
    key: str
    name: str
    port: int
    url: str
    status: str  # ready | starting | failed | disabled
    detail: str = ""
    log_name: str = ""


def tail(path: Path, limit: int = LOG_TAIL_BYTES) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size > limit:
            stream.seek(size - limit)
        return stream.read().decode("utf-8", errors="replace")


def last_error_line(text: str) -> str:
    """Pick the line a human should read first out of a noisy stdout log."""
    markers = (
        "Traceback (most recent call last)",
        "Error:",
        "ERROR",
        "error:",
        "CUDA out of memory",
        "ModuleNotFoundError",
        "ImportError",
        "FileNotFoundError",
        "No such file or directory",
        "Address already in use",
        "command not found",
        "Killed",
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(marker in line for marker in markers):
            return line[:400]
    return lines[-1][:400] if lines else ""


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(PROBE_TIMEOUT)
        return probe.connect_ex((host, port)) == 0


def http_probe(url: str) -> tuple[bool, str]:
    """True when the endpoint answers at all.

    A redirect or a 401/403 still proves the socket belongs to a live HTTP
    server, which is exactly the question RunPod's proxy asks. Only a refused
    connection or a 5xx means the service is not up yet.
    """
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT) as response:
            return response.status < 500, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return exc.code < 500, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(getattr(exc, "reason", exc))[:200]


class ServiceProbe:
    def __init__(self, settings, processes=None):
        self.settings = settings
        self.processes = processes

    def jupyter_token(self) -> str:
        path = self.settings.state_dir / "jupyter-token.txt"
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def jupyter_url(self) -> str:
        base = self.settings.public_port_url(8888) + "/lab"
        token = self.jupyter_token()
        return f"{base}?token={token}" if token else base

    def _service_log(self, name: str) -> Path:
        return self.settings.logs_dir / f"{name}.log"

    def comfyui(self) -> ServiceState:
        ok, detail = http_probe("http://127.0.0.1:8188/")
        status = "ready" if ok else "starting"
        if not ok:
            note = last_error_line(tail(self._service_log("comfyui")))
            marker = self.settings.state_dir / "comfyui-failed"
            if marker.exists():
                status = "failed"
                detail = marker.read_text(encoding="utf-8").strip() or detail
            if note:
                detail = f"{detail} · {note}" if detail else note
        return ServiceState(
            key="comfyui",
            name="ComfyUI",
            port=8188,
            url=self.settings.public_port_url(8188),
            status=status,
            detail=detail,
            log_name="comfyui",
        )

    def jupyter(self) -> ServiceState:
        ok, detail = http_probe("http://127.0.0.1:8888/api")
        note = "" if ok else last_error_line(tail(self._service_log("jupyter")))
        return ServiceState(
            key="jupyter",
            name="JupyterLab",
            port=8888,
            url=self.jupyter_url(),
            status="ready" if ok else "starting",
            detail=detail if ok else (note or detail),
            log_name="jupyter",
        )

    def tool_port(self) -> ServiceState:
        ok, detail = http_probe(f"http://127.0.0.1:{self.settings.tool_port}/__ai_lab_health")
        running = self.processes.current() if self.processes else None
        if running:
            detail = f"{running['tool_id']} · {running['status']}"
        elif ok:
            detail = "свободен"
        return ServiceState(
            key="tool-port",
            name="Порт инструментов",
            port=self.settings.tool_port,
            url=self.settings.public_port_url(self.settings.tool_port),
            status="ready" if ok else "starting",
            detail=detail,
            log_name="tool-port",
        )

    def all(self) -> list[dict[str, object]]:
        return [asdict(state) for state in (self.comfyui(), self.jupyter(), self.tool_port())]

    def as_json(self) -> str:
        return json.dumps(self.all(), ensure_ascii=False)
