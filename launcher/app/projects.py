from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

SLUG = re.compile(r"[^a-z0-9-]+")
MEDIA_EXTENSIONS = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
    ".wav": "audio",
    ".mp3": "audio",
    ".flac": "audio",
    ".json": "data",
    ".txt": "data",
    ".srt": "data",
}
EXPORT_DIRS = ("inputs", "assets", "runs", "workflows", "final")


def slugify(value: str) -> str:
    slug = SLUG.sub("-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Название проекта должно содержать буквы или цифры")
    return slug[:64]


def safe_filename(value: str) -> str:
    name = Path(value).name.replace("\x00", "")
    cleaned = re.sub(r"[^A-Za-z0-9А-Яа-я._-]+", "-", name).strip("-.")
    if not cleaned:
        raise ValueError("Некорректное имя файла")
    return cleaned[:180]


class ProjectManager:
    def __init__(self, projects_dir: Path, bridge_dir: Path, state_dir: Path):
        self.projects_dir = projects_dir
        self.bridge_dir = bridge_dir
        self.state_dir = state_dir
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.list_projects():
            self.create("default")
        if not self.active_file.exists():
            self.select(self.list_projects()[0])

    @property
    def active_file(self) -> Path:
        return self.state_dir / "active-project.txt"

    def list_projects(self) -> list[str]:
        return sorted(
            path.name
            for path in self.projects_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )

    def create(self, name: str) -> str:
        project_id = slugify(name)
        root = self.projects_dir / project_id
        for part in (*EXPORT_DIRS, "runs/comfyui"):
            (root / part).mkdir(parents=True, exist_ok=True)
        metadata = root / "project.json"
        if not metadata.exists():
            metadata.write_text(
                json.dumps(
                    {
                        "id": project_id,
                        "name": name.strip() or project_id,
                        "created_at": datetime.now(UTC).isoformat(),
                        "events": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return project_id

    def active(self) -> str:
        project_id = self.active_file.read_text(encoding="utf-8").strip()
        self.path(project_id)
        return project_id

    def select(self, project_id: str) -> None:
        root = self.path(project_id)
        self.active_file.write_text(project_id, encoding="utf-8")
        links = {
            "input": root / "inputs" / "comfyui",
            "output": root / "runs" / "comfyui",
        }
        for name, target in links.items():
            target.mkdir(parents=True, exist_ok=True)
            link = self.bridge_dir / name
            temporary = self.bridge_dir / f".{name}.next"
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(target, target_is_directory=True)
            os.replace(temporary, link)

    def path(self, project_id: str) -> Path:
        safe_id = slugify(project_id)
        path = (self.projects_dir / safe_id).resolve()
        if path.parent != self.projects_dir.resolve() or not path.is_dir():
            raise KeyError(project_id)
        return path

    def upload(self, project_id: str, filename: str, stream: BinaryIO) -> Path:
        destination = self.path(project_id) / "inputs" / safe_filename(filename)
        with destination.open("wb") as output:
            shutil.copyfileobj(stream, output)
        self._event(project_id, "upload", destination.relative_to(self.path(project_id)), None)
        return destination

    def assets(self, project_id: str) -> list[dict[str, str]]:
        root = self.path(project_id)
        assets: list[dict[str, str]] = []
        for folder in EXPORT_DIRS:
            for path in sorted((root / folder).rglob("*")):
                if not path.is_file() or path.name == "project.json":
                    continue
                media_type = MEDIA_EXTENSIONS.get(path.suffix.lower())
                if not media_type:
                    continue
                relative = path.relative_to(root)
                assets.append(
                    {
                        "name": path.name,
                        "relative_path": relative.as_posix(),
                        "kind": media_type,
                        "size": human_size(path.stat().st_size),
                    }
                )
        return assets

    def resolve_asset(self, project_id: str, relative_path: str) -> Path:
        root = self.path(project_id)
        path = (root / relative_path).resolve()
        if root not in path.parents or not path.is_file():
            raise KeyError(relative_path)
        return path

    def send_to(self, project_id: str, relative_path: str, target_tool: str) -> Path:
        source = self.resolve_asset(project_id, relative_path)
        destination_dir = self.path(project_id) / "inputs" / target_tool
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / safe_filename(source.name)
        if destination.exists():
            destination = destination.with_name(
                f"{destination.stem}-{datetime.now(UTC).strftime('%H%M%S')}{destination.suffix}"
            )
        shutil.copy2(source, destination)
        self._event(
            project_id,
            "send",
            destination.relative_to(self.path(project_id)),
            {"source": relative_path, "target_tool": target_tool},
        )
        return destination

    def export(self, project_id: str) -> Path:
        root = self.path(project_id)
        export_dir = self.projects_dir / ".exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f"{project_id}-", suffix=".zip", dir=export_dir)
        os.close(fd)
        archive = Path(temp_name)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            metadata = root / "project.json"
            if metadata.exists():
                zipped.write(metadata, "project.json")
            for folder in EXPORT_DIRS:
                for path in (root / folder).rglob("*"):
                    if path.is_file():
                        zipped.write(path, path.relative_to(root))
        return archive

    def _event(
        self,
        project_id: str,
        event_type: str,
        path: Path,
        details: dict[str, str] | None,
    ) -> None:
        metadata_path = self.path(project_id) / "project.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.setdefault("events", []).append(
            {
                "type": event_type,
                "path": path.as_posix(),
                "details": details or {},
                "at": datetime.now(UTC).isoformat(),
            }
        )
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
