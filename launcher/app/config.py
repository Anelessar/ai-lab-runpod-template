from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    template_root: Path
    runtime_root: Path
    manifest_dir: Path
    workflow_dir: Path
    launcher_port: int = 3000
    comfyui_url: str = "http://localhost:8188"
    jupyter_url: str = "http://localhost:8888"

    @classmethod
    def from_env(cls) -> Settings:
        default_template = Path(__file__).resolve().parents[2]
        template_root = Path(os.getenv("AI_LAB_TEMPLATE_ROOT", default_template)).resolve()
        runtime_root = Path(os.getenv("AI_LAB_ROOT", "/workspace/ai-lab")).resolve()
        return cls(
            template_root=template_root,
            runtime_root=runtime_root,
            manifest_dir=Path(
                os.getenv("AI_LAB_MANIFEST_DIR", template_root / "manifests")
            ).resolve(),
            workflow_dir=Path(
                os.getenv("AI_LAB_WORKFLOW_DIR", template_root / "workflows" / "comfyui")
            ).resolve(),
            launcher_port=int(os.getenv("AI_LAB_LAUNCHER_PORT", "3000")),
            comfyui_url=os.getenv("AI_LAB_COMFYUI_URL", "http://localhost:8188"),
            jupyter_url=os.getenv("AI_LAB_JUPYTER_URL", "http://localhost:8888"),
        )

    @property
    def tools_dir(self) -> Path:
        return self.runtime_root / "tools"

    @property
    def environments_dir(self) -> Path:
        return self.runtime_root / "environments"

    @property
    def models_dir(self) -> Path:
        return self.runtime_root / "models"

    @property
    def projects_dir(self) -> Path:
        return self.runtime_root / "projects"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_root / "logs"

    @property
    def state_dir(self) -> Path:
        return self.runtime_root / "state"

    @property
    def bridge_dir(self) -> Path:
        return self.runtime_root / "bridge" / "comfyui"

    def ensure_runtime(self) -> None:
        for path in (
            self.runtime_root,
            self.tools_dir,
            self.environments_dir,
            self.models_dir,
            self.projects_dir,
            self.logs_dir,
            self.state_dir,
            self.bridge_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def public_port_url(self, port: int) -> str:
        pod_id = os.getenv("RUNPOD_POD_ID", "").strip()
        if pod_id:
            return f"https://{pod_id}-{port}.proxy.runpod.net"
        return f"http://localhost:{port}"
