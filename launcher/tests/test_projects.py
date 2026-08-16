import io
import zipfile
from pathlib import Path

from app.projects import ProjectManager


def make_manager(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path / "projects", tmp_path / "bridge", tmp_path / "state")


def test_project_switch_updates_comfyui_bridge(tmp_path: Path) -> None:
    projects = make_manager(tmp_path)
    project_id = projects.create("Product Ad 01")
    projects.select(project_id)

    assert projects.active() == "product-ad-01"
    assert (tmp_path / "bridge" / "input").resolve() == (
        tmp_path / "projects" / project_id / "inputs" / "comfyui"
    ).resolve()
    assert (tmp_path / "bridge" / "output").resolve() == (
        tmp_path / "projects" / project_id / "runs" / "comfyui"
    ).resolve()


def test_upload_send_and_export_only_project_data(tmp_path: Path) -> None:
    projects = make_manager(tmp_path)
    project_id = projects.create("Catalog")
    projects.select(project_id)
    uploaded = projects.upload(project_id, "reference image.png", io.BytesIO(b"fake-png"))
    sent = projects.send_to(project_id, uploaded.relative_to(projects.path(project_id)).as_posix(), "scope")
    (projects.path(project_id) / "models" / "huge.ckpt").parent.mkdir(parents=True)
    (projects.path(project_id) / "models" / "huge.ckpt").write_bytes(b"must-not-export")

    archive = projects.export(project_id)
    with zipfile.ZipFile(archive) as zipped:
        names = set(zipped.namelist())

    assert "inputs/reference-image.png" in names
    assert sent.relative_to(projects.path(project_id)).as_posix() in names
    assert all(not name.startswith("models/") for name in names)
    assert "project.json" in names


def test_asset_resolution_blocks_path_escape(tmp_path: Path) -> None:
    projects = make_manager(tmp_path)
    project_id = projects.create("Safe")

    try:
        projects.resolve_asset(project_id, "../../outside.txt")
    except KeyError:
        pass
    else:
        raise AssertionError("path escape must be rejected")
