#!/usr/bin/env bash
set -Eeuo pipefail

AI_LAB_ROOT="${AI_LAB_ROOT:-/workspace/ai-lab}"
TEMPLATE_ROOT="${AI_LAB_TEMPLATE_ROOT:-/opt/ai-lab-template}"

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

cleanup() {
  local code=$?
  kill "${COMFY_PID:-0}" "${LAUNCHER_PID:-0}" "${JUPYTER_PID:-0}" 2>/dev/null || true
  wait 2>/dev/null || true
  exit "$code"
}
trap cleanup EXIT INT TERM

cd /opt/ComfyUI
python3 main.py \
  --listen 0.0.0.0 \
  --port 8188 \
  --input-directory "$AI_LAB_ROOT/bridge/comfyui/input" \
  --output-directory "$AI_LAB_ROOT/bridge/comfyui/output" \
  --extra-model-paths-config "$EXTRA_MODELS" \
  > "$AI_LAB_ROOT/logs/comfyui.log" 2>&1 &
COMFY_PID=$!

cd "$TEMPLATE_ROOT/launcher"
/opt/ai-lab-launcher-venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 3000 \
  > "$AI_LAB_ROOT/logs/launcher.log" 2>&1 &
LAUNCHER_PID=$!

if [[ -z "${JUPYTER_TOKEN:-}" ]]; then
  JUPYTER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  echo "Generated one-time Jupyter token: $JUPYTER_TOKEN"
fi
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root \
  --ServerApp.token="$JUPYTER_TOKEN" --ServerApp.root_dir="$AI_LAB_ROOT" \
  > "$AI_LAB_ROOT/logs/jupyter.log" 2>&1 &
JUPYTER_PID=$!

wait -n "$COMFY_PID" "$LAUNCHER_PID" "$JUPYTER_PID"
