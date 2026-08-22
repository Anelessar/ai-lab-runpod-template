"""One-shot SpatialEdit run for AI Lab: one photo + one instruction -> one PNG.

SpatialEdit ships `spatialedit_demo.py`, which is not runnable as-is: it pins
`cuda:1`, hardcodes one example image and prompt, and its checkpoint paths -
like the ones in `configs/spatialedit_base_config.py` - are literal
`your_base_path/...` placeholders the README tells you to edit by hand.

This wrapper changes none of the model behaviour. It performs exactly the load
and inference sequence the official demo performs, with the paths and the
device supplied as arguments instead of edited into the source, so the Launcher
can drive it from an uploaded image.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Names the upstream demo and README use for the released checkpoints.
DIT_CHECKPOINT = "CKPT_PT.pth"
LORA_DIRECTORY = "CKPT_CT_lora"
VAE_CHECKPOINT = "Wan2.1_VAE.pth"
TEXT_ENCODER_DIR = "Qwen--Qwen3-VL-8B-Instruct"


def find_one(root: Path, name: str, *, directory: bool = False) -> Path:
    """Locate a released file by name, wherever the download nested it.

    The three checkpoints come from three different Hugging Face repos whose
    internal layout is not part of any documented contract, so search rather
    than guess - and when it is absent, say which file is missing instead of
    failing later inside the model loader.
    """
    if not root.exists():
        raise SystemExit(f"Каталог моделей не найден: {root}")
    wanted = Path.is_dir if directory else Path.is_file
    matches = [path for path in sorted(root.rglob(name)) if wanted(path)]
    if not matches:
        raise SystemExit(
            f"Не найден {'каталог' if directory else 'файл'} {name} в {root}. "
            "Скачайте модели кнопкой в Launcher и повторите."
        )
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Edit one image with SpatialEdit")
    parser.add_argument("--image", required=True, help="Source photo")
    parser.add_argument("--prompt", required=True, help="Spatial editing instruction")
    parser.add_argument("--output", required=True, help="Output PNG path")
    parser.add_argument("--model-dir", required=True, help="AI Lab model directory for this tool")
    parser.add_argument("--config", default="configs/spatialedit_base_config.py")
    parser.add_argument("--device", default="cuda:0", help="The demo pins cuda:1; single-GPU Pods need cuda:0")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--guidance", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--basesize", type=int, default=1024)
    parser.add_argument("--neg-prompt", default="")
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    dit_ckpt = find_one(model_dir, DIT_CHECKPOINT)
    lora_dir = find_one(model_dir, LORA_DIRECTORY, directory=True)
    vae_ckpt = find_one(model_dir, VAE_CHECKPOINT)
    text_encoder = model_dir / TEXT_ENCODER_DIR
    if not text_encoder.is_dir():
        raise SystemExit(f"Не найден text encoder {text_encoder}. Скачайте модели в Launcher.")

    # The repository is the working directory, and its `src` package must win
    # over anything else on the path.
    sys.path.insert(0, str(Path.cwd()))

    import torch
    from peft import PeftModel
    from PIL import Image

    from src.config import load_config_class_from_pyfile
    from src.models import load_dit, load_pipeline
    from src.utils import _dynamic_resize_from_bucket, seed_everything

    seed_everything(args.seed)
    device = torch.device(args.device)

    cfg = load_config_class_from_pyfile(args.config)()
    # Same overrides the demo makes, plus the two the README says to edit in
    # the config file by hand.
    cfg.use_lora = False  # load full params first, merge the LoRA manually
    cfg.training_mode = False
    cfg.use_fsdp_inference = False
    cfg.hsdp_shard_dim = 1
    cfg.dit_ckpt_type = "pt"
    cfg.dit_ckpt = str(dit_ckpt)
    cfg.vae_arch_config["pretrained"] = str(vae_ckpt)
    cfg.text_encoder_arch_config["params"]["text_encoder_ckpt"] = str(text_encoder)

    print(f"DiT      : {dit_ckpt}", flush=True)
    print(f"LoRA     : {lora_dir}", flush=True)
    print(f"VAE      : {vae_ckpt}", flush=True)
    print(f"encoder  : {text_encoder}", flush=True)

    dit = load_dit(cfg, device=device)
    dit.requires_grad_(False)
    dit.eval()
    dit = PeftModel.from_pretrained(dit, str(lora_dir))
    dit = dit.merge_and_unload()
    pipeline = load_pipeline(cfg, dit, device)

    image = Image.open(args.image).convert("RGB")
    resized = _dynamic_resize_from_bucket(image, basesize=args.basesize)
    width, height = resized.size

    # Prompt envelope copied verbatim from the official demo.
    image_tokens = "<image>\n"
    prompts = [f"<|im_start|>user\n{image_tokens}{args.prompt}<|im_end|>\n"]
    negative = [f"<|im_start|>user\n{image_tokens}{args.neg_prompt}<|im_end|>\n"]

    generator = torch.Generator(device=pipeline.transformer.device).manual_seed(args.seed)
    with torch.inference_mode():
        frames = pipeline(
            prompt=prompts,
            negative_prompt=negative,
            images=[resized],
            height=height,
            width=width,
            num_frames=1,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance,
            generator=generator,
            num_videos_per_prompt=1,
            output_type="pt",
            return_dict=False,
            enable_denormalization=cfg.enable_denormalization,
        )

    tensor = (frames[0, -1, 0] * 255).to(torch.uint8).cpu()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(tensor.permute(1, 2, 0).numpy()).save(output)
    print(f"edited image -> {output}", flush=True)


if __name__ == "__main__":
    main()
