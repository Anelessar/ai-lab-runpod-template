"""The shared standalone lifecycle, exercised through fake tools.

These use manifests written inline rather than the real catalogue, so they test
the machinery instead of restating what the catalogue happens to contain today.
"""

import time
from pathlib import Path

import pytest
import yaml

from app.config import Settings
from app.jobs import COMPLETED, FAILED, JobManager
from app.manifest import ManifestRegistry
from app.processes import ProcessManager
from app.projects import ProjectManager
from app.tools import INSTALL_MARKER, ToolManager

CLI_TOOL = {
    "id": "fake-cli",
    "name": "Fake CLI",
    "category": "13 · Speech and audio generation",
    "kind": "standalone",
    "description": "one-shot job",
    "source_url": "https://example.com/fake",
    "repo_url": "https://example.com/fake.git",
    "ref": "a" * 40,
    "verified": "installable",
    "entrypoint": "python fake.py",
    "pipeline": "in → out",
    "install": {"mode": "commands", "commands": ["true"]},
    "models": {"mode": "commands", "commands": ["mkdir -p '{model_dir}/weights'"], "check": ["weights"]},
    "run": {
        "mode": "command",
        "command": "printf 'result' > '{output_dir}/out.txt'; echo {prompt}",
        "fields": [{"name": "prompt", "label": "Prompt", "type": "text"}],
    },
}

UI_TOOL = {
    "id": "fake-ui",
    "name": "Fake UI",
    "category": "13 · Speech and audio generation",
    "kind": "standalone",
    "description": "http ui",
    "source_url": "https://example.com/ui",
    "repo_url": "https://example.com/ui.git",
    "ref": "b" * 40,
    "verified": "installable",
    "entrypoint": "python -m http.server",
    "pipeline": "ui",
    "install": {"mode": "commands", "commands": ["true"]},
    "models": {"mode": "disabled"},
    "launch": {"mode": "process", "command": "python3 -m http.server {port} --bind 127.0.0.1", "startup_timeout_s": 30},
}


@pytest.fixture
def lab(tmp_path: Path):
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    (manifest_dir / "fake.yaml").write_text(
        yaml.safe_dump({"tools": [CLI_TOOL, UI_TOOL]}, allow_unicode=True), encoding="utf-8"
    )
    settings = Settings(
        template_root=tmp_path / "template",
        runtime_root=tmp_path / "runtime",
        manifest_dir=manifest_dir,
        workflow_dir=tmp_path / "workflows",
    )
    settings.ensure_runtime()
    registry = ManifestRegistry(manifest_dir).load()
    jobs = JobManager(settings.logs_dir / "jobs", settings.state_dir / "jobs")
    processes = ProcessManager(settings.logs_dir / "processes", settings.state_dir, settings.tool_port)
    projects = ProjectManager(settings.projects_dir, settings.bridge_dir, settings.state_dir)
    manager = ToolManager(settings, registry, jobs, processes, projects)
    yield manager
    processes.stop()


