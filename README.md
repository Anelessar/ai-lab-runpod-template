# AI Lab — одноразовая лаборатория на RunPod

Эта папка — воспроизводимый шаблон рабочей среды. Docker-образ содержит ComfyUI, Launcher, каталог инструментов и установщики, но **не содержит AI-моделей**. При создании Pod всё начинается с чистого состояния; нужные программы, модели и workflow устанавливаются из единой панели. Перед остановкой Pod активный проект скачивается одним ZIP.

## Что открывается после старта

| Порт | Интерфейс | Назначение |
|---|---|---|
| `3000` | AI Lab Launcher | проекты, инструменты, установка, запуск и экспорт |
| `8188` | ComfyUI | все ComfyUI-модели и workflow |
| `8888` | JupyterLab | файлы и ручная диагностика |
| `7860`, `8001`, `8080` | standalone UI | интерфейс запущенного инструмента |

Launcher разрешает одновременно запускать только один тяжёлый standalone-инструмент. ComfyUI работает постоянно. Это уменьшает риск, что две модели случайно займут VRAM одновременно.

## Структура внутри Pod

```text
/opt/ComfyUI/                    # ComfyUI, уже в Docker-образе
/opt/ai-lab-template/            # Launcher, manifests, adapters, installers
/workspace/ai-lab/
├── tools/<tool-id>/              # код standalone-инструмента
├── environments/<tool-id>/       # отдельное Python-окружение
├── models/
│   ├── comfyui/                  # checkpoints/loras/vae/...
│   └── standalone/<tool-id>/     # веса отдельного инструмента
├── cache/                         # Hugging Face/ModelScope cache
├── projects/<project-id>/
│   ├── inputs/                    # исходники и переданные файлы
│   ├── assets/                    # промежуточные материалы
│   ├── runs/comfyui/              # output ComfyUI
│   ├── runs/<tool-id>/             # output standalone-инструмента
│   ├── workflows/                 # изменённые workflow проекта
│   └── final/                     # финальные результаты
├── bridge/comfyui/                # ссылки на активный проект
├── logs/                           # установка, обработка, сервисы
└── state/                          # активный проект
```

Путь до ComfyUI на RunPod может отличаться от локального пути: Launcher не зашивает его в workflow. ComfyUI получает общие model-папки через `extra_model_paths.yaml`, а input/output — через стабильный bridge. При смене проекта bridge переключается автоматически.

## Обычный сеанс

1. Создать Pod из шаблона и открыть порт `3000`.
2. Создать проект или оставить `default`.
3. В карточке инструмента нажать `Установить программу`, затем `Скачать модели` или `Скачать workflow`.
4. Для ComfyUI открыть порт `8188` и загрузить JSON из папки workflow. Ссылки на официальные модели находятся внутри workflow; веса сохраняются в `/workspace/ai-lab/models/comfyui`.
5. Для standalone-инструмента нажать `Запустить` или заполнить встроенную форму обработки.
6. Результаты появляются в общей галерее. Кнопка `Передать в…` копирует выбранный файл во входную папку следующего инструмента.
7. Нажать `Скачать проект ZIP` и только после проверки архива остановить Pod.

ZIP включает `inputs`, `assets`, `runs`, `workflows`, `final` и историю проекта. Веса, окружения, caches и исходники программ туда не попадают.

## Локальная проверка Launcher на Mac

GPU и модели не нужны:

```bash
cd /Users/artem/AI-Lab/runpod-template/launcher
uv sync --extra dev
AI_LAB_ROOT=/tmp/ai-lab-local uv run uvicorn app.main:app --port 3000
```

Открыть `http://localhost:3000`. Кнопки GPU-инструментов на Mac предназначены только для проверки интерфейса; реальный inference выполняется в RunPod.

## Сборка Docker-образа

Образ рассчитан на `linux/amd64` и NVIDIA CUDA. Собирать его на Mac необязательно: workflow `.github/workflows/build-image.yml` публикует образ в GitHub Container Registry после push в `main`.

Для ручной сборки:

```bash
cd /Users/artem/AI-Lab/runpod-template
docker buildx build --platform linux/amd64 -t ghcr.io/anelessar/ai-lab-runpod-template:stable .
```

Если репозиторий или образ приватный, в RunPod нужно добавить credentials для GHCR. Для gated-моделей передавайте `HF_TOKEN` через RunPod Secret, а не записывайте его в файлы этого проекта. `JUPYTER_TOKEN` также можно задать секретом; если его нет, контейнер создаёт одноразовый токен и печатает его в startup log.

## Создание шаблона и Pod

После установки и авторизации `runpodctl`:

```bash
cd /Users/artem/AI-Lab/runpod-template
AI_LAB_IMAGE=ghcr.io/anelessar/ai-lab-runpod-template:stable \
  ./scripts/create-runpod-template.sh

AI_LAB_RUNPOD_TEMPLATE_ID=YOUR_TEMPLATE_ID \
  AI_LAB_GPU_ID="NVIDIA B200" \
  ./scripts/start-pod.sh
```

Шаблон по умолчанию создаёт 500 GB **одноразового container disk** и `0 GB` persistent volume. Размер можно изменить через `AI_LAB_CONTAINER_GB`. Скрипт ставит автоматическую остановку через 12 часов (`AI_LAB_STOP_AFTER`), чтобы забытый Pod не продолжал расходовать деньги. Для обычных image/audio тестов можно выбрать более дешёвую GPU; JoyAI требует Blackwell, а SCoPE и крупнейшие video-модели требуют много VRAM.

## Насколько автоматизирован каталог

- `ready` — есть проверяемый путь установки и запуска/workflow в Launcher.
- `catalogued` — программа ставится отдельно, но её официальный CLI ещё нужно закрепить после первого прикладного теста.
- `manual` — интеграция зависит от community nodes или неполного публичного релиза.

Эти статусы относятся к качеству интеграции в лабораторию, а не к качеству самой модели. Каталог хранится в `manifests/*.yaml`; чтобы добавить инструмент, достаточно нового manifest без изменения интерфейса.

## Важное ограничение

Система воспроизводит оболочку, версии репозиториев с указанным `ref` и способ размещения данных. Она не гарантирует, что сторонний проект, изменивший зависимости или gated-доступ, установится без корректировки. Поэтому первый запуск каждого `catalogued` инструмента — часть тестирования и должен закончиться фиксацией рабочего commit/команды в manifest.
