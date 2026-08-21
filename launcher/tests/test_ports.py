"""Invariants for the ports the template asks RunPod to publish.

Every bug these cover was reported as "the port never opens" or "it says
Initializing forever", so they are written against the template files rather
than against a running Pod.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
TEMPLATE_SCRIPT = (ROOT / "scripts" / "create-runpod-template.sh").read_text(encoding="utf-8")

PUBLISHED_PORTS = {3000, 7860, 8188, 8888}


def declared_expose_ports() -> set[int]:
    line = next(item for item in DOCKERFILE.splitlines() if item.startswith("EXPOSE "))
    return {int(port) for port in line.removeprefix("EXPOSE ").split()}


def declared_template_ports() -> set[int]:
    match = re.search(r'--ports "([^"]+)"', TEMPLATE_SCRIPT)
    assert match, "create-runpod-template.sh must declare --ports"
    return {int(item.split("/")[0]) for item in match.group(1).split(",")}


def test_dockerfile_and_template_declare_the_same_ports() -> None:
    assert declared_expose_ports() == PUBLISHED_PORTS
    assert declared_template_ports() == PUBLISHED_PORTS


def test_no_port_is_declared_for_an_on_demand_tool() -> None:
    # 8001 and 8080 belonged to OmniVoice and JoyAI, which only start when the
    # user launches them; RunPod showed both as Initializing for the whole Pod.
    assert 8001 not in declared_expose_ports()
    assert 8080 not in declared_expose_ports()
    assert 8001 not in declared_template_ports()
    assert 8080 not in declared_template_ports()


def test_every_published_port_has_a_service_started_at_boot() -> None:
    started = {
        3000: "--port \"$LAUNCHER_PORT\"",
        7860: "--port \"$TOOL_PORT\"",
        8188: "--port \"$COMFYUI_PORT\"",
        8888: "--port=\"$JUPYTER_PORT\"",
    }
    for port, needle in started.items():
        assert needle in ENTRYPOINT, f"port {port} is published but nothing starts on it"
    for port in PUBLISHED_PORTS:
        assert f"{port}\n" in ENTRYPOINT or f"={port}" in ENTRYPOINT


def test_comfyui_still_listens_directly_on_its_public_port() -> None:
    assert "nginx" not in DOCKERFILE
    assert "COMFYUI_PORT=8188" in ENTRYPOINT
    assert "Starting ComfyUI on public port" in ENTRYPOINT
    assert "8189" not in ENTRYPOINT


def test_cuda_wait_is_bounded_so_comfyui_cannot_hang_forever() -> None:
    assert "CUDA_WAIT_SECONDS" in ENTRYPOINT
    assert "SECONDS >= deadline" in ENTRYPOINT
    # On timeout ComfyUI still has to come up, otherwise 8188 stays dark and
    # nobody ever learns that the Pod got no usable GPU.
    assert "--cpu" in ENTRYPOINT
    assert "comfyui-failed" in ENTRYPOINT


def test_jupyter_is_reachable_through_the_runpod_proxy() -> None:
    assert "--ServerApp.allow_origin='*'" in ENTRYPOINT
    assert "--ServerApp.allow_remote_access=True" in ENTRYPOINT
    assert "--ServerApp.trust_xheaders=True" in ENTRYPOINT
    assert "--ip=0.0.0.0" in ENTRYPOINT


def test_jupyter_token_is_written_where_the_launcher_can_read_it() -> None:
    assert "jupyter-token.txt" in ENTRYPOINT
    assert "JUPYTER_PASSWORD" in ENTRYPOINT, "RunPod sets JUPYTER_PASSWORD, not JUPYTER_TOKEN"
    assert "umask 077" in ENTRYPOINT


def test_stale_tool_route_is_cleared_on_boot() -> None:
    assert "tool-route.json" in ENTRYPOINT
    assert "process.json" in ENTRYPOINT
