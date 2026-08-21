from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .adapters import (
    InstallPlanner,
    ModelFetcher,
    ToolContext,
    job_adapter,
    launch_adapter,
)
from .config import Settings
from .jobs import Job, JobManager, run_commands
from .manifest import ManifestRegistry, ToolManifest
from .processes import (
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_READY,
    STATUS_STARTING,
    STATUS_STOPPED,
    ProcessManager,
)
from .projects import ProjectManager, human_size
from .services import last_error_line, tail

INSTALL_MARKER = ".ai-lab-installed.json"


def apply_workflow_model_hints(path: Path, workflow) -> None:
    if not workflow.model_hints:
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for hint in workflow.model_hints:
        model = {
            "name": hint.name,
            "url": str(hint.url),
            "directory": hint.directory,
        }
        for node in data.get("nodes", []):
            if node.get("type") != hint.node_type:
                continue
            models = node.setdefault("properties", {}).setdefault("models", [])
            if not any(item.get("name") == hint.name for item in models):
                models.append(model)
                changed = True
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class ToolManager:
    def __init__(
        self,
        settings: Settings,
        registry: ManifestRegistry,
        jobs: JobManager,
        processes: ProcessManager,
        projects: ProjectManager,
    ):
        self.settings = settings
        self.registry = registry
        self.jobs = jobs
        self.processes = processes
        self.projects = projects
        self.installer = InstallPlanner()
        self.models = ModelFetcher()
        # Walking a 67 GB checkpoint folder once per tool per page render is
        # slower than everything else the dashboard does put together.
        self._disk_cache: dict[Path, tuple[float, str]] = {}

    # ---------------------------------------------------------------- context

    def paths(self, tool_id: str) -> dict[str, Path]:
        self.registry.get(tool_id)
        return {
            "tool": self.settings.tools_dir / tool_id,
            "env": self.settings.environments_dir / tool_id,
            "model": self.settings.models_dir / "standalone" / tool_id,
            "workflow": self.settings.workflow_dir / tool_id,
        }

    def context(
        self,
        tool: ToolManifest,
        *,
        port: int | None = None,
        output_dir: Path | None = None,
        values: dict[str, str] | None = None,
    ) -> ToolContext:
        paths = self.paths(tool.id)
        return ToolContext(
            tool=tool,
            tool_dir=paths["tool"],
            env_dir=paths["env"],
            model_dir=paths["model"],
            workflow_dir=paths["workflow"],
            project_dir=self.projects.path(self.projects.active()),
            template_root=self.settings.template_root,
            runtime_root=self.settings.runtime_root,
            port=port,
            output_dir=output_dir,
            values=values or {},
        )

    def is_installed(self, tool: ToolManifest) -> bool:
        if tool.kind in {"comfyui", "modifier", "web"}:
            return True
        return (self.paths(tool.id)["tool"] / INSTALL_MARKER).exists()

    # ----------------------------------------------------------------- status

    def status(self, tool: ToolManifest) -> dict[str, object]:
        paths = self.paths(tool.id)
        context = self.context(tool)
        running = self.processes.current()
        mine = bool(running and running["tool_id"] == tool.id)
        installed = self.is_installed(tool)
        models_present = self.models.present(context)
        active_job = self.jobs.active_for(tool.id)
        latest = self.jobs.latest_for(tool.id)

        process_status = str(running["status"]) if mine else ""
        return {
            "installed": installed,
            "models": models_present,
            "models_missing": self.models.missing(context),
            "models_size_gb": tool.models.size_gb or tool.download_gb,
            "workflows": paths["workflow"].exists() and any(paths["workflow"].glob("*.json")),
            "running": mine and process_status not in {STATUS_FAILED, STATUS_STOPPED},
            "ready": mine and process_status == STATUS_READY,
            "process_status": process_status,
            "process_error": str(running["error"]) if mine else "",
            "running_process": running,
            # Derived from the snapshot above rather than re-probing, so
            # rendering the whole catalogue costs one health check in total.
            "slot_taken_by": self._slot_owner(running),
            "active_job": active_job,
            "latest_job": latest,
            "adapter": tool.adapter_type,
            "disk": self._disk(paths["tool"]),
            "model_disk": self._disk(paths["model"]),
            "next_action": self.next_action(tool, installed, models_present, mine, process_status, active_job),
        }

    @staticmethod
    def _slot_owner(running: dict[str, object] | None) -> str | None:
        if not running or running["status"] in {STATUS_FAILED, STATUS_STOPPED}:
            return None
        return str(running["tool_id"])

    def _disk(self, path: Path, ttl: float = 30.0) -> str:
        if not path.exists():
            return "0 B"
        cached = self._disk_cache.get(path)
        now = time.monotonic()
        if cached and now - cached[0] < ttl:
            return cached[1]
        size = human_size(sum(item.stat().st_size for item in path.rglob("*") if item.is_file()))
        self._disk_cache[path] = (now, size)
        return size

    def next_action(
        self,
        tool: ToolManifest,
        installed: bool,
        models_present: bool,
        mine: bool,
        process_status: str,
        active_job: dict[str, object] | None,
    ) -> dict[str, str]:
        """The single button the dashboard should offer, and nothing else."""
        if tool.verified == "unavailable":
            return {"kind": "blocked", "label": "Недоступно", "reason": tool.unavailable_reason}
        if active_job:
            return {"kind": "wait", "label": f"Идёт {active_job['kind']}", "reason": ""}
        if tool.adapter_type in {"comfyui", "web"}:
            return {"kind": "open", "label": "Открыть", "reason": ""}
        if not tool.is_automatable:
            return {
                "kind": "manual",
                "label": "Только ручной запуск",
                "reason": tool.install.instructions or "Автоматическая установка не проверена.",
            }
        if not installed:
            return {"kind": "install", "label": "Установить программу", "reason": ""}
        if tool.models.mode not in {"disabled", "manual"} and not models_present:
            size = tool.models.size_gb or tool.download_gb
            suffix = f" (~{size:g} GB)" if size else ""
            return {"kind": "models", "label": f"Скачать модели{suffix}", "reason": ""}
        if mine and process_status == STATUS_READY:
            return {"kind": "open-ui", "label": "Открыть UI", "reason": ""}
        # A process that already died must never read as "starting" - that is
        # the same lie as a port stuck on "Initializing", just inside the UI.
        if mine and process_status in {STATUS_STARTING, STATUS_DEGRADED}:
            return {"kind": "wait", "label": "Запускается…", "reason": ""}
        if tool.has_ui:
            label = "Запустить заново" if process_status == STATUS_FAILED else "Запустить"
            return {"kind": "launch", "label": label, "reason": ""}
        if tool.has_job:
            return {"kind": "run", "label": "Запустить тест", "reason": ""}
        return {"kind": "none", "label": "", "reason": ""}

    # ---------------------------------------------------------------- install

    def install(self, tool_id: str) -> Job:
        tool = self.registry.get(tool_id)
        if tool.verified == "unavailable":
            raise RuntimeError(tool.unavailable_reason)
        if tool.install.mode in {"disabled", "manual"}:
            raise RuntimeError(tool.install.instructions or "Автоматическая установка не настроена")

        def task(log_path: Path) -> None:
            context = self.context(tool)
            tool_dir = context.tool_dir
            if (tool_dir / INSTALL_MARKER).exists():
                self._append(log_path, "Программа уже установлена.\n")
                return
            if tool.install.mode == "git-auto":
                self._clone(tool, tool_dir, log_path)
            else:
                tool_dir.mkdir(parents=True, exist_ok=True)
            context.env_dir.mkdir(parents=True, exist_ok=True)
            commands = self.installer.commands(context)
            self._append(log_path, f"\nУстановочные команды: {commands}\n")
            run_commands(commands, cwd=tool_dir, env=self.environment(context), log_path=log_path)
            (tool_dir / INSTALL_MARKER).write_text(
                json.dumps(
                    {
                        "tool": tool.id,
                        "ref": tool.ref,
                        "commit": self._commit(tool_dir),
                        "installed_at": datetime.now(UTC).isoformat(),
                        "commands": commands,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        return self.jobs.submit("install", tool_id, task)

    def _clone(self, tool: ToolManifest, tool_dir: Path, log_path: Path) -> None:
        if tool_dir.exists() and any(tool_dir.iterdir()):
            raise RuntimeError(f"{tool_dir} уже существует и не является готовой установкой")
        tool_dir.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            for command in (
                ["git", "clone", "--filter=blob:none", str(tool.repo_url), str(tool_dir)],
                ["git", "-C", str(tool_dir), "checkout", tool.ref],
                ["git", "-C", str(tool_dir), "submodule", "update", "--init", "--recursive"],
            ):
                stream.write(f"\n$ {' '.join(command)}\n")
                stream.flush()
                subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, text=True, check=True)

    def _commit(self, tool_dir: Path) -> str:
        if not (tool_dir / ".git").exists():
            return ""
        try:
            return subprocess.check_output(
                ["git", "-C", str(tool_dir), "rev-parse", "HEAD"], text=True
            ).strip()
        except subprocess.CalledProcessError:
            return ""

    def _append(self, log_path: Path, text: str) -> None:
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(text)

    # ----------------------------------------------------------------- models

    def download_models(self, tool_id: str) -> Job:
        tool = self.registry.get(tool_id)
        if tool.models.mode in {"disabled", "manual"}:
            raise RuntimeError(tool.models.instructions or "Модели скачиваются вручную")
        if tool.models.requires_hf_token and not os.getenv("HF_TOKEN"):
            raise RuntimeError(
                "Нужен HF_TOKEN. Добавьте RunPod Secret huggingface_token в шаблон Pod и перезапустите его."
            )

        def task(log_path: Path) -> None:
            context = self.context(tool)
            context.model_dir.mkdir(parents=True, exist_ok=True)
            commands = self.models.commands(context)
            if not commands:
                raise RuntimeError("В manifest не указано, что именно скачивать")
            run_commands(
                commands,
                cwd=context.tool_dir if context.tool_dir.exists() else self.settings.runtime_root,
                env=self.environment(context),
                log_path=log_path,
            )
            missing = self.models.missing(context)
            if missing:
                raise RuntimeError(f"После загрузки не хватает файлов: {', '.join(missing)}")

        return self.jobs.submit("models", tool_id, task)

    # -------------------------------------------------------------- workflows

    def download_workflows(self, tool_id: str) -> Job:
        tool = self.registry.get(tool_id)
        if not tool.workflows:
            raise RuntimeError("Для инструмента не указаны workflow")

        def task(log_path: Path) -> None:
            paths = self.paths(tool_id)
            paths["workflow"].mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as stream:
                for workflow in tool.workflows:
                    destination = paths["workflow"] / f"{workflow.name}.json"
                    if workflow.url:
                        stream.write(f"Downloading {workflow.url} -> {destination}\n")
                        urllib.request.urlretrieve(str(workflow.url), destination)
                    elif workflow.local_file:
                        source = (self.settings.template_root / workflow.local_file).resolve()
                        shutil.copy2(source, destination)
                    apply_workflow_model_hints(destination, workflow)

        return self.jobs.submit("workflows", tool_id, task)

    # ----------------------------------------------------------------- launch

    def launch(self, tool_id: str) -> dict[str, object]:
        tool = self.registry.get(tool_id)
        adapter = launch_adapter(tool)
        if not adapter.needs_process:
            return {"url": adapter.url(self.context(tool), self.settings), "external": True}

        self._require_ready_to_run(tool)
        port = self.processes.allocate_port(tool.launch.port)
        context = self.context(tool, port=port)
        spec = adapter.spec(context)
        record = self.processes.start(
            tool.id,
            spec.command,
            name=tool.name,
            cwd=context.tool_dir,
            env=self.environment(context),
            port=spec.port,
            path=spec.path,
            health_type=spec.health_type,
            health_path=spec.health_path,
            startup_timeout_s=spec.startup_timeout_s,
        )
        return {
            "url": adapter.url(context, self.settings),
            "external": True,
            "pid": record.pid,
            "internal_port": record.port,
        }

    def _require_ready_to_run(self, tool: ToolManifest) -> None:
        if tool.verified == "unavailable":
            raise RuntimeError(tool.unavailable_reason)
        if not self.is_installed(tool):
            raise RuntimeError("Сначала установите программу")
        context = self.context(tool)
        if tool.models.mode not in {"disabled", "manual"} and not self.models.present(context):
            missing = ", ".join(self.models.missing(context)) or "модели"
            raise RuntimeError(f"Сначала скачайте модели: не хватает {missing}")

    def open_url(self, tool_id: str) -> str:
        tool = self.registry.get(tool_id)
        adapter = launch_adapter(tool)
        context = self.context(tool)
        if adapter.needs_process:
            running = self.processes.current()
            if not running or running["tool_id"] != tool.id or running["status"] != STATUS_READY:
                raise RuntimeError("Инструмент ещё не прошёл health-check — ссылка появится после запуска")
        return adapter.url(context, self.settings)

    # -------------------------------------------------------------------- run

    def run(self, tool_id: str, values: dict[str, str]) -> Job:
        tool = self.registry.get(tool_id)
        adapter = job_adapter(tool)
        if adapter.type != "cli-job":
            raise RuntimeError("Для инструмента пока нет формы запуска")
        if tool.kind == "standalone":
            self._require_ready_to_run(tool)

        prepared = self._prepare_fields(tool, values)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        project_dir = self.projects.path(self.projects.active())
        output_dir = project_dir / "runs" / tool.id / stamp
        output_dir.mkdir(parents=True, exist_ok=True)

        context = self.context(tool, output_dir=output_dir, values=prepared)
        spec = adapter.spec(context)
        env = self.environment(context)

        def task(log_path: Path) -> None:
            run_commands(
                [spec.command],
                cwd=context.tool_dir,
                env=env,
                log_path=log_path,
                timeout=spec.timeout_s,
            )

        job = self.jobs.submit("run", tool_id, task)
        self.jobs.record_artifacts(job.id, [str(output_dir.relative_to(project_dir))])
        return job

    def _prepare_fields(self, tool: ToolManifest, values: dict[str, str]) -> dict[str, str]:
        prepared: dict[str, str] = {}
        active = self.projects.active()
        for field in tool.run.fields:
            raw = (values.get(field.name) or field.default).strip()
            if field.required and not raw:
                raise ValueError(f"Заполните поле «{field.label}»")
            if field.type == "file" and raw:
                raw = str(self.projects.resolve_asset(active, raw))
            elif field.type == "number" and raw:
                float(raw)
            elif field.type == "select" and raw and field.choices and raw not in field.choices:
                raise ValueError(f"Недопустимое значение поля «{field.label}»: {raw}")
            prepared[field.name] = shlex.quote(raw)
        return prepared

    # ------------------------------------------------------------------ stop

    def stop(self, tool_id: str) -> None:
        self.processes.stop(tool_id)

    def dismiss(self, tool_id: str) -> None:
        self.processes.clear(tool_id)

    def process_log(self, tool_id: str) -> str:
        return self.processes.log(tool_id)

    def process_error(self, tool_id: str) -> str:
        return last_error_line(tail(self.settings.logs_dir / "processes" / f"process-{tool_id}.log"))

    # ---------------------------------------------------------------- cleanup

    def delete_program(self, tool_id: str) -> None:
        tool = self.registry.get(tool_id)
        if self.processes.occupied_by() == tool.id:
            raise RuntimeError("Сначала остановите инструмент")
        paths = self.paths(tool_id)
        self._safe_delete(paths["tool"], self.settings.tools_dir)
        self._safe_delete(paths["env"], self.settings.environments_dir)

    def delete_models(self, tool_id: str) -> None:
        self.registry.get(tool_id)
        self._safe_delete(self.paths(tool_id)["model"], self.settings.models_dir)

    def _safe_delete(self, path: Path, allowed_root: Path) -> None:
        resolved_root = allowed_root.resolve()
        resolved = path.resolve()
        if resolved == resolved_root or resolved_root not in resolved.parents:
            raise RuntimeError(f"Refusing to delete unsafe path: {resolved}")
        if path.exists():
            shutil.rmtree(path)

    # ------------------------------------------------------------ environment

    def environment(self, context: ToolContext) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "AI_LAB_ROOT": str(self.settings.runtime_root),
                "AI_LAB_TOOL_ID": context.tool.id,
                "AI_LAB_TOOL_DIR": str(context.tool_dir),
                "AI_LAB_ENV_DIR": str(context.env_dir),
                "AI_LAB_MODEL_DIR": str(context.model_dir),
                "AI_LAB_PROJECT_DIR": str(context.project_dir),
                "HF_HOME": str(self.settings.cache_dir / "huggingface"),
                "HF_HUB_ENABLE_HF_TRANSFER": os.getenv("HF_HUB_ENABLE_HF_TRANSFER", "0"),
                "MODELSCOPE_CACHE": str(self.settings.cache_dir / "modelscope"),
                "UV_PROJECT_ENVIRONMENT": str(context.env_dir),
                "UV_CACHE_DIR": str(self.settings.cache_dir / "uv"),
                "PYTHONUNBUFFERED": "1",
            }
        )
        if context.port:
            env["PORT"] = str(context.port)
            env["GRADIO_SERVER_PORT"] = str(context.port)
            env["GRADIO_SERVER_NAME"] = "0.0.0.0"
        if context.output_dir is not None:
            env["AI_LAB_OUTPUT_DIR"] = str(context.output_dir)
        env["PATH"] = f"{context.env_dir / 'bin'}:{env.get('PATH', '')}"
        return env
