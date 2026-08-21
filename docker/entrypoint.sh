#!/usr/bin/env bash
set -Eeuo pipefail

AI_LAB_ROOT="${AI_LAB_ROOT:-/workspace/ai-lab}"
TEMPLATE_ROOT="${AI_LAB_TEMPLATE_ROOT:-/opt/ai-lab-template}"
LAUNCHER_VENV="${AI_LAB_LAUNCHER_VENV:-/opt/ai-lab-launcher-venv}"

# Every port here is a port RunPod is asked to publish, and every one of them
# has a process that starts unconditionally at boot. A declared port with no
# listener is what RunPod renders as "Initializing" forever.
LAUNCHER_PORT=3000
COMFYUI_PORT=8188
JUPYTER_PORT=8888
TOOL_PORT=7860

CUDA_WAIT_SECONDS="${AI_LAB_CUDA_WAIT_SECONDS:-600}"

log() {
  printf '[ai-lab] %s\n' "$*"
}

cleanup() {
  local code=$?
  local pid
  trap - EXIT INT TERM
  for pid in "${COMFY_PID:-}" "${LAUNCHER_PID:-}" "${JUPYTER_PID:-}" "${TOOLPORT_PID:-}"; do
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  exit "$code"
}
trap cleanup EXIT INT TERM

wait_for_service() {
  local name="$1"
  local url="$2"
  local pid="$3"
  local required="${4:-false}"
  local attempt

  for attempt in {1..60}; do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      log "$name is ready at $url"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      log "$name exited before becoming ready"
      if [[ "$required" == "true" ]]; then
        return 1
      fi
      return 0
    fi
    sleep 2
  done

  log "$name did not become ready within 120 seconds"
  [[ "$required" != "true" ]]
}

wait_for_cuda() {
  local attempt=0
  local deadline=$(( SECONDS + CUDA_WAIT_SECONDS ))

  # nvidia-smi may already see the card while the CUDA runtime still cannot
  # create a context. ComfyUI exits permanently in that short window, leaving
  # RunPod's port 8188 stuck on "Initializing". Test the same call ComfyUI uses
  # and only start it after the allocated GPU is actually usable.
  #
  # The wait is bounded: an unbounded loop turned "this Pod has no usable GPU"
  # into a port that never opens and never explained itself.
  until /usr/local/bin/python -c \
    'import torch; assert torch.cuda.is_available(); torch.cuda.mem_get_info()' \
    >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      return 1
    fi
    attempt=$((attempt + 1))
    if (( attempt == 1 || attempt % 10 == 0 )); then
      log "Waiting for the CUDA device before starting ComfyUI (attempt $attempt)"
    fi
    sleep 3
  done

  log "CUDA device is ready for ComfyUI"
  return 0
}

log "Preparing runtime at $AI_LAB_ROOT"

mkdir -p \
  "$AI_LAB_ROOT/bridge/comfyui" \
  "$AI_LAB_ROOT/cache/huggingface" \
  "$AI_LAB_ROOT/cache/modelscope" \
  "$AI_LAB_ROOT/cache/uv" \
  "$AI_LAB_ROOT/models/comfyui" \
  "$AI_LAB_ROOT/projects" \
  "$AI_LAB_ROOT/logs" \
  "$AI_LAB_ROOT/state" \
  "$AI_LAB_ROOT/state/jobs" \
  "$AI_LAB_ROOT/tools" \
  "$AI_LAB_ROOT/environments"

export HF_HOME="${HF_HOME:-$AI_LAB_ROOT/cache/huggingface}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$AI_LAB_ROOT/cache/modelscope}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$AI_LAB_ROOT/cache/uv}"
export AI_LAB_MANIFEST_DIR="${AI_LAB_MANIFEST_DIR:-$TEMPLATE_ROOT/manifests}"
export AI_LAB_WORKFLOW_DIR="${AI_LAB_WORKFLOW_DIR:-$TEMPLATE_ROOT/workflows/comfyui}"
export AI_LAB_TOOL_PORT="$TOOL_PORT"

EXTRA_MODELS="$AI_LAB_ROOT/state/extra_model_paths.yaml"
if [[ ! -f "$EXTRA_MODELS" ]]; then
  sed "s|__MODEL_ROOT__|$AI_LAB_ROOT/models/comfyui|g" \
    "$TEMPLATE_ROOT/docker/extra_model_paths.yaml.template" > "$EXTRA_MODELS"
fi

# A tool that was running when the container stopped cannot still be running
# now, so start from a clean route table instead of proxying to a dead port.
rm -f "$AI_LAB_ROOT/state/process.json" "$AI_LAB_ROOT/state/tool-route.json" \
      "$AI_LAB_ROOT/state/comfyui-failed"

# Launcher creates the default project and atomically points these bridge links at it.
"$LAUNCHER_VENV/bin/python" -c "from app.config import Settings; from app.projects import ProjectManager; s=Settings.from_env(); s.ensure_runtime(); ProjectManager(s.projects_dir,s.bridge_dir,s.state_dir)"

