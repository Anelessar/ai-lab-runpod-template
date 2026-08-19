from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_hugging_face_downloader_is_pinned_in_image() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "COMFYUI_HF_DOWNLOADER_REF="
        "2bba5db6a52479e8ad465dbade19dd0da0784bd3"
    ) in dockerfile
    assert "jnxmx/ComfyUI_HuggingFace_Downloader.git" in dockerfile
    assert (
        "/opt/ComfyUI/custom_nodes/ComfyUI_HuggingFace_Downloader/requirements.txt"
        in dockerfile
    )


def test_comfyui_manager_is_pinned_in_image() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "COMFYUI_MANAGER_REF="
        "f39cbd56fecae0b27a446c0cd450cd591f3a8bea"
    ) in dockerfile
    assert "Comfy-Org/ComfyUI-Manager.git" in dockerfile
    assert "/opt/ComfyUI/custom_nodes/comfyui-manager" in dockerfile
    assert (
        "/opt/ComfyUI/custom_nodes/comfyui-manager/requirements.txt"
        in dockerfile
    )


def test_runpod_template_maps_existing_hf_secret() -> None:
    script = (ROOT / "scripts" / "create-runpod-template.sh").read_text(
        encoding="utf-8"
    )

    assert 'AI_LAB_HF_SECRET_NAME:-huggingface_token' in script
    assert '"HF_TOKEN":"{{ RUNPOD_SECRET_%s }}"' in script


def test_comfyui_public_port_has_startup_gateway() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    gateway = (ROOT / "docker" / "comfyui-gateway.conf").read_text(
        encoding="utf-8"
    )

    assert "nginx" in dockerfile
    assert "nginx -t" in dockerfile
    assert "Starting ComfyUI gateway on port 8188" in entrypoint
    assert "--port 8189" in entrypoint
    assert "proxy_pass http://127.0.0.1:8189" in gateway
    assert "proxy_set_header Upgrade $http_upgrade" in gateway
    assert "ComfyUI запускается" in gateway


def test_dashboard_health_and_project_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_LAB_ROOT", str(tmp_path / "bootstrap-runtime"))
    from app.main import create_app

    settings = Settings(
        template_root=ROOT,
        runtime_root=tmp_path / "runtime",
        manifest_dir=ROOT / "manifests",
        workflow_dir=ROOT / "workflows" / "comfyui",
    )
    with TestClient(create_app(settings)) as client:
        health = client.get("/health")
        dashboard = client.get("/")
        created = client.post("/projects", data={"name": "Real Test"}, follow_redirects=True)

    assert health.status_code == 200
    assert health.json()["tools"] >= 35
    assert dashboard.status_code == 200
    assert "AI Lab" in dashboard.text
    assert "Ideogram 4" in dashboard.text
    assert "JoyAI-Video-Edit" in dashboard.text
    assert "Workflow: 15 доступно" in dashboard.text
    assert "Скачать workflow (15)" in dashboard.text
    assert "freefuse_zimage_complete" not in dashboard.text
    assert "запуск готов" not in dashboard.text
    assert "нужен первый ручной тест" not in dashboard.text
    assert dashboard.text.index("1 · Image generation") < dashboard.text.index("2 · Image editing")
    assert dashboard.text.index("2 · Image editing") < dashboard.text.index("12 · Acceleration")
    assert created.status_code == 200
    assert "real-test" in created.text
