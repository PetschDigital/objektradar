# Einziges Interface fuer Objektradar. Kein direkter Aufruf von manage.py.
PY := .venv/bin/python

.PHONY: help db-up db-down db-logs check makemigrations migrate showmigrations run superuser shell test

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | grep -v '^help$$' | sed 's/^/  make /'

db-up:            ## Postgres-Container starten (Port 5433)
	docker compose up -d db

db-down:
	docker compose down

db-logs:
	docker compose logs -f db

check:
	$(PY) manage.py check

makemigrations:   ## make makemigrations APP=objekte
	$(PY) manage.py makemigrations $(APP)

migrate:
	$(PY) manage.py migrate

showmigrations:
	$(PY) manage.py showmigrations

run:
	$(PY) manage.py runserver

superuser:        ## Konto von Hand anlegen - es gibt keinen Registrierungsweg
	$(PY) manage.py createsuperuser

shell:
	$(PY) manage.py shell

test:
	$(PY) manage.py test
