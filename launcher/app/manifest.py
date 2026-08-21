from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, HttpUrl, model_validator

TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CATEGORY_NUMBER = re.compile(r"^\s*(\d+)\s*[·.]?")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

# How far a standalone tool has actually been taken.
#
#   unavailable  - upstream does not let an ordinary user run this at all
#   catalogued   - known and described, but only a manual, hand-driven setup
#   installable  - AI Lab automates install and weights using the commands the
#                  pinned upstream commit documents for itself; not yet started
#                  on a GPU
#   launchable   - the launch command was started in a Pod and passed health
#   smoke-tested - a real end-to-end run produced an artifact in a project
#
# Nothing above "catalogued" may be claimed from a manifest entry alone.
VERIFIED_LEVELS = ("unavailable", "catalogued", "installable", "launchable", "smoke-tested")
AUTOMATABLE = {"installable", "launchable", "smoke-tested"}
RUNNABLE = {"launchable", "smoke-tested"}

VERIFIED_LABELS = {
    "unavailable": "недоступно",
    "catalogued": "только вручную",
    "installable": "ставится автоматически, запуск на GPU ещё не проверен",
    "launchable": "запуск проверен",
    "smoke-tested": "проверен полный прогон",
}


def category_sort_key(category: str) -> tuple[int, str]:
    match = CATEGORY_NUMBER.match(category)
    number = int(match.group(1)) if match else 10_000
    return number, category.casefold()


class CommandAction(BaseModel):
    mode: Literal["disabled", "manual", "commands", "git-auto"] = "disabled"
    commands: list[str] = Field(default_factory=list)
    instructions: str = ""
    python_version: str = "3.11"


class ModelFile(BaseModel):
    """A weight that is fetched by direct URL rather than by HF repo id."""

    url: HttpUrl
    path: str
    size_gb: float | None = None


class ModelAction(BaseModel):
    mode: Literal["disabled", "manual", "commands", "huggingface"] = "disabled"
    repo_ids: list[str] = Field(default_factory=list)
    files: list[ModelFile] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    instructions: str = ""
    size_gb: float | None = None
    requires_hf_token: bool = False
    # Relative paths under the tool's model directory that must exist before
    # the weights count as downloaded. Without this a single stray byte in the
    # folder was enough for the UI to claim "модели: есть".
    check: list[str] = Field(default_factory=list)


class LaunchAction(BaseModel):
    mode: Literal["disabled", "web", "process", "comfyui"] = "disabled"
    command: str = ""
    # Left unset the Launcher allocates a private loopback port and publishes
    # the UI through the shared public tool port.
    port: int | None = None
    path: str = "/"
    health_path: str = "/"
    health_type: Literal["http", "port", "process"] = "http"
    startup_timeout_s: int = 600
    ui: Literal["gradio", "streamlit", "fastapi", "websocket", "other"] = "other"


class RunField(BaseModel):
    name: str
    label: str
    type: Literal["text", "textarea", "number", "file", "select"] = "text"
    required: bool = True
    default: str = ""
    choices: list[str] = Field(default_factory=list)
    help: str = ""


class RunAction(BaseModel):
    mode: Literal["disabled", "command"] = "disabled"
    command: str = ""
    fields: list[RunField] = Field(default_factory=list)
    timeout_s: int = 10_800
    # A short command that proves the environment imports and the entrypoint
    # parses arguments, without touching weights or a GPU.
    smoke_command: str = ""


class SmokeEvidence(BaseModel):
    """Proof that somebody actually ran this, not that it looks runnable."""

    date: str
    gpu: str
    duration_s: int | None = None
    notes: str = ""


class WorkflowModelHint(BaseModel):
    node_type: str
    name: str
    url: HttpUrl
    directory: str


class WorkflowSource(BaseModel):
    name: str
    url: HttpUrl | None = None
    local_file: str | None = None
    model_hints: list[WorkflowModelHint] = Field(default_factory=list)


