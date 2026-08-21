"""The shared public tool port.

Its whole job is to never look dead: it answers before any tool is installed,
while one is starting, and after one has been stopped.
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
from app.toolport import ToolPortProxy


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"tool says {self.path}".encode()
        self.send_response(200)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


@pytest.fixture
def upstream():
    server = HTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def client_for(route_file: Path) -> httpx.AsyncClient:
    proxy = ToolPortProxy(route_file, launcher_url="http://localhost:3000")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url="http://tool-port")


@pytest.mark.anyio
async def test_health_answers_even_with_no_route_file(tmp_path: Path) -> None:
    async with client_for(tmp_path / "missing.json") as client:
        response = await client.get("/__ai_lab_health")
    assert response.status_code == 200
    assert response.json()["tool"] is None


@pytest.mark.anyio
async def test_placeholder_page_instead_of_a_dead_port(tmp_path: Path) -> None:
    route = tmp_path / "tool-route.json"
    route.write_text("{}", encoding="utf-8")
    async with client_for(route) as client:
        response = await client.get("/")
    assert response.status_code == 503
    assert "Launcher" in response.text


@pytest.mark.anyio
async def test_requests_reach_the_running_tool_at_its_own_root(tmp_path: Path, upstream: int) -> None:
    route = tmp_path / "tool-route.json"
    route.write_text(
        f'{{"tool_id": "demo", "name": "Demo", "port": {upstream}, "status": "ready", "path": "/"}}',
        encoding="utf-8",
    )
    async with client_for(route) as client:
        response = await client.get("/queue/status?x=1")
    # The tool sees "/queue/status", not a prefixed path, so Gradio and
    # Streamlit need no root-path configuration to work behind this port.
    assert response.status_code == 200
    assert response.text == "tool says /queue/status?x=1"


@pytest.mark.anyio
async def test_unreachable_tool_reports_why_instead_of_hanging(tmp_path: Path) -> None:
    route = tmp_path / "tool-route.json"
    route.write_text('{"tool_id": "demo", "name": "Demo", "port": 1, "status": "starting"}', encoding="utf-8")
    async with client_for(route) as client:
        response = await client.get("/")
    assert response.status_code == 502
    assert "Demo" in response.text
