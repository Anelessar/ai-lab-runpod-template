from __future__ import annotations

import argparse
from pathlib import Path

import soundfile as sf
from transformers import AutoModel


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate one MiDashengLM audio scene")
    parser.add_argument("--model", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    model = AutoModel.from_pretrained(args.model, trust_remote_code=True).cuda()
    result = model.generate(args.text)
    sf.write(output, result["audio"], result["sample_rate"])
    print(output)


if __name__ == "__main__":
    main()
