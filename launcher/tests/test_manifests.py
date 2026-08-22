import re
from pathlib import Path

from app.manifest import COMMIT_SHA, RUNNABLE, ManifestRegistry

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ManifestRegistry(ROOT / "manifests").load()


def test_all_manifests_are_valid_and_unique() -> None:
    tools = REGISTRY.all()

    assert len(tools) >= 35
    assert len({tool.id for tool in tools}) == len(tools)
    assert {"ideogram-4", "z-image", "ltx-2-5", "scope", "joyai-video-edit", "indextts-2-5", "midashenglm-gen"} <= {
        tool.id for tool in tools
    }


def test_categories_are_sorted_by_their_number() -> None:
    numbers = [int(category.split("·", 1)[0].strip()) for category in REGISTRY.categories()]

    assert numbers == sorted(numbers)


def test_asymflow_is_a_comfyui_adapter_with_a_baseline_workflow() -> None:
    asymflow = REGISTRY.get("asymflow")

    assert asymflow.kind == "comfyui"
    assert asymflow.install.mode == "disabled"
    assert {workflow.name for workflow in asymflow.workflows} == {
        "asymflow_flux2_klein_9b",
        "baseline_flux2_klein_9b_base",
    }
    workflow = next(
        item for item in asymflow.workflows if item.name == "asymflow_flux2_klein_9b"
    )
    assert len(workflow.model_hints) == 1
    assert workflow.model_hints[0].name == "asymflux2_klein_9b.safetensors"
    assert workflow.model_hints[0].directory == "loras"


def test_ready_comfyui_tools_have_downloadable_workflows() -> None:
    ready_comfy = [
        tool for tool in REGISTRY.all() if tool.kind == "comfyui" and tool.adapter_status == "ready"
    ]

    assert ready_comfy
    assert all(tool.workflows for tool in ready_comfy)
    assert all(workflow.url or workflow.local_file for tool in ready_comfy for workflow in tool.workflows)


def test_tools_with_official_comfyui_workflows_are_connected_to_the_launcher() -> None:
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

    assert all(REGISTRY.get(tool_id).workflows for tool_id in official_workflow_tools)
    assert all(
        REGISTRY.get(tool_id).launch.mode == "comfyui" for tool_id in official_workflow_tools
    )


def test_every_free_tool_has_a_concrete_pipeline() -> None:
    assert all(tool.pipeline.strip() for tool in REGISTRY.all() if tool.access == "free")


# --------------------------------------------------------------- honesty rules


def test_no_standalone_tool_claims_ready_without_a_verified_launch() -> None:
    liars = [
        tool.id
        for tool in REGISTRY.standalone()
        if tool.adapter_status == "ready" and tool.verified not in RUNNABLE
    ]
    assert not liars, f"ready without a verified launch: {liars}"


def test_automated_standalone_tools_pin_an_exact_commit() -> None:
    unpinned = [
        tool.id
        for tool in REGISTRY.standalone()
        if tool.install.mode == "git-auto" and not COMMIT_SHA.fullmatch(tool.ref)
    ]
    assert not unpinned, f"git-auto without a pinned sha: {unpinned}"


def test_every_automated_standalone_tool_records_its_real_entrypoint() -> None:
    missing = [
        tool.id
        for tool in REGISTRY.standalone()
        if tool.is_automatable and not tool.entrypoint.strip()
    ]
    assert not missing, f"no entrypoint recorded: {missing}"


def test_unavailable_tools_explain_themselves() -> None:
    for tool in REGISTRY.all():
        if tool.verified == "unavailable":
            assert tool.unavailable_reason.strip(), f"{tool.id} is unavailable with no reason"
            assert tool.launch.mode == "disabled"
            assert tool.run.mode == "disabled"


def test_no_tool_asks_for_a_public_port_of_its_own() -> None:
    # Tool UIs are published through the shared tool port; a per-tool public
    # port is what produced the permanent "Initializing" entries in RunPod.
    claimed = [tool.id for tool in REGISTRY.all() if tool.launch.port is not None]
    assert not claimed, f"tools requesting their own port: {claimed}"


def test_manual_tools_say_why_they_are_manual() -> None:
    for tool in REGISTRY.standalone():
        if tool.install.mode in {"disabled", "manual"}:
            assert tool.install.instructions.strip(), f"{tool.id} has no explanation"


def test_one_shot_tools_write_into_the_project_run_folder() -> None:
    for tool in REGISTRY.all():
        if tool.run.mode != "command":
            continue
        assert "{output_dir}" in tool.run.command, f"{tool.id} does not write into runs/"


def test_manifest_commands_only_reference_adapters_that_exist() -> None:
    # A manifest pointing at a deleted or renamed helper script fails at run
    # time, in a job log, minutes after the user pressed the button.
    referenced = set()
    pattern = re.compile(r"\{template_root\}/(adapters/[\w./-]+)")
    for tool in REGISTRY.all():
        for command in [tool.run.command, tool.launch.command, *tool.install.commands, *tool.models.commands]:
            referenced.update(pattern.findall(command))
    assert referenced, "expected at least one adapter reference"
    for relative in sorted(referenced):
        assert (ROOT / relative).is_file(), f"manifest references a missing file: {relative}"


def test_manifest_scripts_only_reference_scripts_that_exist() -> None:
    pattern = re.compile(r"\{template_root\}/(scripts/[\w./-]+)")
    for tool in REGISTRY.all():
        for command in [*tool.install.commands, *tool.models.commands]:
            for relative in pattern.findall(command):
                assert (ROOT / relative).is_file(), f"manifest references a missing script: {relative}"


def test_huggingface_downloads_declare_what_must_exist_afterwards() -> None:
    for tool in REGISTRY.all():
        if tool.models.mode in {"disabled", "manual"}:
            continue
        expected = tool.models.check or [item.path for item in tool.models.files] or tool.models.repo_ids
        assert expected, f"{tool.id} downloads models but never says what should appear"


def test_the_committed_status_report_matches_the_manifests() -> None:
    # The report is the thing a human reads to decide what to try next; it
    # must not describe a catalogue that no longer exists.
    import subprocess
    import sys

    rendered = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report-standalone.py")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    committed = (ROOT / "docs" / "STANDALONE-STATUS.md").read_text(encoding="utf-8")
    assert committed == rendered, (
        "docs/STANDALONE-STATUS.md is stale — run: python3 scripts/report-standalone.py --write"
    )


def test_pypi_installs_pin_the_cuda_the_image_ships() -> None:
    """A torch from plain PyPI can be built for a newer CUDA than the driver.

    That failure appears only at the first real inference, as "The NVIDIA
    driver on your system is too old", long after install reported success -
    which is exactly what happened on the first live run in a Pod.
    """
    for tool in REGISTRY.standalone():
        for command in tool.install.commands:
            if "uv pip install" not in command:
                continue
            # --no-build-isolation compiles an extension against the torch that
            # is already installed; switching index there would be wrong.
            if "--no-build-isolation" in command:
                continue
            assert "--torch-backend" in command, f"{tool.id}: {command}"


def test_smoke_tested_tools_record_where_and_when() -> None:
    for tool in REGISTRY.all():
        if tool.verified != "smoke-tested":
            continue
        assert tool.smoke is not None
        assert tool.smoke.gpu.strip(), f"{tool.id}: smoke evidence needs the GPU"
        assert tool.smoke.date.strip(), f"{tool.id}: smoke evidence needs a date"
