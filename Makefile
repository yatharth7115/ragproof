PYTHON ?= .venv/bin/python

.PHONY: install lab test integration evaluate serve build check-migrations
install:
	$(PYTHON) -m pip install -c constraints.txt -e '.[dev,api,otlp,storage,integrations]'
lab:
	docker compose -f infrastructure/docker-compose.lab.yml up -d --wait
test:
	$(PYTHON) -m unittest discover -s tests -v
integration:
	RAGPROOF_RUN_STORAGE_INTEGRATION=1 $(PYTHON) -m unittest discover -s tests -v
evaluate:
	$(PYTHON) -m ragproof_verifier.evaluate
serve:
	$(PYTHON) -m uvicorn ragproof_store.api:create_app --factory --host 127.0.0.1 --port 8080
build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir dist .
check-migrations:
	$(PYTHON) -m ragproof_store.migrations --check
