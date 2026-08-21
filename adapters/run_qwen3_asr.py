"""One-shot Qwen3-ASR transcription for AI Lab.

Qwen3-ASR ships a Python API (`Qwen3ASRModel.from_pretrained(...).transcribe(...)`)
and no console entrypoint, so AI Lab needs this thin wrapper to turn a project
asset into files under runs/. It adds nothing to the model: the call, the
arguments and the result fields are exactly what the upstream README documents.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe one file with Qwen3-ASR")
    parser.add_argument("--model", required=True, help="Local directory of the Qwen3-ASR checkpoint")
    parser.add_argument("--audio", required=True, help="Audio or video file to transcribe")
    parser.add_argument("--output-dir", required=True, help="Directory that receives transcript.txt/json")
    parser.add_argument(
        "--language",
        default="auto",
        help='Force a language name (for example "English"); "auto" lets the model decide',
    )
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-new-tokens", type=int, default=4096)
    args = parser.parse_args()

    import torch
    from qwen_asr import Qwen3ASRModel

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model = Qwen3ASRModel.from_pretrained(
        args.model,
        dtype=torch.bfloat16,
        device_map=args.device,
        max_new_tokens=args.max_new_tokens,
    )
    results = model.transcribe(
        audio=str(Path(args.audio).resolve()),
        language=None if args.language.lower() in {"", "auto"} else args.language,
    )

    result = results[0]
    text = getattr(result, "text", "")
    (output_dir / "transcript.txt").write_text(text, encoding="utf-8")
    (output_dir / "transcript.json").write_text(
        json.dumps(
            {
                "source": str(Path(args.audio).resolve()),
                "language": getattr(result, "language", None),
                "text": text,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"transcript -> {output_dir / 'transcript.txt'}")


if __name__ == "__main__":
    main()
