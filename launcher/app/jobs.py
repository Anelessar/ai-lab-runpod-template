from __future__ import annotations

import subprocess
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Job:
    id: str
    kind: str
    tool_id: str
    status: str
    created_at: str
    updated_at: str
    log_path: str
    error: str = ""


class JobManager:
    def __init__(self, logs_dir: Path, max_workers: int = 2):
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ai-lab-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def submit(self, kind: str, tool_id: str, task: Callable[[Path], None]) -> Job:
        now = datetime.now(UTC).isoformat()
        job_id = uuid.uuid4().hex[:12]
        log_path = self.logs_dir / f"{job_id}-{tool_id}-{kind}.log"
        job = Job(job_id, kind, tool_id, "queued", now, now, str(log_path))
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job_id, task, log_path)
        return job

    def _run(self, job_id: str, task: Callable[[Path], None], log_path: Path) -> None:
        self._update(job_id, status="running")
        try:
            task(log_path)
        except Exception as exc:  # noqa: BLE001 - stored for the UI
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write("\n\nERROR\n")
                stream.write(traceback.format_exc())
            self._update(job_id, status="failed", error=str(exc))
        else:
            self._update(job_id, status="completed")

    def _update(self, job_id: str, **changes: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = datetime.now(UTC).isoformat()

    def all(self) -> list[dict[str, str]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [asdict(job) for job in jobs]

    def latest_for(self, tool_id: str) -> dict[str, str] | None:
        return next((job for job in self.all() if job["tool_id"] == tool_id), None)

    def read_log(self, job_id: str) -> str:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        path = Path(job.log_path)
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def run_commands(
    commands: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        for command in commands:
            stream.write(f"\n$ {command}\n")
            stream.flush()
            completed = subprocess.run(
                ["bash", "-c", command],
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError(f"Command failed with exit code {completed.returncode}: {command}")