def wait_for(predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def install(lab: ToolManager, tool_id: str) -> None:
    job = lab.install(tool_id)
    assert wait_for(lambda: lab.jobs.latest_for(tool_id)["status"] in {COMPLETED, FAILED})
    assert lab.jobs.latest_for(tool_id)["status"] == COMPLETED, lab.jobs.read_log(job.id)


def test_next_action_walks_install_then_models_then_run(lab: ToolManager) -> None:
    tool = lab.registry.get("fake-cli")
    assert lab.status(tool)["next_action"]["kind"] == "install"

    install(lab, "fake-cli")
    assert lab.status(tool)["next_action"]["kind"] == "models"

    lab.download_models("fake-cli")
    assert wait_for(lambda: lab.jobs.latest_for("fake-cli")["status"] == COMPLETED)
    assert lab.status(tool)["next_action"]["kind"] == "run"


def test_models_are_not_reported_present_until_the_expected_files_exist(lab: ToolManager) -> None:
    tool = lab.registry.get("fake-cli")
    model_dir = lab.paths("fake-cli")["model"]
    model_dir.mkdir(parents=True)
    (model_dir / "stray.txt").write_text("noise")

    # A stray byte in the folder used to be enough for "Модели: есть".
    status = lab.status(tool)
    assert status["models"] is False
    assert status["models_missing"] == ["weights"]

    (model_dir / "weights").mkdir()
    assert lab.status(tool)["models"] is True


def test_run_refuses_before_the_weights_are_there(lab: ToolManager) -> None:
    install(lab, "fake-cli")
    with pytest.raises(RuntimeError, match="скачайте модели"):
        lab.run("fake-cli", {"prompt": "hello"})


def test_run_writes_into_the_active_project_runs_folder(lab: ToolManager) -> None:
    install(lab, "fake-cli")
    (lab.paths("fake-cli")["model"] / "weights").mkdir(parents=True)

    job = lab.run("fake-cli", {"prompt": "hello world"})
    assert wait_for(lambda: lab.jobs.latest_for("fake-cli")["status"] in {COMPLETED, FAILED})
    assert lab.jobs.latest_for("fake-cli")["status"] == COMPLETED, lab.jobs.read_log(job.id)

    project = lab.projects.path(lab.projects.active())
    produced = sorted((project / "runs" / "fake-cli").rglob("out.txt"))
    assert produced, "the run must leave its artifact in runs/<tool-id>/"
    assert produced[0].read_text(encoding="utf-8") == "result"
    assert lab.jobs.latest_for("fake-cli")["artifacts"], "the job must point at its output folder"


def test_run_field_values_are_quoted_not_interpolated_raw(lab: ToolManager) -> None:
    install(lab, "fake-cli")
    (lab.paths("fake-cli")["model"] / "weights").mkdir(parents=True)

    job = lab.run("fake-cli", {"prompt": "hi; touch /tmp/ai-lab-pwned"})
    assert wait_for(lambda: lab.jobs.latest_for("fake-cli")["status"] == COMPLETED)
    assert "hi; touch /tmp/ai-lab-pwned" in lab.jobs.read_log(job.id)
    assert not Path("/tmp/ai-lab-pwned").exists()


def test_missing_required_field_is_rejected_with_the_field_label(lab: ToolManager) -> None:
    install(lab, "fake-cli")
    (lab.paths("fake-cli")["model"] / "weights").mkdir(parents=True)
    with pytest.raises(ValueError, match="Prompt"):
        lab.run("fake-cli", {"prompt": "  "})


def test_open_url_is_refused_until_the_health_check_passes(lab: ToolManager) -> None:
    install(lab, "fake-ui")
    with pytest.raises(RuntimeError, match="health-check"):
        lab.open_url("fake-ui")


def test_launching_a_ui_publishes_it_on_the_shared_tool_port(lab: ToolManager) -> None:
    install(lab, "fake-ui")
    result = lab.launch("fake-ui")
    try:
        assert result["internal_port"] != lab.settings.tool_port
        assert wait_for(lambda: lab.status(lab.registry.get("fake-ui"))["ready"])
        # The link the user is given is the shared public port, never the
        # private one the tool actually bound.
        assert lab.open_url("fake-ui") == lab.settings.public_port_url(lab.settings.tool_port) + "/"
    finally:
        lab.stop("fake-ui")
    assert lab.status(lab.registry.get("fake-ui"))["running"] is False


def test_install_marker_records_the_pinned_ref(lab: ToolManager) -> None:
    install(lab, "fake-cli")
    marker = lab.paths("fake-cli")["tool"] / INSTALL_MARKER
    assert marker.is_file()
    assert CLI_TOOL["ref"] in marker.read_text(encoding="utf-8")


def test_dashboard_status_does_not_probe_health_once_per_tool(lab: ToolManager, monkeypatch) -> None:
    # The dashboard calls status() for every tool in the catalogue. Without a
    # shared snapshot that is one health probe and one full model-directory
    # walk per card, which on a Pod holding tens of GB of weights makes the
    # page take seconds to render.
    probes = {"count": 0}
    original = lab.processes._current_uncached

    def counted():
        probes["count"] += 1
        return original()

    monkeypatch.setattr(lab.processes, "_current_uncached", counted)
    lab.processes._cached = None

    for tool in lab.registry.all():
        lab.status(tool)

    assert probes["count"] == 1, f"one snapshot expected, made {probes['count']}"


def test_a_failed_launch_offers_a_retry_not_a_starting_spinner(lab: ToolManager) -> None:
    install(lab, "fake-ui")
    lab.processes.start(
        "fake-ui",
        "python3 -c 'import nonexistent_module_for_ai_lab'",
        name="Fake UI",
        cwd=lab.paths("fake-ui")["tool"],
        env={"PATH": "/usr/bin:/bin"},
        port=lab.processes.allocate_port(),
        startup_timeout_s=60,
    )
    tool = lab.registry.get("fake-ui")
    assert wait_for(lambda: lab.status(tool)["process_status"] == "failed")

    status = lab.status(tool)
    # Reporting a dead process as "starting" is the same lie as a RunPod port
    # stuck on Initializing, just moved inside the UI.
    assert status["next_action"]["kind"] == "launch"
    assert status["next_action"]["label"] == "Запустить заново"
    assert "ModuleNotFoundError" in status["process_error"]
