PY ?= python
PORT ?= 8030
VENV ?= .venv

.PHONY: help install run test bench docker clean

help:
	@echo "make install   create $(VENV) and install runtime + dev dependencies"
	@echo "make run       serve the API and dashboard on http://127.0.0.1:$(PORT)"
	@echo "make test      run the full suite (detection, API, BDD, tenancy)"
	@echo "make bench     benchmark detection and log search"
	@echo "make docker    build the container image"
	@echo "make clean     remove caches and the local SQLite database"

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -r requirements-dev.txt

run:
	$(VENV)/bin/python -m uvicorn adsentry.main:app --app-dir src --reload --port $(PORT)

test:
	$(VENV)/bin/python -m pytest -q

bench:
	LOG_LEVEL=CRITICAL PYTHONPATH=src $(VENV)/bin/python scripts/bench_detection.py

docker:
	docker build -t adsentry:1.0.0 .

clean:
	rm -rf .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -f adsentry.db
