"""Background jobs must outlive the web process that started them."""

import time
from pathlib import Path

from app.jobs import COMPLETED, FAILED, INTERRUPTED, JobManager


def manager(tmp_path: Path) -> JobManager:
    return JobManager(tmp_path / "logs", tmp_path / "state")


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_finished_jobs_are_still_listed_after_a_restart(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    job = jobs.submit("install", "demo", lambda log_path: log_path.write_text("done"))
    assert wait_for(lambda: jobs.latest_for("demo")["status"] == COMPLETED)

    reloaded = manager(tmp_path)
    latest = reloaded.latest_for("demo")
    assert latest["id"] == job.id
    assert latest["status"] == COMPLETED
    assert reloaded.read_log(job.id) == "done"


def test_a_job_orphaned_by_a_restart_is_marked_interrupted(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    job = jobs.submit("install", "demo", lambda log_path: log_path.write_text("x"))
    assert wait_for(lambda: jobs.latest_for("demo")["status"] == COMPLETED)

    # Rewrite the record the way a Launcher killed mid-install would leave it:
    # status "running", owned by a pid that no longer exists.
    record = jobs.state_dir / f"{job.id}.json"
    record.write_text(
        record.read_text(encoding="utf-8")
        .replace(f'"status": "{COMPLETED}"', '"status": "running"')
        .replace(f'"owner_pid": {job.owner_pid}', '"owner_pid": 999999'),
        encoding="utf-8",
    )

    reloaded = manager(tmp_path)
    latest = reloaded.latest_for("demo")
    assert latest["status"] == INTERRUPTED
    assert "перезапущен" in latest["error"]


def test_failure_carries_the_error_line_from_the_log(tmp_path: Path) -> None:
    jobs = manager(tmp_path)

    def failing(log_path: Path) -> None:
        log_path.write_text("cloning...\nModuleNotFoundError: No module named torch\n")
        raise RuntimeError("Команда завершилась с кодом 1")

    jobs.submit("install", "demo", failing)
    assert wait_for(lambda: jobs.latest_for("demo")["status"] == FAILED)
    error = jobs.latest_for("demo")["error"]
    assert "кодом 1" in error
    assert "ModuleNotFoundError" in error


def test_active_job_blocks_a_second_action_on_the_same_tool(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    release = {"go": False}

    def slow(log_path: Path) -> None:
        while not release["go"]:
            time.sleep(0.01)

    jobs.submit("models", "demo", slow)
    try:
        assert wait_for(lambda: jobs.active_for("demo") is not None)
        assert jobs.active_for("demo")["kind"] == "models"
    finally:
        release["go"] = True
    assert wait_for(lambda: jobs.active_for("demo") is None)
