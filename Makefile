# Einziges Interface fuer Objektradar. Kein direkter Aufruf von manage.py.
PY := .venv/bin/python

.PHONY: help db-up db-down db-logs check makemigrations migrate showmigrations createcachetable run superuser passwort shell test

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

createcachetable:  ## Tabelle `django_cache` anlegen - einmal je Umgebung
	$(PY) manage.py createcachetable

run:
	$(PY) manage.py runserver

superuser:        ## Konto von Hand anlegen - es gibt keinen Registrierungsweg
	$(PY) manage.py createsuperuser

passwort:         ## Passwort neu setzen - make passwort BENUTZER=Nico
# Fuer den Fall, dass jemand sein Passwort vergisst. Es gibt keinen Versand per
# Mail und keinen Zuruecksetzweg in der Oberflaeche; das neue Passwort
# ueberreicht Steffen einzeln.
#
# `changepassword` bringt Django mit und fragt VERDECKT ab. Deshalb kein
# eigener Befehl und kein Passwort als Argument: als Argument stuende es in der
# Shell-Historie und in der Prozessliste.
	$(PY) manage.py changepassword $(BENUTZER)

shell:
	$(PY) manage.py shell

test:              ## make test [TESTS=objekte.tests.UebernahmeTests]
	$(PY) manage.py test --settings=config.settings_test $(TESTS)
