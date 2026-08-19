from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, HttpUrl, model_validator

TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CATEGORY_NUMBER = re.compile(r"^\s*(\d+)\s*[·.]?")


def category_sort_key(category: str) -> tuple[int, str]:
    match = CATEGORY_NUMBER.match(category)
    number = int(match.group(1)) if match else 10_000
    return number, category.casefold()


class CommandAction(BaseModel):
    mode: Literal["disabled", "manual", "commands", "git-auto"] = "disabled"
    commands: list[str] = Field(default_factory=list)
    instructions: str = ""


class ModelAction(BaseModel):
    mode: Literal["disabled", "manual", "commands", "huggingface"] = "disabled"
    repo_ids: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    instructions: str = ""
    size_gb: float | None = None


class LaunchAction(BaseModel):
    mode: Literal["disabled", "web", "process", "comfyui"] = "disabled"
    command: str = ""
    port: int | None = None
    path: str = "/"
    health_path: str = "/"


class RunField(BaseModel):
    name: str
    label: str
    type: Literal["text", "textarea", "number", "file"] = "text"
    required: bool = True
    default: str = ""


class RunAction(BaseModel):
    mode: Literal["disabled", "command"] = "disabled"
    command: str = ""
    fields: list[RunField] = Field(default_factory=list)


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
    license_note: str = "Проверить лицензию перед коммерческим использованием."
    requirements: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    pipeline: str = ""
    install: CommandAction = Field(default_factory=CommandAction)
    models: ModelAction = Field(default_factory=ModelAction)
    launch: LaunchAction = Field(default_factory=LaunchAction)
    run: RunAction = Field(default_factory=RunAction)
    workflows: list[WorkflowSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_manifest(self) -> ToolManifest:
        if not TOOL_ID.fullmatch(self.id):
            raise ValueError(f"Invalid tool id: {self.id}")
        if self.launch.mode == "process" and not self.launch.command:
            raise ValueError(f"{self.id}: process launch requires a command")
        if self.install.mode == "git-auto" and not self.repo_url:
            raise ValueError(f"{self.id}: git-auto requires repo_url")
        if self.run.mode == "command" and not self.run.command:
            raise ValueError(f"{self.id}: command run requires a command")
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

    def get(self, tool_id: str) -> ToolManifest:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {tool_id}") from exc

    def categories(self) -> list[str]:
        return sorted({tool.category for tool in self._tools.values()}, key=category_sort_key)
