# Recipes must stay valid under both cmd.exe (GnuWin32 make from PowerShell)
# and sh (macOS, Git Bash): plain commands only, no shell-specific syntax.

CLIP    ?= tests/fixtures/lab-avolo-58m22-70m00.mp4
TRACE   ?= tests/corpus/traces/trace.jsonl
SUMMARY ?= tests/corpus/traces/summary.json
REPLAY  ?= tests/corpus/traces/replay.jsonl
FPS     ?= 12
MODEL   ?= models/yolo11m-pose.pt
ARGS    ?=

# Ultralytics would otherwise pip-install missing packages behind uv's back.
export YOLO_AUTOINSTALL := false

.DEFAULT_GOAL := help
.PHONY: help sync check test model engine bench probe setup setup-camera run corpus replay

# $(info) is printed by make itself, so no shell quoting rules apply.
help:
	$(info Cibles :)
	$(info   make sync          installe l'environnement (uv sync))
	$(info   make check         ruff F821 puis pytest, à lancer avant toute exécution)
	$(info   make test          pytest seul)
	$(info   make model         télécharge les poids YOLO vers MODEL)
	$(info   make engine        exporte MODEL en moteur TensorRT fp16, lié à ce GPU et à ce pilote)
	$(info   make bench         latence .pt contre .engine, lot unitaire, sur les images du corpus)
	$(info   make probe         go/no-go OBS et latences, sort 0 si tout passe)
	$(info   make setup         (re)construit la scène sur la vidéo de test)
	$(info   make setup-camera  (re)construit la scène sur la caméra)
	$(info   make run           la boucle de recadrage)
	$(info   make corpus        rejeu déterministe hors OBS : CLIP vers TRACE et SUMMARY)
	$(info   make replay        rejoue TRACE (--from-trace) vers REPLAY et SUMMARY, sans décoder ni détecter)
	$(info )
	$(info Arguments en plus : make run ARGS="--duration 60 --upper-body")
	@cd .

sync:
	uv sync

check:
	uvx ruff check --select F821 .
	uv run pytest -q

test:
	uv run pytest -q

model:
	uv run python -c "from ultralytics.utils.downloads import attempt_download_asset as get; get('$(MODEL)')"

engine:
	uv run yolo export model=$(MODEL) format=engine half=True imgsz=640 batch=1 device=0

bench:
	uv run python tests/corpus/tools/bench_detectors.py $(MODEL)

probe:
	uv run python -m scripts.probe $(ARGS)

setup:
	uv run python -m scripts.setup_scene --force $(ARGS)

setup-camera:
	uv run python -m scripts.setup_scene --force --camera $(ARGS)

run:
	uv run python -m scripts.run $(ARGS)

corpus:
	uv run python -m scripts.corpus $(CLIP) --fps $(FPS) --out $(TRACE) --summary-json $(SUMMARY) $(ARGS)

replay:
	uv run python -m scripts.corpus --from-trace $(TRACE) --out $(REPLAY) --summary-json $(SUMMARY) $(ARGS)
