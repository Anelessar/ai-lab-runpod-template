#!/usr/bin/env bash
# Smoke-test standalone tools inside a running Pod, without weights and without
# a GPU run: install the pinned commit, then execute the manifest's
# `run.smoke_command` (typically `--help`) to prove the environment imports and
# the documented entrypoint really accepts the arguments AI Lab passes it.
#
#   scripts/smoke-standalone.sh                 # every tool that declares one
#   scripts/smoke-standalone.sh lavasr-v2 scope # only these
#
# Exit code is non-zero if any tool fails, so this is usable from CI on a
# GPU runner as well as by hand over SSH.
set -Eeuo pipefail

TEMPLATE_ROOT="${AI_LAB_TEMPLATE_ROOT:-/opt/ai-lab-template}"
LAUNCHER_VENV="${AI_LAB_LAUNCHER_VENV:-/opt/ai-lab-launcher-venv}"
REPORT="${AI_LAB_SMOKE_REPORT:-${AI_LAB_ROOT:-/workspace/ai-lab}/logs/smoke-standalone.log}"

mkdir -p "$(dirname "$REPORT")"

exec "$LAUNCHER_VENV/bin/python" - "$@" <<'PY'
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(os.environ.get("AI_LAB_TEMPLATE_ROOT", "/opt/ai-lab-template")) / "launcher"))

from app.config import Settings
from app.jobs import COMPLETED, FAILED, JobManager
from app.manifest import ManifestRegistry
from app.processes import ProcessManager
from app.projects import ProjectManager
from app.tools import ToolManager

settings = Settings.from_env()
settings.ensure_runtime()
registry = ManifestRegistry(settings.manifest_dir).load()
jobs = JobManager(settings.logs_dir / "jobs", settings.state_dir / "jobs")
processes = ProcessManager(settings.logs_dir / "processes", settings.state_dir, settings.tool_port)
projects = ProjectManager(settings.projects_dir, settings.bridge_dir, settings.state_dir)
tools = ToolManager(settings, registry, jobs, processes, projects)

wanted = set(sys.argv[1:])
targets = [
    tool
    for tool in registry.standalone()
    if tool.run.smoke_command and (not wanted or tool.id in wanted)
]
missing = wanted - {tool.id for tool in targets}
if missing:
    print(f"no smoke_command in the manifest for: {', '.join(sorted(missing))}", file=sys.stderr)

failures: list[str] = []
for tool in targets:
    print(f"\n=== {tool.id} ({tool.ref[:12]})", flush=True)
    if not tools.is_installed(tool):
        job = tools.install(tool.id)
        while jobs.latest_for(tool.id)["status"] not in {COMPLETED, FAILED}:
            time.sleep(5)
        record = jobs.latest_for(tool.id)
        if record["status"] != COMPLETED:
            print(f"install failed: {record['error']}", flush=True)
            failures.append(f"{tool.id}: install — {record['error']}")
            continue
        print(f"installed, log: {job.log_path}", flush=True)

    context = tools.context(tool)
    command = context.render(tool.run.smoke_command)
    print(f"$ {command}", flush=True)
    completed = subprocess.run(
        ["bash", "-c", command],
        cwd=context.tool_dir,
        env=tools.environment(context),
        text=True,
        capture_output=True,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    print(output[-2000:], flush=True)
    if completed.returncode:
        failures.append(f"{tool.id}: smoke exit {completed.returncode}")

print("\n================ smoke summary ================")
print(f"tools attempted: {len(targets)}")
for failure in failures:
    print(f"FAIL {failure}")
if not failures:
    print("all attempted tools passed their smoke command")
sys.exit(1 if failures else 0)
PY
