#!/usr/bin/env bash
set -Eeuo pipefail

AI_LAB_ROOT="${AI_LAB_ROOT:-/workspace/ai-lab}"
TEMPLATE_ROOT="${AI_LAB_TEMPLATE_ROOT:-/opt/ai-lab-template}"

log() {
  printf '[ai-lab] %s\n' "$*"
}

cleanup() {
  local code=$?
  local pid
  trap - EXIT INT TERM
  for pid in "${COMFY_PID:-}" "${LAUNCHER_PID:-}" "${JUPYTER_PID:-}"; do
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

  # nvidia-smi may already see the card while the CUDA runtime still cannot
  # create a context. ComfyUI exits permanently in that short window, leaving
  # RunPod's port 8188 stuck on "Initializing". Test the same call ComfyUI uses
  # and only start it after the allocated GPU is actually usable.
  until /usr/local/bin/python -c \
    'import torch; assert torch.cuda.is_available(); torch.cuda.mem_get_info()' \
    >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if (( attempt == 1 || attempt % 10 == 0 )); then
      log "Waiting for the CUDA device before starting ComfyUI (attempt $attempt)"
    fi
    sleep 3
  done

  log "CUDA device is ready for ComfyUI"
}

log "Preparing runtime at $AI_LAB_ROOT"

mkdir -p \
  "$AI_LAB_ROOT/bridge/comfyui" \
  "$AI_LAB_ROOT/cache/huggingface" \
  "$AI_LAB_ROOT/cache/modelscope" \
  "$AI_LAB_ROOT/models/comfyui" \
  "$AI_LAB_ROOT/projects" \
  "$AI_LAB_ROOT/logs" \
  "$AI_LAB_ROOT/state" \
  "$AI_LAB_ROOT/tools" \
  "$AI_LAB_ROOT/environments"

export HF_HOME="${HF_HOME:-$AI_LAB_ROOT/cache/huggingface}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$AI_LAB_ROOT/cache/modelscope}"
export AI_LAB_MANIFEST_DIR="${AI_LAB_MANIFEST_DIR:-$TEMPLATE_ROOT/manifests}"
export AI_LAB_WORKFLOW_DIR="${AI_LAB_WORKFLOW_DIR:-$TEMPLATE_ROOT/workflows/comfyui}"

EXTRA_MODELS="$AI_LAB_ROOT/state/extra_model_paths.yaml"
if [[ ! -f "$EXTRA_MODELS" ]]; then
  sed "s|__MODEL_ROOT__|$AI_LAB_ROOT/models/comfyui|g" \
    "$TEMPLATE_ROOT/docker/extra_model_paths.yaml.template" > "$EXTRA_MODELS"
fi

# Launcher creates the default project and atomically points these bridge links at it.
/opt/ai-lab-launcher-venv/bin/python -c "from app.config import Settings; from app.projects import ProjectManager; s=Settings.from_env(); s.ensure_runtime(); ProjectManager(s.projects_dir,s.bridge_dir,s.state_dir)"

log "Starting AI Lab Launcher on port 3000"
cd "$TEMPLATE_ROOT/launcher"
/opt/ai-lab-launcher-venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 3000 \
  > >(tee -a "$AI_LAB_ROOT/logs/launcher.log") 2>&1 &
LAUNCHER_PID=$!

# Keep the CUDA readiness check in ComfyUI's background process so Launcher
# and Jupyter can become available immediately even when the host is slow to
# hand the GPU to the container.
(
  wait_for_cuda
  log "Starting ComfyUI on public port 8188"
  cd /opt/ComfyUI
  exec /usr/local/bin/python main.py \
    --listen 0.0.0.0 \
    --port 8188 \
    --input-directory "$AI_LAB_ROOT/bridge/comfyui/input" \
    --output-directory "$AI_LAB_ROOT/bridge/comfyui/output" \
    --extra-model-paths-config "$EXTRA_MODELS"
) > >(tee -a "$AI_LAB_ROOT/logs/comfyui.log") 2>&1 &
COMFY_PID=$!

if [[ -z "${JUPYTER_TOKEN:-}" ]]; then
  JUPYTER_TOKEN="$(/usr/local/bin/python -c 'import secrets; print(secrets.token_urlsafe(18))')"
  log "Generated one-time Jupyter token: $JUPYTER_TOKEN"
fi
log "Starting JupyterLab on port 8888"
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root \
  --ServerApp.token="$JUPYTER_TOKEN" --ServerApp.root_dir="$AI_LAB_ROOT" \
  > >(tee -a "$AI_LAB_ROOT/logs/jupyter.log") 2>&1 &
JUPYTER_PID=$!

wait_for_service "AI Lab Launcher" "http://127.0.0.1:3000/health" "$LAUNCHER_PID" true
wait_for_service "ComfyUI" "http://127.0.0.1:8188/" "$COMFY_PID"
wait_for_service "JupyterLab" "http://127.0.0.1:8888/" "$JUPYTER_PID"

log "Startup checks finished; keeping the Pod alive while Launcher is running"
wait "$LAUNCHER_PID"
