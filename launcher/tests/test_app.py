from pathlib import Path

from app.config import Settings
from fastapi.testclient import TestClient

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
        services = client.get("/api/services")
        dashboard = client.get("/")
        created = client.post("/projects", data={"name": "Real Test"}, follow_redirects=True)

    assert health.status_code == 200
    assert health.json()["tools"] >= 35
    assert services.status_code == 200
    assert services.json()["ports"] == {
        "launcher": 3000,
        "comfyui": 8188,
        "jupyter": 8888,
        "tools": 7860,
    }
    assert {item["port"] for item in services.json()["services"]} == {7860, 8188, 8888}
    assert dashboard.status_code == 200
    assert "AI Lab" in dashboard.text
    assert "Ideogram 4" in dashboard.text
    assert "JoyAI-Video-Edit" in dashboard.text
    assert "Порты Pod" in dashboard.text
    assert "Workflow: 15 доступно" in dashboard.text
    assert "Скачать workflow (15)" in dashboard.text
    assert "freefuse_zimage_complete" not in dashboard.text
    assert "запуск готов" not in dashboard.text
    assert "нужен первый ручной тест" not in dashboard.text
    assert dashboard.text.index("1 · Image generation") < dashboard.text.index("2 · Image editing")
    assert dashboard.text.index("2 · Image editing") < dashboard.text.index("12 · Acceleration")
    assert created.status_code == 200
    assert "real-test" in created.text


def test_dashboard_offers_install_before_launch_for_a_standalone_tool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_LAB_ROOT", str(tmp_path / "bootstrap-runtime"))
    from app.main import create_app

    settings = Settings(
        template_root=ROOT,
        runtime_root=tmp_path / "runtime",
        manifest_dir=ROOT / "manifests",
        workflow_dir=ROOT / "workflows" / "comfyui",
    )
    with TestClient(create_app(settings)) as client:
        dashboard = client.get("/")
        # Nothing is installed in a fresh runtime, so no tool may present an
        # "open it" link, and a not-yet-launched UI must not be linkable.
        opened = client.get("/tools/indextts-2-5/open", follow_redirects=False)

    assert "Установить программу" in dashboard.text
    assert opened.status_code == 303
    assert "health-check" in opened.headers["location"]


def test_jupyter_link_carries_the_token_the_pod_generated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_LAB_ROOT", str(tmp_path / "bootstrap-runtime"))
    from app.main import create_app

    settings = Settings(
        template_root=ROOT,
        runtime_root=tmp_path / "runtime",
        manifest_dir=ROOT / "manifests",
        workflow_dir=ROOT / "workflows" / "comfyui",
    )
    settings.ensure_runtime()
    (settings.state_dir / "jupyter-token.txt").write_text("s3cret-token", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        dashboard = client.get("/")

    # Without this the RunPod "Connect" button lands on a login form and the
    # port looks broken even though JupyterLab is running.
    assert "/lab?token=s3cret-token" in dashboard.text
