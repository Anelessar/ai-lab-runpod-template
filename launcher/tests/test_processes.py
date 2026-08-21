"""Supervision of the single long-running standalone tool.

The bug these were written against: the running tool lived in a `Popen` handle
inside the web process, so a Launcher restart made a running tool invisible
while the process itself kept holding the GPU.
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.processes import (
    STATUS_FAILED,
    STATUS_READY,
    STATUS_STARTING,
    ProcessManager,
    pid_alive,
)


def manager(tmp_path: Path) -> ProcessManager:
    return ProcessManager(tmp_path / "logs", tmp_path / "state", tool_port=7860)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("content-length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        return


@pytest.fixture
def healthy_port():
    server = HTTPServer(("127.0.0.1", 0), Ok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_only_one_standalone_process_runs_at_a_time(tmp_path: Path) -> None:
    supervisor = manager(tmp_path)
    first = supervisor.start(
        "first", "sleep 30", cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, port=free_port()
    )
    try:
        assert first.pid > 0
        with pytest.raises(RuntimeError, match="один standalone"):
            supervisor.start(
                "second", "sleep 30", cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, port=free_port()
            )
    finally:
        supervisor.stop("first")
    assert supervisor.current() is None
    assert not pid_alive(first.pid)


def test_a_running_tool_survives_a_launcher_restart(tmp_path: Path, healthy_port: int) -> None:
    supervisor = manager(tmp_path)
    record = supervisor.start(
        "demo", "sleep 30", cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, port=healthy_port
    )
    try:
        # A brand new manager stands in for the Launcher being restarted or the
        # request landing on a different worker.
        fresh = manager(tmp_path)
        current = fresh.current()
        assert current is not None
        assert current["tool_id"] == "demo"
        assert current["pid"] == record.pid
        assert current["status"] == STATUS_READY
    finally:
        supervisor.stop("demo")


def test_health_failure_becomes_failed_not_eternal_starting(tmp_path: Path) -> None:
    supervisor = manager(tmp_path)
    # Nothing ever listens on this port, so health can never pass.
    supervisor.start(
        "silent",
        "sleep 30",
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        port=free_port(),
        startup_timeout_s=0,
    )
    try:
        current = supervisor.current()
        assert current["status"] == STATUS_FAILED
        assert "health-check" in current["error"]
    finally:
        supervisor.stop("silent")


def test_a_process_that_exits_reports_the_error_from_its_log(tmp_path: Path) -> None:
    supervisor = manager(tmp_path)
    supervisor.start(
        "broken",
        "echo 'ModuleNotFoundError: No module named torch' >&2; exit 1",
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        port=free_port(),
    )
    assert wait_for(lambda: (supervisor.current() or {}).get("status") == STATUS_FAILED)
    current = supervisor.current()
    assert "ModuleNotFoundError" in current["error"]
    assert "ModuleNotFoundError" in supervisor.log("broken")


def test_route_file_tracks_the_tool_the_public_port_should_serve(tmp_path: Path) -> None:
    supervisor = manager(tmp_path)
    port = free_port()
    assert json.loads(supervisor.route_file.read_text()) == {} if supervisor.route_file.exists() else True

    supervisor.start("demo", "sleep 30", cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, port=port)
    try:
        route = json.loads(supervisor.route_file.read_text(encoding="utf-8"))
        assert route["tool_id"] == "demo"
        assert route["port"] == port
    finally:
        supervisor.stop("demo")
    assert json.loads(supervisor.route_file.read_text(encoding="utf-8")) == {}


def test_starting_state_is_reported_while_the_tool_boots(tmp_path: Path) -> None:
    supervisor = manager(tmp_path)
    supervisor.start(
        "slow",
        "sleep 30",
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        port=free_port(),
        startup_timeout_s=600,
    )
    try:
        current = supervisor.current()
        assert current["status"] == STATUS_STARTING
        assert "Запускается" in current["error"]
    finally:
        supervisor.stop("slow")


def test_allocated_ports_never_collide_with_the_public_tool_port(tmp_path: Path) -> None:
    supervisor = manager(tmp_path)
    assert supervisor.allocate_port() != 7860
    assert supervisor.allocate_port(7860) != 7860
