# Статус standalone-инструментов

Файл генерируется командой `python3 scripts/report-standalone.py --write`
из `manifests/*.yaml`. Значение `verified` описано в README.

| Инструмент | verified | Адаптер | Как запустить | Commit | Лицензия | Веса |
|---|---|---|---|---|---|---|
| **HiFi-Inpaint** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Запустить тест», результат в `runs/hifi-inpaint/` | `606a21e7e152` | Apache-2.0 | — |
| **HunyuanImage-3.0-Instruct** | catalogued | — | только вручную внутри Pod | — | — | — |
| **ReDesign** | catalogued | — | только вручную внутри Pod | `5023ec3aca2b` | NOASSERTION | — |
| **SpatialEdit** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Скачать модели» → «Запустить тест», результат в `runs/spatialedit/` | `8b0b3519e88a` | MIT | ~60 GB |
| **UniGenDet** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Скачать модели» → «Запустить тест», результат в `runs/unigenddet/` | `94e039dec7c2` | MIT | ~29 GB |
| **VIBE 2B** | catalogued | — | только вручную внутри Pod | — | — | — |
| **WindowSeat v1.0** | catalogued | — | только вручную внутри Pod | — | — | — |
| **CoInteract** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Скачать модели» → «Запустить тест», результат в `runs/cointeract/` | `8858e65e93d6` | Apache-2.0 | ~60 GB |
| **InteractAvatar** | catalogued | — | только вручную внутри Pod | `5ca013e57189` | Apache-2.0 | — |
| **SCoPE** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Скачать модели» → «Запустить тест», результат в `runs/scope/` | `6658d0e28664` | NOASSERTION | ~67 GB |
| **JoyAI-Video-Edit 0811** | installable | долгоживущий HTTP UI | Launcher → «Установить программу» → «Скачать модели» → «Запустить» → «Открыть UI» (порт 7860) | `6134e39da948` | Apache-2.0 | ~51 GB |
| **MatAnyone2** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Запустить тест», результат в `runs/matanyone2/` | `0079197acd6d` | NOASSERTION | качает сам при первом запуске |
| **ReCo / ReCo_Ref** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Скачать модели» → «Запустить тест», результат в `runs/reco/` | `a5838412dff3` | Apache-2.0 | — |
| **V-RGBX** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Скачать модели» → «Запустить тест», результат в `runs/v-rgbx/` | `4de559b89f3e` | Apache-2.0 | — |
| **IndexTTS-2.5** | launchable | долгоживущий HTTP UI | Launcher → «Установить программу» → «Скачать модели» → «Запустить» → «Открыть UI» (порт 7860) | `4f8792ff120c` | — | ~6 GB |
| **LongCat-AudioDiT 1B** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Скачать модели» → «Запустить тест», результат в `runs/longcat-audiodit/` | `12c76b51d2a8` | MIT | ~3 GB |
| **MiDashengLM-Gen** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Запустить тест», результат в `runs/midashenglm-gen/` | `fcebd304948d` | Apache-2.0 | качает сам при первом запуске |
| **MioTTS-2.6B** | catalogued | — | только вручную внутри Pod | — | — | — |
| **MOSS-TTS v1.5** | installable | долгоживущий HTTP UI | Launcher → «Установить программу» → «Скачать модели» → «Запустить» → «Открыть UI» (порт 7860) | `58b20a0d5fcc` | Apache-2.0 | ~12 GB |
| **OmniVoice** | installable | долгоживущий HTTP UI | Launcher → «Установить программу» → «Запустить» → «Открыть UI» (порт 7860) | `38e992bc60f8` | Apache-2.0 | качает сам при первом запуске |
| **TADA 1B / 3B** | unavailable | — | недоступен | — | — | — |
| **VibeVoice-Realtime 0.5B** | installable | долгоживущий HTTP UI | Launcher → «Установить программу» → «Скачать модели» → «Запустить» → «Открыть UI» (порт 7860) | `94da20d98b2f` | MIT | ~2 GB |
| **LavaSR v2** | smoke-tested | one-shot CLI-задача | Launcher → «Установить программу» → «Запустить тест», результат в `runs/lavasr-v2/` | `33ac04089251` | Apache-2.0 | ~50 MB |
| **Qwen3-ASR 1.7B / 0.6B** | installable | one-shot CLI-задача | Launcher → «Установить программу» → «Скачать модели» → «Запустить тест», результат в `runs/qwen3-asr/` | `7c6daf77a242` | Apache-2.0 | ~4 GB |

## Что ещё недоступно и почему

