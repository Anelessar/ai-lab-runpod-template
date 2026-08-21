#!/usr/bin/env python3
"""Render docs/STANDALONE-STATUS.md from the manifests.

The report is generated so it cannot quietly drift away from what the Launcher
actually does; a test compares the committed file with this output.

    python3 scripts/report-standalone.py --write
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "launcher"))

from app.manifest import ManifestRegistry

REPORT = ROOT / "docs" / "STANDALONE-STATUS.md"

ADAPTER_LABEL = {
    "http-ui": "долгоживущий HTTP UI",
    "cli-job": "one-shot CLI-задача",
    "comfyui": "ComfyUI",
    "web": "внешний веб-сервис",
    "hf-download": "только загрузка весов",
    "none": "—",
}

MANUAL = "только вручную внутри Pod"


def how_to_launch(tool) -> str:
    """The steps the Launcher will actually offer for this tool, in order."""
    if tool.verified == "unavailable":
        return "недоступен"
    if not tool.is_automatable:
        return MANUAL
    steps = ["«Установить программу»"]
    if tool.models.mode not in {"disabled", "manual"}:
        steps.append("«Скачать модели»")
    if tool.adapter_type == "http-ui":
        steps += ["«Запустить»", "«Открыть UI» (порт 7860)"]
        return "Launcher → " + " → ".join(steps)
    if tool.adapter_type == "cli-job":
        steps.append("«Запустить тест»")
        return "Launcher → " + " → ".join(steps) + f", результат в `runs/{tool.id}/`"
    return MANUAL


def render(registry: ManifestRegistry) -> str:
    lines = [
        "# Статус standalone-инструментов",
        "",
        "Файл генерируется командой `python3 scripts/report-standalone.py --write`",
        "из `manifests/*.yaml`. Значение `verified` описано в README.",
        "",
        "| Инструмент | verified | Адаптер | Как запустить | Commit | Лицензия | Веса |",
        "|---|---|---|---|---|---|---|",
    ]
    for tool in registry.standalone():
        how = how_to_launch(tool)
        gb = tool.models.size_gb or tool.download_gb
        if not gb:
            size = "качает сам при первом запуске" if tool.models.mode == "disabled" else "—"
        elif gb < 1:
            size = f"~{gb * 1000:.0f} MB"
        else:
            size = f"~{gb:g} GB"
        commit = f"`{tool.ref[:12]}`" if tool.ref != "main" else "—"
        lines.append(
            f"| **{tool.name}** | {tool.verified} | {ADAPTER_LABEL[tool.adapter_type]} | {how} "
            f"| {commit} | {tool.license_spdx or '—'} | {size} |"
        )

    lines += ["", "## Что ещё недоступно и почему", ""]
    for tool in registry.standalone():
        if tool.verified in {"installable", "launchable", "smoke-tested"}:
            continue
        reason = tool.unavailable_reason or tool.install.instructions
        lines.append(f"- **{tool.name}** (`{tool.verified}`) — {reason}")

    lines += ["", "## Настоящие команды запуска", ""]
    for tool in registry.standalone():
        if not tool.entrypoint:
            continue
        lines.append(f"- **{tool.name}**: `{tool.entrypoint}`")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write docs/STANDALONE-STATUS.md")
    args = parser.parse_args()

    text = render(ManifestRegistry(ROOT / "manifests").load())
    if args.write:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(text, encoding="utf-8")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    else:
        # write, not print: stdout must byte-match the file so the drift test
        # compares content rather than a trailing newline.
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