class ToolManifest(BaseModel):
    id: str
    name: str
    category: str
    kind: Literal["comfyui", "standalone", "modifier", "web"]
    description: str
    source_url: HttpUrl
    repo_url: HttpUrl | None = None
    ref: str = "main"
    priority: Literal["must", "mid", "trash"] = "must"
    access: Literal["free", "paid", "unavailable"] = "free"
    adapter_status: Literal["ready", "catalogued", "manual"] = "catalogued"
    verified: Literal[VERIFIED_LEVELS] = "catalogued"  # type: ignore[valid-type]
    unavailable_reason: str = ""
    license_note: str = "Проверить лицензию перед коммерческим использованием."
    license_spdx: str = ""
    entrypoint: str = ""
    vram_gb: float | None = None
    download_gb: float | None = None
    requirements: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    pipeline: str = ""
    install: CommandAction = Field(default_factory=CommandAction)
    models: ModelAction = Field(default_factory=ModelAction)
    launch: LaunchAction = Field(default_factory=LaunchAction)
    run: RunAction = Field(default_factory=RunAction)
    smoke: SmokeEvidence | None = None
    workflows: list[WorkflowSource] = Field(default_factory=list)

    # --------------------------------------------------------------- helpers

    @property
    def is_automatable(self) -> bool:
        """Whether the manifest actually wires up install/launch/run."""
        return (
            self.install.mode not in {"disabled", "manual"}
            or self.launch.mode in {"process", "comfyui", "web"}
            or self.run.mode == "command"
        )

    @property
    def verified_label(self) -> str:
        return VERIFIED_LABELS.get(self.verified, self.verified)

    @property
    def has_ui(self) -> bool:
        return self.launch.mode == "process"

    @property
    def has_job(self) -> bool:
        return self.run.mode == "command"

    @property
    def adapter_type(self) -> str:
        if self.kind == "comfyui" or self.launch.mode == "comfyui":
            return "comfyui"
        if self.launch.mode == "web":
            return "web"
        if self.launch.mode == "process":
            return "http-ui"
        if self.run.mode == "command":
            return "cli-job"
        if self.models.mode in {"huggingface", "commands"} and self.install.mode == "disabled":
            return "hf-download"
        return "none"

    # ------------------------------------------------------------ validation

    @model_validator(mode="after")
    def validate_manifest(self) -> ToolManifest:
        errors: list[str] = []
        if not TOOL_ID.fullmatch(self.id):
            errors.append(f"некорректный id: {self.id}")
        if self.launch.mode == "process" and not self.launch.command:
            errors.append("launch.mode=process требует command")
        if self.install.mode == "git-auto" and not self.repo_url:
            errors.append("install.mode=git-auto требует repo_url")
        if self.run.mode == "command" and not self.run.command:
            errors.append("run.mode=command требует command")
        if self.verified == "unavailable" and not self.unavailable_reason:
            errors.append("verified=unavailable требует unavailable_reason")

        if self.verified in AUTOMATABLE:
            if self.install.mode in {"disabled", "manual"} and self.kind == "standalone":
                errors.append(f"verified={self.verified} требует автоматической установки")
            if self.install.mode == "git-auto" and not COMMIT_SHA.fullmatch(self.ref):
                errors.append(
                    f"verified={self.verified} требует закреплённый commit sha в ref, а не «{self.ref}»"
                )
            if not self.entrypoint:
                errors.append(f"verified={self.verified} требует поле entrypoint с реальной командой")

        if self.verified in RUNNABLE and not (self.has_ui or self.has_job):
            errors.append(f"verified={self.verified} требует launch.mode=process или run.mode=command")

        if self.verified == "smoke-tested" and self.smoke is None:
            errors.append("verified=smoke-tested требует блок smoke с датой и GPU")

        # `adapter_status: ready` is what the dashboard renders as a green
        # chip. For a standalone tool that promise is only allowed once the
        # tool is actually launchable.
        if self.adapter_status == "ready":
            if self.kind == "standalone" and self.verified not in RUNNABLE:
                errors.append(
                    "adapter_status=ready для standalone требует verified=launchable или smoke-tested"
                )
            if self.kind in {"comfyui", "modifier"} and not self.workflows and self.launch.mode != "comfyui":
                errors.append("adapter_status=ready требует workflows либо launch.mode=comfyui")

        if errors:
            raise ValueError(f"{self.id}: " + "; ".join(errors))
        return self


class Catalog(BaseModel):
    tools: list[ToolManifest]


class ManifestRegistry:
    def __init__(self, manifest_dir: Path):
        self.manifest_dir = manifest_dir
        self._tools: dict[str, ToolManifest] = {}

    def load(self) -> ManifestRegistry:
        tools: dict[str, ToolManifest] = {}
        for path in sorted(self.manifest_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            entries = raw.get("tools", [raw])
            for entry in entries:
                tool = ToolManifest.model_validate(entry)
                if tool.id in tools:
                    raise ValueError(f"Duplicate tool id {tool.id} in {path}")
                tools[tool.id] = tool
        self._tools = tools
        return self

    def all(self) -> list[ToolManifest]:
        return sorted(
            self._tools.values(),
            key=lambda item: (category_sort_key(item.category), item.name.casefold()),
        )

    def standalone(self) -> list[ToolManifest]:
        return [tool for tool in self.all() if tool.kind == "standalone"]

    def get(self, tool_id: str) -> ToolManifest:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {tool_id}") from exc

    def categories(self) -> list[str]:
        return sorted({tool.category for tool in self._tools.values()}, key=category_sort_key)
