#!/usr/bin/env bash
set -Eeuo pipefail

TEMPLATE_ID="${AI_LAB_RUNPOD_TEMPLATE_ID:?Set AI_LAB_RUNPOD_TEMPLATE_ID}"
GPU_ID="${AI_LAB_GPU_ID:-NVIDIA B200}"
GPU_COUNT="${AI_LAB_GPU_COUNT:-1}"
POD_NAME="${AI_LAB_POD_NAME:-ai-lab-test}"
STOP_AFTER="${AI_LAB_STOP_AFTER:-12h}"

if ! command -v runpodctl >/dev/null 2>&1; then
  echo "runpodctl is not installed. Install and configure it with runpodctl doctor." >&2
  exit 1
fi

runpodctl pod create \
  --template-id "$TEMPLATE_ID" \
  --gpu-id "$GPU_ID" \
  --gpu-count "$GPU_COUNT" \
  --min-cuda-version 12.8 \
  --stop-after "$STOP_AFTER" \
  --name "$POD_NAME"
