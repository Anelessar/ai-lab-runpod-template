from __future__ import annotations

import mimetypes
from collections import defaultdict
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .jobs import JobManager
from .manifest import ManifestRegistry
from .processes import ProcessManager
from .projects import ProjectManager
from .tools import ToolManager


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_runtime()
    registry = ManifestRegistry(settings.manifest_dir).load()
    jobs = JobManager(settings.logs_dir / "jobs")
    processes = ProcessManager(settings.logs_dir / "processes")
    projects = ProjectManager(settings.projects_dir, settings.bridge_dir, settings.state_dir)
    tools = ToolManager(settings, registry, jobs, processes, projects)

    app = FastAPI(title="AI Lab Launcher", version="0.1.0")
    template_dir = Path(__file__).resolve().parent / "templates"
    static_dir = Path(__file__).resolve().parent / "static"
    templates = Jinja2Templates(directory=template_dir)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.state.settings = settings
    app.state.registry = registry
    app.state.jobs = jobs
    app.state.processes = processes
    app.state.projects = projects
    app.state.tools = tools

    @app.get("/")
    def dashboard(request: Request, message: str = "", error: str = ""):
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for tool in registry.all():
            grouped[tool.category].append({"manifest": tool, "status": tools.status(tool)})
        project_id = projects.active()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "categories": grouped,
                "projects": projects.list_projects(),
                "active_project": project_id,
                "assets": projects.assets(project_id),
                "all_tools": registry.all(),
                "jobs": jobs.all()[:20],
                "running": processes.current(),
                "message": message,
                "error": error,
                "comfyui_url": settings.public_port_url(8188),
                "jupyter_url": settings.public_port_url(8888),
            },
        )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "tools": len(registry.all()),
            "active_project": projects.active(),
            "running": processes.current(),
        }

    @app.get("/api/status")
    def api_status():
        return {
            "jobs": jobs.all(),
            "running": processes.current(),
            "tools": {tool.id: tools.status(tool) for tool in registry.all()},
        }

    @app.post("/projects")
    def create_project(name: str = Form(...)):
        try:
            project_id = projects.create(name)
            projects.select(project_id)
            return go_home(message=f"Проект {project_id} создан")
        except Exception as exc:  # noqa: BLE001
            return go_home(error=str(exc))

    @app.post("/projects/select")
    def select_project(project_id: str = Form(...)):
        try:
            projects.select(project_id)
            return go_home(message=f"Активный проект: {project_id}")
        except Exception as exc:  # noqa: BLE001
            return go_home(error=str(exc))

    @app.post("/projects/{project_id}/upload")
    def upload(project_id: str, file: Annotated[UploadFile, File()]):
        try:
            projects.upload(project_id, file.filename or "upload.bin", file.file)
            return go_home(message=f"Файл {file.filename} добавлен")
        except Exception as exc:  # noqa: BLE001
            return go_home(error=str(exc))

    @app.post("/projects/{project_id}/send")
    def send_asset(
        project_id: str,
        relative_path: str = Form(...),
        target_tool: str = Form(...),
    ):
        try:
            registry.get(target_tool)
            destination = projects.send_to(project_id, relative_path, target_tool)
            return go_home(message=f"Передано в {target_tool}: {destination.name}")
        except Exception as exc:  # noqa: BLE001
            return go_home(error=str(exc))

    @app.get("/projects/{project_id}/export")
    def export_project(project_id: str):
        try:
            archive = projects.export(project_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(archive, filename=f"{project_id}.zip", media_type="application/zip")

    @app.get("/workflows/{tool_id}/{workflow_name}")
    def workflow_file(tool_id: str, workflow_name: str):
        try:
            tool = registry.get(tool_id)
            workflow = next(item for item in tool.workflows if item.name == workflow_name)
            path = tools.paths(tool_id)["workflow"] / f"{workflow.name}.json"
            if not path.is_file():
                raise FileNotFoundError("Сначала нажмите «Скачать workflow»")
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, filename=f"{tool_id}-{workflow.name}.json")

    @app.get("/files/{project_id}/{relative_path:path}")
    def project_file(project_id: str, relative_path: str):
        try:
            path = projects.resolve_asset(project_id, relative_path)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])

    @app.post("/tools/{tool_id}/run")
    async def run_tool(tool_id: str, request: Request):
        try:
            form = await request.form()
            values = {key: str(value) for key, value in form.items()}
            job = tools.run(tool_id, values)
            return go_home(message=f"Обработка запущена: {job.id}")
        except Exception as exc:  # noqa: BLE001
            return go_home(error=str(exc))

    @app.post("/tools/{tool_id}/{action}")
    def tool_action(tool_id: str, action: str):
        try:
            if action == "install":
                job = tools.install(tool_id)
                return go_home(message=f"Установка запущена: {job.id}")
            if action == "models":
                job = tools.download_models(tool_id)
                return go_home(message=f"Загрузка моделей запущена: {job.id}")
            if action == "workflows":
                job = tools.download_workflows(tool_id)
                return go_home(message=f"Загрузка workflow запущена: {job.id}")
            if action == "launch":
                result = tools.launch(tool_id)
                url = result.get("url", "")
                return go_home(message=f"{tool_id} запущен. Откройте {url}")
            if action == "stop":
                tools.stop(tool_id)
                return go_home(message=f"{tool_id} остановлен")
            if action == "delete-program":
                tools.delete_program(tool_id)
                return go_home(message=f"Программа {tool_id} удалена")
            if action == "delete-models":
                tools.delete_models(tool_id)
                return go_home(message=f"Модели {tool_id} удалены")
            raise ValueError(f"Неизвестное действие: {action}")
        except Exception as exc:  # noqa: BLE001
            return go_home(error=str(exc))

    @app.get("/tools/{tool_id}/open")
    def open_tool(tool_id: str):
        try:
            tool = registry.get(tool_id)
            if tool.launch.mode == "comfyui":
                url = settings.public_port_url(8188)
            elif tool.launch.mode == "process" and tool.launch.port:
                url = settings.public_port_url(tool.launch.port) + tool.launch.path
            else:
                url = str(tool.source_url)
            return RedirectResponse(url, status_code=307)
        except Exception as exc:  # noqa: BLE001
            return go_home(error=str(exc))

    @app.get("/api/jobs/{job_id}/log")
    def job_log_api(job_id: str):
        try:
            return JSONResponse({"job_id": job_id, "log": jobs.read_log(job_id)})
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @app.get("/jobs/{job_id}")
    def job_log_page(request: Request, job_id: str):
        try:
            log = jobs.read_log(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return templates.TemplateResponse(
            request,
            "log.html",
            {"title": f"Лог задачи {job_id}", "log": log},
        )

    return app


def go_home(*, message: str = "", error: str = "") -> RedirectResponse:
    query = f"?message={quote(message)}" if message else f"?error={quote(error)}"
    return RedirectResponse(f"/{query}", status_code=303)


app = create_app()