log "Starting AI Lab Launcher on port $LAUNCHER_PORT"
cd "$TEMPLATE_ROOT/launcher"
"$LAUNCHER_VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$LAUNCHER_PORT" \
  > >(tee -a "$AI_LAB_ROOT/logs/launcher.log") 2>&1 &
LAUNCHER_PID=$!

# The tool port is owned by a proxy for the whole life of the Pod, so RunPod
# always gets an answer on 7860 - a placeholder page when nothing runs, and the
# running tool's own UI otherwise. Standalone tools bind private loopback ports
# and are reached through here, which is why no tool needs its own public port.
log "Starting the standalone tool port on $TOOL_PORT"
"$LAUNCHER_VENV/bin/uvicorn" app.toolport:app --host 0.0.0.0 --port "$TOOL_PORT" \
  --ws websockets \
  > >(tee -a "$AI_LAB_ROOT/logs/tool-port.log") 2>&1 &
TOOLPORT_PID=$!

# Keep the CUDA readiness check inside ComfyUI's background process so Launcher,
# Jupyter and the tool port become available immediately even when the host is
# slow to hand the GPU to the container.
(
  comfy_args=()
  if ! wait_for_cuda; then
    log "No usable CUDA device after ${CUDA_WAIT_SECONDS}s - starting ComfyUI on CPU so the port still opens"
    printf 'GPU не появилась за %s с, ComfyUI запущен в режиме --cpu.\n' "$CUDA_WAIT_SECONDS" \
      > "$AI_LAB_ROOT/state/comfyui-failed"
    comfy_args+=(--cpu)
  fi
  log "Starting ComfyUI on public port $COMFYUI_PORT"
  cd /opt/ComfyUI
  exec /usr/local/bin/python main.py \
    --listen 0.0.0.0 \
    --port "$COMFYUI_PORT" \
    --input-directory "$AI_LAB_ROOT/bridge/comfyui/input" \
    --output-directory "$AI_LAB_ROOT/bridge/comfyui/output" \
    --extra-model-paths-config "$EXTRA_MODELS" \
    "${comfy_args[@]}"
) > >(tee -a "$AI_LAB_ROOT/logs/comfyui.log") 2>&1 &
COMFY_PID=$!

# RunPod's own convention is JUPYTER_PASSWORD; keep accepting JUPYTER_TOKEN too.
JUPYTER_TOKEN="${JUPYTER_TOKEN:-${JUPYTER_PASSWORD:-}}"
if [[ -z "$JUPYTER_TOKEN" ]]; then
  JUPYTER_TOKEN="$(/usr/local/bin/python -c 'import secrets; print(secrets.token_urlsafe(18))')"
fi
# The Launcher reads this file to build a link that already carries the token.
# Printing the token only into the container log was why the RunPod "Connect"
# button landed on a login screen nobody had the password for.
umask 077
printf '%s' "$JUPYTER_TOKEN" > "$AI_LAB_ROOT/state/jupyter-token.txt"
umask 022

log "Starting JupyterLab on port $JUPYTER_PORT"
# allow_origin/allow_remote_access/trust_xheaders are required behind the
# RunPod HTTPS proxy: without them JupyterLab's websockets are rejected and the
# tab hangs on a blank page even though the port itself answers.
jupyter lab \
  --ip=0.0.0.0 --port="$JUPYTER_PORT" --no-browser --allow-root \
  --ServerApp.token="$JUPYTER_TOKEN" \
  --ServerApp.root_dir="$AI_LAB_ROOT" \
  --ServerApp.allow_origin='*' \
  --ServerApp.allow_remote_access=True \
  --ServerApp.trust_xheaders=True \
  --ServerApp.terminado_settings='{"shell_command":["/bin/bash"]}' \
  --FileContentsManager.delete_to_trash=False \
  > >(tee -a "$AI_LAB_ROOT/logs/jupyter.log") 2>&1 &
JUPYTER_PID=$!

wait_for_service "AI Lab Launcher" "http://127.0.0.1:$LAUNCHER_PORT/health" "$LAUNCHER_PID" true
wait_for_service "Tool port" "http://127.0.0.1:$TOOL_PORT/__ai_lab_health" "$TOOLPORT_PID" true
# /api answers without authentication and proves the server, not just the socket.
wait_for_service "JupyterLab" "http://127.0.0.1:$JUPYTER_PORT/api" "$JUPYTER_PID"
wait_for_service "ComfyUI" "http://127.0.0.1:$COMFYUI_PORT/" "$COMFY_PID"

log "Open the Launcher for live status of every port: http://127.0.0.1:$LAUNCHER_PORT/"
log "Startup checks finished; keeping the Pod alive while Launcher is running"
wait "$LAUNCHER_PID"
