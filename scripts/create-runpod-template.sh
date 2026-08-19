#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE="${AI_LAB_IMAGE:?Set AI_LAB_IMAGE, for example ghcr.io/USER/ai-lab-runpod-template:stable}"
NAME="${AI_LAB_TEMPLATE_NAME:-AI Lab Disposable}"
CONTAINER_GB="${AI_LAB_CONTAINER_GB:-500}"
HF_SECRET_NAME="${AI_LAB_HF_SECRET_NAME:-huggingface_token}"

if ! command -v runpodctl >/dev/null 2>&1; then
  echo "runpodctl is not installed. Install it from https://docs.runpod.io/runpodctl/overview" >&2
  exit 1
fi

if [[ ! "$HF_SECRET_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "AI_LAB_HF_SECRET_NAME must be a valid RunPod secret name." >&2
  exit 1
fi

# A secret stored in RunPod is not exposed to the container until the template
# maps it to an environment variable. The default expects a secret named
# huggingface_token, matching the name already used in the AI Lab account.
TEMPLATE_ENV=$(printf \
  '{"AI_LAB_ROOT":"/workspace/ai-lab","HF_TOKEN":"{{ RUNPOD_SECRET_%s }}"}' \
  "$HF_SECRET_NAME")

runpodctl template create \
  --name "$NAME" \
  --image "$IMAGE" \
  --container-disk-in-gb "$CONTAINER_GB" \
  --volume-in-gb 0 \
  --ports "3000/http,8188/http,8888/http,7860/http,8001/http,8080/http" \
  --env "$TEMPLATE_ENV" \
  --readme "Disposable AI Lab: Launcher :3000, ComfyUI :8188, Jupyter :8888. No persistent volume. Export the project ZIP before stopping the Pod."
