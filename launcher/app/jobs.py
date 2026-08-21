"""Background jobs (install, model download, one-shot runs).

Job records are written to disk as they change so that a Launcher restart, or
simply a browser refresh landing on a fresh worker, still shows the install that
is currently running instead of an empty list.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .services import last_error_line, tail

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
INTERRUPTED = "interrupted"
TERMINAL = {COMPLETED, FAILED, INTERRUPTED}


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
    owner_pid: int = 0
    artifacts: list[str] = field(default_factory=list)


class JobManager:
    def __init__(self, logs_dir: Path, state_dir: Path, max_workers: int = 2):
        self.logs_dir = logs_dir
        self.state_dir = state_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ai-lab-job")
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._load()

    # ---------------------------------------------------------------- storage

    def _record_path(self, job_id: str) -> Path:
        return self.state_dir / f"{job_id}.json"

    def _load(self) -> None:
        known = set(Job.__dataclass_fields__)
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job = Job(**{key: value for key, value in raw.items() if key in known})
            # A job left "running" by a Launcher that is no longer alive can
            # never finish; showing it as running forever is the exact lie this
            # module exists to avoid.
            if job.status in {QUEUED, RUNNING} and not self._owner_alive(job.owner_pid):
                job.status = INTERRUPTED
                job.error = "Launcher был перезапущен во время выполнения задачи."
                self._persist(job)
            self._jobs[job.id] = job

    @staticmethod
    def _owner_alive(pid: int) -> bool:
        if pid <= 0 or pid == os.getpid():
            return pid == os.getpid()
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _persist(self, job: Job) -> None:
        path = self._record_path(job.id)
        temporary = path.with_suffix(".json.next")
        temporary.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    # -------------------------------------------------------------------- API

    def submit(self, kind: str, tool_id: str, task: Callable[[Path], None]) -> Job:
        now = datetime.now(UTC).isoformat()
        job_id = uuid.uuid4().hex[:12]
        log_path = self.logs_dir / f"{job_id}-{tool_id}-{kind}.log"
        job = Job(job_id, kind, tool_id, QUEUED, now, now, str(log_path), owner_pid=os.getpid())
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
        self._executor.submit(self._run, job_id, task, log_path)
        return job

    def _run(self, job_id: str, task: Callable[[Path], None], log_path: Path) -> None:
        self._update(job_id, status=RUNNING)
        try:
            task(log_path)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            # Read the log before appending our own traceback: otherwise the
            # "last error line" is always this exception repeating itself
            # instead of the line from the command that actually broke.
            note = last_error_line(tail(log_path))
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write("\n\nERROR\n")
                stream.write(traceback.format_exc())
            message = str(exc).strip() or exc.__class__.__name__
            if note and note not in message and message not in note:
                message = f"{message} — {note}"
            self._update(job_id, status=FAILED, error=message)
        else:
            self._update(job_id, status=COMPLETED, error="")

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = datetime.now(UTC).isoformat()
            self._persist(job)

    def record_artifacts(self, job_id: str, artifacts: list[str]) -> None:
        self._update(job_id, artifacts=artifacts)

    def all(self) -> list[dict[str, object]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [asdict(job) for job in jobs]

    def latest_for(self, tool_id: str) -> dict[str, object] | None:
        return next((job for job in self.all() if job["tool_id"] == tool_id), None)

    def active_for(self, tool_id: str) -> dict[str, object] | None:
        return next(
            (job for job in self.all() if job["tool_id"] == tool_id and job["status"] not in TERMINAL),
            None,
        )

    def read_log(self, job_id: str) -> str:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        path = Path(job.log_path)
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def prune(self, keep: int = 200) -> None:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            for job in jobs[keep:]:
                self._jobs.pop(job.id, None)
                self._record_path(job.id).unlink(missing_ok=True)


def run_commands(
    commands: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    timeout: int | None = None,
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
                timeout=timeout,
            )
            if completed.returncode:
                raise RuntimeError(
                    f"Команда завершилась с кодом {completed.returncode}: {command}"
                )
