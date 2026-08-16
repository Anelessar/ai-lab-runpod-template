#!/usr/bin/env bash
set -Eeuo pipefail

TOOL_DIR="${1:?tool directory is required}"
ENV_DIR="${2:?environment directory is required}"

uv venv "$ENV_DIR" --python 3.10
uv pip install --python "$ENV_DIR/bin/python" -r "$TOOL_DIR/deploy/requirements.txt"

# Portable baseline: use BF16 + cuDNN/SDPA. This avoids baking an architecture-
# specific SageAttention/FA4 build into the template. It is slower but easier to
# reproduce; a later benchmark can enable the optimized Blackwell path.
JOYOMNI_OPS_NO_FP8=1 uv pip install \
  --python "$ENV_DIR/bin/python" \
  --no-build-isolation \
  "$TOOL_DIR/deploy/joyomni_ops"

echo "JoyAI baseline environment installed at $ENV_DIR"
