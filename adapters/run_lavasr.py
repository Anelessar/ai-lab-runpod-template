"""One-shot LavaSR v2 speech restoration for AI Lab.

LavaSR is distributed as a library (`LavaSR.model.LavaEnhance2`) with no console
entrypoint, so this wrapper exists purely to read a project asset, call the
documented `enhance()` and write a 48 kHz WAV into runs/.
"""

from __future__ import annotations

import argparse
from pathlib import Path

OUTPUT_SAMPLE_RATE = 48_000


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore one audio file with LavaSR v2")
    parser.add_argument("--audio", required=True, help="Input audio file")
    parser.add_argument("--output", required=True, help="Output 48 kHz WAV path")
    parser.add_argument("--device", default="cuda", help="torch device: cuda, cpu, mps")
    parser.add_argument("--repo", default="YatharthS/LavaSR", help="Hugging Face model id")
    parser.add_argument("--input-sr", type=int, default=None, help="Override the input sample rate")
    parser.add_argument(
        "--denoise",
        default="false",
        choices=["true", "false"],
        help="Only enable when the source actually carries noise",
    )
    parser.add_argument("--batch", default="false", choices=["true", "false"], help="Use for very long audio")
    args = parser.parse_args()

    import soundfile as sf
    from LavaSR.model import LavaEnhance2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    model = LavaEnhance2(args.repo, args.device)
    load_kwargs = {"input_sr": args.input_sr} if args.input_sr else {}
    audio, _ = model.load_audio(str(Path(args.audio).resolve()), **load_kwargs)
    restored = model.enhance(
        audio,
        denoise=args.denoise == "true",
        batch=args.batch == "true",
    )
    sf.write(output, restored.cpu().numpy().squeeze(), OUTPUT_SAMPLE_RATE)
    print(f"restored -> {output}")


if __name__ == "__main__":
    main()
