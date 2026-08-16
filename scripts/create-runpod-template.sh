#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE="${AI_LAB_IMAGE:?Set AI_LAB_IMAGE, for example ghcr.io/USER/ai-lab-runpod-template:stable}"
NAME="${AI_LAB_TEMPLATE_NAME:-AI Lab Disposable}"
CONTAINER_GB="${AI_LAB_CONTAINER_GB:-500}"

if ! command -v runpodctl >/dev/null 2>&1; then
  echo "runpodctl is not installed. Install it from https://docs.runpod.io/runpodctl/overview" >&2
  exit 1
fi

runpodctl template create \
  --name "$NAME" \
  --image "$IMAGE" \
  --container-disk-in-gb "$CONTAINER_GB" \
  --volume-in-gb 0 \
  --ports "3000/http,8188/http,8888/http,7860/http,8001/http,8080/http" \
  --env '{"AI_LAB_ROOT":"/workspace/ai-lab"}' \
  --readme "Disposable AI Lab: Launcher :3000, ComfyUI :8188, Jupyter :8888. No persistent volume. Export the project ZIP before stopping the Pod."
