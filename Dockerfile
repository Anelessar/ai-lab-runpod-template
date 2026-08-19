ARG RUNPOD_BASE=runpod/pytorch:1.1.0-cu1281-torch280-ubuntu2204
FROM ${RUNPOD_BASE}

ARG COMFYUI_REF=b963f4ad210a42841ab23dfc28a84143a0cce227
ARG COMFYUI_PIFLOW_REF=1e161f39ef4bf04c9033ed6a6cdc4d8373cc9e72
ARG COMFYUI_HF_DOWNLOADER_REF=2bba5db6a52479e8ad465dbade19dd0da0784bd3
ARG COMFYUI_MANAGER_REF=f39cbd56fecae0b27a446c0cd450cd591f3a8bea
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    AI_LAB_TEMPLATE_ROOT=/opt/ai-lab-template \
    AI_LAB_ROOT=/workspace/ai-lab

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl ffmpeg git git-lfs libgl1 libglib2.0-0 ninja-build \
      python3-dev python3-venv unzip wget \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install --system

RUN python3 -m pip install --no-cache-dir --upgrade pip uv

RUN git clone --filter=blob:none https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI \
    && git -C /opt/ComfyUI checkout ${COMFYUI_REF} \
    && git clone --filter=blob:none https://github.com/Lakonik/ComfyUI-piFlow.git /opt/ComfyUI/custom_nodes/ComfyUI-piFlow \
    && git -C /opt/ComfyUI/custom_nodes/ComfyUI-piFlow checkout ${COMFYUI_PIFLOW_REF} \
    && git clone --filter=blob:none https://github.com/jnxmx/ComfyUI_HuggingFace_Downloader.git /opt/ComfyUI/custom_nodes/ComfyUI_HuggingFace_Downloader \
    && git -C /opt/ComfyUI/custom_nodes/ComfyUI_HuggingFace_Downloader checkout ${COMFYUI_HF_DOWNLOADER_REF} \
    && rm -rf /opt/ComfyUI/custom_nodes/ComfyUI_HuggingFace_Downloader/node_modules \
    && git clone --filter=blob:none https://github.com/Comfy-Org/ComfyUI-Manager.git /opt/ComfyUI/custom_nodes/comfyui-manager \
    && git -C /opt/ComfyUI/custom_nodes/comfyui-manager checkout ${COMFYUI_MANAGER_REF} \
    && uv pip install --system -r /opt/ComfyUI/requirements.txt \
    && uv pip install --system -r /opt/ComfyUI/custom_nodes/ComfyUI_HuggingFace_Downloader/requirements.txt \
    && uv pip install --system -r /opt/ComfyUI/custom_nodes/comfyui-manager/requirements.txt \
    && uv pip install --system "huggingface_hub[cli]" jupyterlab

WORKDIR /opt/ai-lab-template
COPY launcher /opt/ai-lab-template/launcher
COPY manifests /opt/ai-lab-template/manifests
COPY workflows /opt/ai-lab-template/workflows
COPY adapters /opt/ai-lab-template/adapters
COPY scripts /opt/ai-lab-template/scripts
COPY docker /opt/ai-lab-template/docker

RUN ln -sf /usr/local/bin/python /usr/local/bin/python3 \
    && uv venv /opt/ai-lab-launcher-venv --python /usr/local/bin/python \
    && uv pip install --python /opt/ai-lab-launcher-venv/bin/python /opt/ai-lab-template/launcher \
    && chmod +x /opt/ai-lab-template/docker/entrypoint.sh /opt/ai-lab-template/scripts/*.sh

EXPOSE 3000 8188 8888 7860 8001 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:3000/health || exit 1

ENTRYPOINT ["/opt/ai-lab-template/docker/entrypoint.sh"]
