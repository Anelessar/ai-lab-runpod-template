from pathlib import Path

from app.manifest import ManifestRegistry

ROOT = Path(__file__).resolve().parents[2]


def test_all_manifests_are_valid_and_unique() -> None:
    registry = ManifestRegistry(ROOT / "manifests").load()
    tools = registry.all()

    assert len(tools) >= 35
    assert len({tool.id for tool in tools}) == len(tools)
    assert {"ideogram-4", "z-image", "ltx-2-5", "scope", "joyai-video-edit", "indextts-2-5", "midashenglm-gen"} <= {
        tool.id for tool in tools
    }


def test_categories_are_sorted_by_their_number() -> None:
    registry = ManifestRegistry(ROOT / "manifests").load()
    numbers = [int(category.split("·", 1)[0].strip()) for category in registry.categories()]

    assert numbers == sorted(numbers)


def test_asymflow_is_a_comfyui_adapter_with_a_baseline_workflow() -> None:
    registry = ManifestRegistry(ROOT / "manifests").load()
    asymflow = registry.get("asymflow")

    assert asymflow.kind == "comfyui"
    assert asymflow.install.mode == "disabled"
    assert {workflow.name for workflow in asymflow.workflows} == {
        "asymflow_flux2_klein_9b",
        "baseline_flux2_klein_9b_base",
    }


def test_ready_comfyui_tools_have_downloadable_workflows() -> None:
    registry = ManifestRegistry(ROOT / "manifests").load()
    ready_comfy = [
        tool for tool in registry.all() if tool.kind == "comfyui" and tool.adapter_status == "ready"
    ]

    assert ready_comfy
    assert all(tool.workflows for tool in ready_comfy)
    assert all(workflow.url or workflow.local_file for tool in ready_comfy for workflow in tool.workflows)


def test_tools_with_official_comfyui_workflows_are_connected_to_the_launcher() -> None:
    registry = ManifestRegistry(ROOT / "manifests").load()
    official_workflow_tools = {
        "asymflow",
        "ernie-image",
        "flux2-klein",
        "freefuse",
        "genfocus",
        "ideogram-4",
        "ltx-2-5",
        "lucida-v7",
        "mage-flow",
        "minimax-h3",
        "mrflow",
        "qwen-image-2512",
        "qwen-image-edit-2511",
        "qwen-image-layered",
        "turbodiffusion",
        "wan-animate-2",
        "z-image",
    }

    assert all(registry.get(tool_id).workflows for tool_id in official_workflow_tools)
    assert all(
        registry.get(tool_id).launch.mode == "comfyui" for tool_id in official_workflow_tools
    )


def test_every_free_tool_has_a_concrete_pipeline() -> None:
    registry = ManifestRegistry(ROOT / "manifests").load()
    assert all(tool.pipeline.strip() for tool in registry.all() if tool.access == "free")
