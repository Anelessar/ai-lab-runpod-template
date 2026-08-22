"""Declarative adapters for the standalone tool lifecycle.

Every standalone tool goes through the same seven steps - install, models,
launch/run, health, logs, stop, artifacts. The only thing that differs between
tools is *how* a step is expressed, so the differences live in the manifest and
in these few small strategy classes. Nothing here branches on a tool id.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .manifest import ToolManifest


@dataclass
class ToolContext:
    """Everything a rendered command may refer to."""

    tool: ToolManifest
    tool_dir: Path
    env_dir: Path
    model_dir: Path
    workflow_dir: Path
    project_dir: Path
    template_root: Path
    runtime_root: Path
    port: int | None = None
    output_dir: Path | None = None
    values: dict[str, str] = field(default_factory=dict)

    def placeholders(self) -> dict[str, str]:
        markers = {
            "template_root": str(self.template_root),
            "runtime_root": str(self.runtime_root),
            "tool_dir": str(self.tool_dir),
            "env_dir": str(self.env_dir),
            "model_dir": str(self.model_dir),
            "workflow_dir": str(self.workflow_dir),
            "project_dir": str(self.project_dir),
            "python": str(self.env_dir / "bin" / "python"),
            "port": str(self.port or self.tool.launch.port or 7860),
            "timestamp": datetime.now(UTC).strftime("%Y%m%d-%H%M%S"),
        }
        if self.output_dir is not None:
            markers["output_dir"] = str(self.output_dir)
        markers.update(self.values)
        return markers

    def render(self, value: str) -> str:
        for marker, replacement in self.placeholders().items():
            value = value.replace("{" + marker + "}", replacement)
        return value


class InstallPlanner:
    """Turns install.mode into a concrete command list.

    `git-auto` with no explicit commands falls back to whatever the cloned
    repository actually ships, which is how the pinned upstream projects
    document their own setup (uv project, requirements.txt, or a package).
    """

    def commands(self, context: ToolContext) -> list[str]:
        tool = context.tool
        if tool.install.commands:
            return [context.render(command) for command in tool.install.commands]
        return [context.render(command) for command in self.autodetect(context)]

    def autodetect(self, context: ToolContext) -> list[str]:
        tool_dir = context.tool_dir
        install = context.tool.install
        python = install.python_version
        # A uv project brings its own lock and its own torch index; overriding
        # it here would defeat the point of installing a pinned commit.
        if (tool_dir / "uv.lock").exists() or (tool_dir / "pyproject.toml").exists():
            return ["uv sync"]
        if (tool_dir / "requirements.txt").exists():
            return [
                f"uv venv '{{env_dir}}' --python {python}",
                (
                    "uv pip install --python '{env_dir}/bin/python' "
                    f"--torch-backend {install.torch_backend} -r requirements.txt"
                ),
            ]
        return [f"uv venv '{{env_dir}}' --python {python}"]


class ModelFetcher:
    """models.mode -> commands, plus an honest 'are the weights here?' check."""

    def commands(self, context: ToolContext) -> list[str]:
        tool = context.tool
        commands = [context.render(command) for command in tool.models.commands]
        if tool.models.mode == "huggingface":
            for repo_id in tool.models.repo_ids:
                destination = context.model_dir / self.local_name(repo_id)
                # `hf download` resumes and skips files that already match,
                # so re-running the button never re-downloads tens of GB.
                commands.append(f"hf download {shlex.quote(repo_id)} --local-dir {shlex.quote(str(destination))}")
        for item in tool.models.files:
            destination = context.model_dir / item.path
            commands.append(
                f"mkdir -p {shlex.quote(str(destination.parent))} && "
                f"curl -fL --retry 3 --retry-delay 2 -C - -o {shlex.quote(str(destination))} "
                f"{shlex.quote(str(item.url))}"
            )
        return commands

    @staticmethod
    def local_name(repo_id: str) -> str:
        return repo_id.replace("/", "--")

    def expected_paths(self, context: ToolContext) -> list[Path]:
        tool = context.tool
        expected = [context.model_dir / item for item in tool.models.check]
        if not expected and tool.models.mode == "huggingface":
            expected = [context.model_dir / self.local_name(repo) for repo in tool.models.repo_ids]
        if not expected:
            expected = [context.model_dir / item.path for item in tool.models.files]
        return expected

    @staticmethod
    def _has_content(path: Path) -> bool:
        """A download counts only once it has actually produced bytes.

        `hf download --local-dir X` creates X before it does any work, so a
        failed download still leaves the directory behind. Treating that as
        "weights present" is how a tool ends up offering Launch and then dying
        on a missing checkpoint.
        """
        if path.is_file():
            return path.stat().st_size > 0
        if path.is_dir():
            return any(item.is_file() for item in path.rglob("*"))
        return False

    def present(self, context: ToolContext) -> bool:
        expected = self.expected_paths(context)
        if not expected:
            return False
        return all(self._has_content(path) for path in expected)

    def missing(self, context: ToolContext) -> list[str]:
        return [
            path.name
            for path in self.expected_paths(context)
            if not self._has_content(path)
        ]


@dataclass
class LaunchSpec:
    command: str
    port: int | None
    path: str
    health_type: str
    health_path: str
    startup_timeout_s: int


class LaunchAdapter:
    """Base: a tool that has no process to launch."""

    type = "none"
    needs_process = False

    def spec(self, context: ToolContext) -> LaunchSpec:
        raise RuntimeError("Для инструмента нет автоматического запуска")

    def url(self, context: ToolContext, settings) -> str:
        return str(context.tool.source_url)


class ComfyUiAdapter(LaunchAdapter):
    type = "comfyui"

    def url(self, context: ToolContext, settings) -> str:
        return settings.comfyui_url


class WebAdapter(LaunchAdapter):
    type = "web"


class HttpUiAdapter(LaunchAdapter):
    """Long-lived HTTP UI (Gradio / Streamlit / FastAPI) behind the tool port."""

    type = "http-ui"
    needs_process = True

    def spec(self, context: ToolContext) -> LaunchSpec:
        launch = context.tool.launch
        return LaunchSpec(
            command=context.render(launch.command),
            port=context.port,
            path=launch.path,
            health_type=launch.health_type,
            health_path=launch.health_path,
            startup_timeout_s=launch.startup_timeout_s,
        )

    def url(self, context: ToolContext, settings) -> str:
        # Always the shared public tool port - the private port the tool binds
        # is never published by RunPod.
        base = settings.public_port_url(settings.tool_port)
        path = context.tool.launch.path
        return base + (path if path.startswith("/") else f"/{path}")


@dataclass
class JobSpec:
    command: str
    output_dir: Path
    timeout_s: int


class JobAdapter:
    type = "none"
    accepts_fields = False

    def spec(self, context: ToolContext) -> JobSpec:
        raise RuntimeError("Для инструмента нет формы запуска")


class CliJobAdapter(JobAdapter):
    """One-shot CLI/Python job writing into runs/<tool-id>/<stamp>/."""

    type = "cli-job"
    accepts_fields = True

    def spec(self, context: ToolContext) -> JobSpec:
        run = context.tool.run
        return JobSpec(
            command=context.render(run.command),
            output_dir=context.output_dir or context.project_dir / "runs" / context.tool.id,
            timeout_s=run.timeout_s,
        )


LAUNCH_ADAPTERS: dict[str, LaunchAdapter] = {
    "comfyui": ComfyUiAdapter(),
    "web": WebAdapter(),
    "http-ui": HttpUiAdapter(),
    "hf-download": LaunchAdapter(),
    "cli-job": LaunchAdapter(),
    "none": LaunchAdapter(),
}

JOB_ADAPTERS: dict[str, JobAdapter] = {
    "cli-job": CliJobAdapter(),
}


def launch_adapter(tool: ToolManifest) -> LaunchAdapter:
    return LAUNCH_ADAPTERS.get(tool.adapter_type, LAUNCH_ADAPTERS["none"])


def job_adapter(tool: ToolManifest) -> JobAdapter:
    if tool.run.mode == "command":
        return JOB_ADAPTERS["cli-job"]
    return JobAdapter()
