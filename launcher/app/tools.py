from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .jobs import Job, JobManager, run_commands
from .manifest import ManifestRegistry, ToolManifest
from .processes import ProcessManager
from .projects import ProjectManager, human_size


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

    def paths(self, tool_id: str) -> dict[str, Path]:
        self.registry.get(tool_id)
        return {
            "tool": self.settings.tools_dir / tool_id,
            "env": self.settings.environments_dir / tool_id,
            "model": self.settings.models_dir / "standalone" / tool_id,
            "workflow": self.settings.workflow_dir / tool_id,
        }

    def status(self, tool: ToolManifest) -> dict[str, object]:
        paths = self.paths(tool.id)
        running = self.processes.current()
        latest = self.jobs.latest_for(tool.id)
        return {
            "installed": (paths["tool"] / ".ai-lab-installed.json").exists()
            or tool.kind in {"comfyui", "modifier", "web"},
            "models": paths["model"].exists() and any(paths["model"].rglob("*")),
            "workflows": paths["workflow"].exists() and any(paths["workflow"].glob("*.json")),
            "running": bool(running and running["tool_id"] == tool.id),
            "running_process": running,
            "latest_job": latest,
            "disk": human_size(sum(path.stat().st_size for path in paths["tool"].rglob("*") if path.is_file()))
            if paths["tool"].exists()
            else "0 B",
            "model_disk": human_size(sum(path.stat().st_size for path in paths["model"].rglob("*") if path.is_file()))
            if paths["model"].exists()
            else "0 B",
        }

    def install(self, tool_id: str) -> Job:
        tool = self.registry.get(tool_id)
        if tool.install.mode in {"disabled", "manual"}:
            raise RuntimeError(tool.install.instructions or "Автоматическая установка не настроена")

        def task(log_path: Path) -> None:
            paths = self.paths(tool_id)
            tool_dir = paths["tool"]
            env_dir = paths["env"]
            if (tool_dir / ".ai-lab-installed.json").exists():
                with log_path.open("a", encoding="utf-8") as stream:
                    stream.write("Program is already installed.\n")
                return
            if tool.install.mode == "git-auto":
                if tool_dir.exists() and any(tool_dir.iterdir()):
                    raise RuntimeError(f"{tool_dir} уже существует и не является готовой установкой")
                tool_dir.parent.mkdir(parents=True, exist_ok=True)
                clone = ["git", "clone", "--filter=blob:none", "--recursive", str(tool.repo_url), str(tool_dir)]
                with log_path.open("a", encoding="utf-8") as stream:
                    subprocess.run(clone, stdout=stream, stderr=subprocess.STDOUT, text=True, check=True)
                    subprocess.run(
                        ["git", "-C", str(tool_dir), "checkout", tool.ref],
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        text=True,
                        check=True,
                    )
                    subprocess.run(
                        ["git", "-C", str(tool_dir), "submodule", "update", "--init", "--recursive"],
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        text=True,
                        check=True,
                    )
                commands = tool.install.commands or self._auto_install_commands(tool_dir)
            else:
                tool_dir.mkdir(parents=True, exist_ok=True)
                commands = tool.install.commands
            env_dir.mkdir(parents=True, exist_ok=True)
            rendered = [self._render(command, tool, paths) for command in commands]
            run_commands(rendered, cwd=tool_dir, env=self._environment(tool, paths), log_path=log_path)
            commit = ""
            if (tool_dir / ".git").exists():
                commit = subprocess.check_output(
                    ["git", "-C", str(tool_dir), "rev-parse", "HEAD"], text=True
                ).strip()
            (tool_dir / ".ai-lab-installed.json").write_text(
                json.dumps({"tool": tool.id, "ref": tool.ref, "commit": commit}, indent=2),
                encoding="utf-8",
            )

        return self.jobs.submit("install", tool_id, task)

    def download_models(self, tool_id: str) -> Job:
        tool = self.registry.get(tool_id)
        if tool.models.mode in {"disabled", "manual"}:
            raise RuntimeError(tool.models.instructions or "Модели скачиваются вручную")

        def task(log_path: Path) -> None:
            paths = self.paths(tool_id)
            paths["model"].mkdir(parents=True, exist_ok=True)
            commands = list(tool.models.commands)
            if tool.models.mode == "huggingface":
                for repo_id in tool.models.repo_ids:
                    destination = paths["model"] / repo_id.replace("/", "--")
                    commands.append(f"hf download {repo_id} --local-dir '{destination}'")
            run_commands(
                [self._render(command, tool, paths) for command in commands],
                cwd=paths["tool"] if paths["tool"].exists() else self.settings.runtime_root,
                env=self._environment(tool, paths),
                log_path=log_path,
            )

        return self.jobs.submit("models", tool_id, task)

    def download_workflows(self, tool_id: str) -> Job:
        tool = self.registry.get(tool_id)
        if not tool.workflows:
            raise RuntimeError("Для инструмента не указаны workflow")

        def task(log_path: Path) -> None:
            paths = self.paths(tool_id)
            paths["workflow"].mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as stream:
                for workflow in tool.workflows:
                    filename = f"{workflow.name}.json"
                    destination = paths["workflow"] / filename
                    if workflow.url:
                        stream.write(f"Downloading {workflow.url} -> {destination}\n")
                        urllib.request.urlretrieve(str(workflow.url), destination)
                    elif workflow.local_file:
                        source = (self.settings.template_root / workflow.local_file).resolve()
                        shutil.copy2(source, destination)
                    apply_workflow_model_hints(destination, workflow)

        return self.jobs.submit("workflows", tool_id, task)

    def launch(self, tool_id: str) -> dict[str, object]:
        tool = self.registry.get(tool_id)
        if tool.launch.mode == "comfyui":
            return {"url": self.settings.comfyui_url, "external": True}
        if tool.launch.mode == "web":
            return {"url": str(tool.source_url), "external": True}
        if tool.launch.mode != "process":
            raise RuntimeError("Для инструмента пока нет автоматического запуска")
        paths = self.paths(tool_id)
        if not (paths["tool"] / ".ai-lab-installed.json").exists():
            raise RuntimeError("Сначала установите программу")
        command = self._render(tool.launch.command, tool, paths)
        running = self.processes.start(
            tool_id,
            command,
            cwd=paths["tool"],
            env=self._environment(tool, paths),
            port=tool.launch.port,
        )
        return {
            "url": self.settings.public_port_url(running.port) + tool.launch.path
            if running.port
            else "",
            "external": True,
        }

    def run(self, tool_id: str, values: dict[str, str]) -> Job:
        tool = self.registry.get(tool_id)
        if tool.run.mode != "command":
            raise RuntimeError("Для инструмента пока нет формы запуска")
        paths = self.paths(tool_id)
        if tool.kind == "standalone" and not (paths["tool"] / ".ai-lab-installed.json").exists():
            raise RuntimeError("Сначала установите программу")
        prepared: dict[str, str] = {}
        for field in tool.run.fields:
            raw = values.get(field.name, field.default).strip()
            if field.required and not raw:
                raise ValueError(f"Заполните поле «{field.label}»")
            if field.type == "file" and raw:
                raw = str(self.projects.resolve_asset(self.projects.active(), raw))
            elif field.type == "number" and raw:
                float(raw)
            prepared[field.name] = shlex.quote(raw)

        project_dir = self.projects.path(self.projects.active())
        output_dir = project_dir / "runs" / tool.id
        output_dir.mkdir(parents=True, exist_ok=True)
        prepared.update(
            {
                "output_dir": shlex.quote(str(output_dir)),
                "timestamp": datetime.now(UTC).strftime("%Y%m%d-%H%M%S"),
            }
        )
        command = self._render(tool.run.command, tool, paths)
        for marker, replacement in prepared.items():
            command = command.replace("{" + marker + "}", replacement)

        def task(log_path: Path) -> None:
            run_commands(
                [command],
                cwd=paths["tool"],
                env=self._environment(tool, paths),
                log_path=log_path,
            )

        return self.jobs.submit("run", tool_id, task)

    def stop(self, tool_id: str) -> None:
        self.processes.stop(tool_id)

    def delete_program(self, tool_id: str) -> None:
        tool = self.registry.get(tool_id)
        running = self.processes.current()
        if running and running["tool_id"] == tool.id:
            raise RuntimeError("Сначала остановите инструмент")
        paths = self.paths(tool_id)
        self._safe_delete(paths["tool"], self.settings.tools_dir)
        self._safe_delete(paths["env"], self.settings.environments_dir)

    def delete_models(self, tool_id: str) -> None:
        self.registry.get(tool_id)
        path = self.paths(tool_id)["model"]
        self._safe_delete(path, self.settings.models_dir)

    def _safe_delete(self, path: Path, allowed_root: Path) -> None:
        resolved_root = allowed_root.resolve()
        resolved = path.resolve()
        if resolved == resolved_root or resolved_root not in resolved.parents:
            raise RuntimeError(f"Refusing to delete unsafe path: {resolved}")
        if path.exists():
            shutil.rmtree(path)

    def _auto_install_commands(self, tool_dir: Path) -> list[str]:
        if (tool_dir / "pyproject.toml").exists() or (tool_dir / "uv.lock").exists():
            return ["uv sync"]
        if (tool_dir / "requirements.txt").exists():
            return [
                "uv venv '{env_dir}' --python 3.11",
                "uv pip install --python '{env_dir}/bin/python' -r requirements.txt",
            ]
        return []

    def _environment(self, tool: ToolManifest, paths: dict[str, Path]) -> dict[str, str]:
        env = os.environ.copy()
        project_dir = self.projects.path(self.projects.active())
        env.update(
            {
                "AI_LAB_ROOT": str(self.settings.runtime_root),
                "AI_LAB_TOOL_ID": tool.id,
                "AI_LAB_TOOL_DIR": str(paths["tool"]),
                "AI_LAB_ENV_DIR": str(paths["env"]),
                "AI_LAB_MODEL_DIR": str(paths["model"]),
                "AI_LAB_PROJECT_DIR": str(project_dir),
                "HF_HOME": str(self.settings.runtime_root / "cache" / "huggingface"),
                "MODELSCOPE_CACHE": str(self.settings.runtime_root / "cache" / "modelscope"),
                "UV_PROJECT_ENVIRONMENT": str(paths["env"]),
                "PORT": str(tool.launch.port or 7860),
            }
        )
        env["PATH"] = f"{paths['env'] / 'bin'}:{env.get('PATH', '')}"
        return env

    def _render(self, value: str, tool: ToolManifest, paths: dict[str, Path]) -> str:
        replacements = {
            "{template_root}": str(self.settings.template_root),
            "{tool_dir}": str(paths["tool"]),
            "{env_dir}": str(paths["env"]),
            "{model_dir}": str(paths["model"]),
            "{workflow_dir}": str(paths["workflow"]),
            "{project_dir}": str(self.projects.path(self.projects.active())),
            "{port}": str(tool.launch.port or 7860),
        }
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
        return value