- **HunyuanImage-3.0-Instruct** (`catalogued`) — Отдельного репозитория с installer нет — только inference-код в model card, рассчитанный на multi-GPU. Одноразовый Pod AI Lab с одной GPU для него не подходит.
- **ReDesign** (`catalogued`) — Официальная установка — conda environment.yml плюс post_install.sh, который доставляет PyTorch cu128, PaddlePaddle, diffusers из git, sam2 и CUDA-расширение GroundingDINO. Это не воспроизводится в изолированном uv-окружении AI Lab, поэтому кнопки установки нет: ставить вручную в Pod.
- **VIBE 2B** (`catalogued`) — У проекта есть страница и веса, но нет репозитория с installer и зафиксированным commit, поэтому автоматическая установка не заводится.
- **WindowSeat v1.0** (`catalogued`) — Кода в виде репозитория нет — только model card с inference-сниппетом. Закреплять commit нечего, поэтому автоматической установки тоже нет.
- **InteractAvatar** (`catalogued`) — Официальная установка требует conda (librosa и ffmpeg ставятся из conda-forge) и сборки flash_attn 2.7.4.post1 без build isolation. Запуск идёт через шелл-скрипт с путями, зашитыми внутри, а не через CLI с аргументами, поэтому кнопки автозапуска нет.
- **MioTTS-2.6B** (`catalogued`) — Есть только model card со сниппетом; официального репозитория с installer и закрепляемым commit нет, поэтому автоустановки нет.
- **TADA 1B / 3B** (`unavailable`) — Есть только блог-анонс Hume: официальный inference-репозиторий и точные веса для 1B/3B в AI Lab не зафиксированы, поэтому закреплять нечего и обещать запуск нельзя.

## Настоящие команды запуска

- **HiFi-Inpaint**: `python inference.py --base_model_path ... --lora_path ... --ref_image ... --mask_image ... --output ...`
- **HunyuanImage-3.0-Instruct**: `официальный inference-код из model card`
- **ReDesign**: `conda env create -f environment.yml && bash post_install.sh && python -m ReDesign.run_single_image`
- **SpatialEdit**: `python spatialedit_demo.py (демо без аргументов; AI Lab вызывает ту же последовательность через adapters/run_spatialedit.py)`
- **UniGenDet**: `python demo.py --mode t2i|detection --model_path ./pretrained/bagel_7b_mot --output_dir ...`
- **VIBE 2B**: `inference-сниппет из model card / Hugging Face Space`
- **WindowSeat v1.0**: `inference-сниппет из model card`
- **CoInteract**: `python batch_infer.py --csv_path ... --output_dir ...`
- **InteractAvatar**: `bash test_inter_tia2mv_GPu_hoi.sh (обёртка над test_wanx_tia2mv_obj_back.py)`
- **SCoPE**: `python inference.py --model_path ... --input_image ... --prompt ... --camera_path ... --output_path ...`
- **JoyAI-Video-Edit 0811**: `bash deploy/run_server.sh → uvicorn xvideo/serving/serve_joyomni_streaming.py, Web UI на GET /`
- **MatAnyone2**: `python inference_matanyone2.py -i <video|frames> -m <first-frame mask> -o <output dir>`
- **ReCo / ReCo_Ref**: `python scripts/inference_reco_single.py --task_name ... --test_txt_file_name ... --base_video_folder ...`
- **V-RGBX**: `python vrgbx_edit_inference.py --video_path ... --edit_type ... --edit_x_path ...`
- **IndexTTS-2.5**: `uv run webui.py --host 0.0.0.0 --port N --model_dir <checkpoints> --version 2.5`
- **LongCat-AudioDiT 1B**: `python inference.py --text ... --output_audio ... --model_dir ...`
- **MiDashengLM-Gen**: `uv run python infer.py --text ... --output_dir ...`
- **MioTTS-2.6B**: `Python/Gradio-сниппет из model card`
- **MOSS-TTS v1.5**: `python clis/moss_tts_local_v1.5_app.py --host 0.0.0.0 --port N --model-dir ... --codec-dir ...`
- **OmniVoice**: `omnivoice-demo --ip 0.0.0.0 --port N (web UI) либо omnivoice-infer --model k2-fsa/OmniVoice --text ... --output ...`
- **TADA 1B / 3B**: `не зафиксирован`
- **VibeVoice-Realtime 0.5B**: `python demo/vibevoice_realtime_demo.py --port N --model_path microsoft/VibeVoice-Realtime-0.5B`
- **LavaSR v2**: `pip install git+https://github.com/ysharma3501/LavaSR.git, затем Python API LavaEnhance2(...).enhance() (CLI проект не публикует)`
- **Qwen3-ASR 1.7B / 0.6B**: `pip install qwen-asr, затем Python API Qwen3ASRModel.transcribe (готового CLI проект не публикует)`
