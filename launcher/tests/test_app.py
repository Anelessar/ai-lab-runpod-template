from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]


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
