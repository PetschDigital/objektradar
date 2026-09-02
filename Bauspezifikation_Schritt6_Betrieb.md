# Bauspezifikation · Schritt 6 — Betrieb hinter einem Reverse Proxy

> Für Claude Code. Aufsetzend auf den Stand nach Punkt 5 (522 Tests grün).
> Stand: 01.09.2026

---

## 0. Geltung — zuerst lesen

### Worum es in dieser Runde geht

Das Werkzeug soll auf einem VPS laufen: Ubuntu 24.04, Gunicorn hinter **Caddy** als
Reverse Proxy, PostgreSQL 16 nativ. Das ändert drei Annahmen, die lokal richtig waren und
im Betrieb falsch sind. Diese Runde baut **keine neuen Funktionen**.

### Der neue Umstand, aus dem fast alles folgt

**Django sieht die Anfrage nicht mehr direkt.** Caddy nimmt sie entgegen und reicht sie an
Gunicorn auf `127.0.0.1:8200` weiter. Daraus folgt:

- `REMOTE_ADDR` ist für **jede** Anfrage `127.0.0.1` — für alle fünf Personen dieselbe
- HTTPS endet bei Caddy; Gunicorn bekommt die Anfrage über HTTP
- Gunicorn läuft mit `--workers 2`, also in **mehreren Prozessen**

### Bereits gebaut — nicht ändern, nicht erneut melden

- Punkt 5 vollständig: Filter, Sortierung, Blättern, Votum-Übersicht
- Login-Rate-Limit über den Cache, 5 Fehlversuche, 15 Minuten, HTTP 429
- Login-Template und Zugangs-URLs liegen in `konten`
- Redirect nach dem Einwerfen auf die Liste, nach der Übernahme auf die Objektansicht
- Der Härtungsblock in `config/settings.py` ab Zeile 146 (`if not DEBUG:`) ist im Grundsatz
  richtig und bleibt bestehen. Geändert werden nur die unter 4 genannten Zeilen.

### Eine falsche Fährte, die ausdrücklich nicht verfolgt wird

Beim ersten Testlauf auf dem Server fielen 335 von 522 Tests. Die Ursache ist **allein**
`SECURE_SSL_REDIRECT = True`: Die `SecurityMiddleware` antwortet vor jeder View mit
HTTP 301 auf `https://`, deshalb ist `response.context` überall `None`.

**`ALLOWED_HOSTS` ist nicht die Ursache und wird nicht angefasst.** Djangos Testrunner
ergänzt `testserver` selbst. Wer dort etwas ändert, behebt nichts und verdeckt etwas.

### Keine neuen Abhängigkeiten

Kein WhiteNoise, kein Redis, kein `django-environ`, nichts. Statische Dateien liefert
Caddy aus, der Cache läuft über die Datenbank. Ausnahme: `gunicorn` wird in
`requirements.txt` **nachgetragen** — es ist auf dem Server bereits installiert, steht
aber nicht in der Datei.

---

## 1. Testeinstellungen

Neu: `config/settings_test.py`.

```python
from config.settings import *  # noqa: F403
```

Darin abgeschaltet, weil ein Reverse Proxy im Test nicht existiert:

```
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
```

**`DEBUG` bleibt auf `False`.** Es wäre bequem, im Test `DEBUG=True` zu setzen — dann
verschwänden dieselben Fehler auch. Es ist aber falsch: `DEBUG=True` verändert
Fehlerseiten, Hostprüfung und Abfrageprotokollierung gleichzeitig und macht den Testlauf
zu einer Messung an einer Konfiguration, die es im Betrieb nicht gibt. Abgeschaltet wird
genau das, was der fehlende Proxy verursacht — nicht mehr.

Im `Makefile` läuft `make test` künftig gegen dieses Modul:

```
--settings=config.settings_test
```

Das gilt auch für `make test TESTS=…`.

**Der Zeuge dieser Zusage ist der Testlauf selbst:** Alle 522 bestehenden Tests laufen mit
`DJANGO_DEBUG=False` in der Umgebung grün durch. Ein zusätzlicher Test, der prüft, dass
`SECURE_SSL_REDIRECT` unter Testeinstellungen `False` ist, wäre eine Tautologie und wird
nicht gebaut.

---

## 2. Die Absender-Adresse hinter dem Proxy

### Der Stand und warum er kippt

`03_Technik.md` hält fest: `X-Forwarded-For` wird bewusst **nicht** gelesen, weil der Kopf
ohne vorgeschalteten Proxy frei wählbar ist und das Rate-Limit aushebeln würde. Das war
richtig, solange Django die Anfrage direkt entgegennahm.

Hinter Caddy kehrt es sich um: `REMOTE_ADDR` ist für alle Anfragen `127.0.0.1`. Das
Rate-Limit zählt dann nicht mehr je Absender, sondern **global** — fünf Fehlversuche von
irgendwem sperren alle fünf Personen für 15 Minuten. Dieser Fall tritt im Alltag ein,
nicht theoretisch.

### Was gebaut wird

Eine Einstellung, die den Proxy ausdrücklich einschaltet:

```
VERTRAUE_PROXY = env("DJANGO_VERTRAUE_PROXY", "False").lower() in {"1", "true", "yes"}
```

Standard ist **aus**. Lokal ändert sich damit nichts.

Die Ermittlung der Absender-Adresse im Rate-Limit folgt dann diesen Regeln, in dieser
Reihenfolge:

1. Ist `VERTRAUE_PROXY` aus → `REMOTE_ADDR`, wie bisher. Der Kopf wird nicht gelesen.
2. Ist `REMOTE_ADDR` **nicht** `127.0.0.1` oder `::1` → `REMOTE_ADDR`. Eine Anfrage, die
   nicht vom lokalen Proxy kommt, darf den Kopf nicht setzen dürfen.
3. Sonst: der **letzte** Eintrag aus `X-Forwarded-For`, also der am weitesten rechts.
4. Fehlt der Kopf oder ist er leer → `REMOTE_ADDR`.

**Warum der letzte und nicht der erste:** Caddy hängt die tatsächliche Absender-Adresse
rechts an das an, was der Aufrufer geschickt hat. Schickt jemand
`X-Forwarded-For: 9.9.9.9`, steht dort danach `9.9.9.9, <echte IP>`. Der linke Teil ist
frei erfunden, der rechte stammt vom Proxy. Wer den ersten Eintrag nimmt, baut genau die
Lücke ein, vor der `03` warnt.

### Zeugen

- Bei ausgeschaltetem `VERTRAUE_PROXY` hebt ein gesetzter `X-Forwarded-For` die Sperre
  nicht auf — **der bestehende Test bleibt unverändert und muss grün bleiben**
- Bei eingeschaltetem `VERTRAUE_PROXY` und `REMOTE_ADDR=127.0.0.1` zählt der rechte
  Eintrag: zwei verschiedene rechte Einträge sperren sich nicht gegenseitig
- Ein vorangestellter erfundener Eintrag (`9.9.9.9, 203.0.113.7`) hebt die Sperre für
  `203.0.113.7` **nicht** auf
- Bei eingeschaltetem `VERTRAUE_PROXY`, aber `REMOTE_ADDR=203.0.113.9` wird der Kopf
  ignoriert
- Fehlender Kopf bei eingeschaltetem `VERTRAUE_PROXY` fällt auf `REMOTE_ADDR` zurück,
  ohne Fehler

### Bekannte Grenze, die dokumentiert und nicht gebaut wird

Schaltet jemand später den Cloudflare-Proxy ein, wird die Kette zwei Sprünge lang und der
rechte Eintrag ist dann Cloudflares Adresse — womit wieder alle denselben Zähler teilen.
Das wird **jetzt nicht** gelöst. Es gehört als Kommentar an die Funktion.

---

## 3. Gemeinsamer Cache

Gunicorn läuft mit zwei Arbeitsprozessen. `LocMemCache` ist prozesslokal, das Rate-Limit
gälte je Prozess und die tatsächliche Versuchszahl verdoppelte sich.

Umstellen auf `django.core.cache.backends.db.DatabaseCache`, Tabelle `django_cache`.

Begründung gegen Redis: ein zweiter Dienst, der laufen, überwacht und abgesichert werden
müsste. Die Schreiblast des Rate-Limits sind wenige Zeilen je Anmeldeversuch.

**Der Cache-Backend bleibt auch unter Testeinstellungen der Datenbank-Cache**, damit die
Rate-Limit-Zeugen messen, was im Betrieb läuft. Der Testlauf wird dadurch langsamer; das
ist der Preis und wird in Kauf genommen. Djangos Testrunner legt die Tabelle in der
Testdatenbank selbst an.

Zurückmelden: die Laufzeit von `make test` vorher und nachher.

---

## 4. Härtung anpassen

Im Block ab Zeile 146:

- `SECURE_HSTS_SECONDS` von einem Jahr auf **300** — für die ersten Betriebstage. Ein
  gesetzter HSTS-Kopf ist im Browser bindend; geht am Zertifikat etwas schief, kommt
  niemand mehr behelfsweise über HTTP drauf. Der Wert wird nach ein paar Tagen von Hand
  hochgezogen; das gehört als Kommentar an die Zeile.
- `SECURE_HSTS_PRELOAD` **entfernen**. Preloading gilt für die Hauptdomain; eine
  Unterdomain kommt weder in die Browser-Liste noch soll sie das.
- `SECURE_HSTS_INCLUDE_SUBDOMAINS` bleibt.
- `SECURE_SSL_REDIRECT` bleibt auf `True`. Caddy leitet zwar selbst um, aber die Zeile
  kostet im Betrieb nichts und schützt, falls Gunicorn je ohne Proxy erreichbar wird.

---

## 5. Statische Dateien

```
STATIC_ROOT = BASE_DIR / "staticfiles"
```

`.gitignore` schließt `/staticfiles/` bereits aus. Ausgeliefert wird das Verzeichnis von
Caddy, nicht von Django — deshalb kein WhiteNoise.

`manage.py collectstatic` muss ohne Rückfrage durchlaufen.

---

## 6. Kleinigkeiten aus der letzten Runde

**`select_related("eingestellt_von")` entfernen**, samt dem Zeugen
`test_der_einwerfer_steht_ohne_zusatzabfrage_bereit`. Begründung: Die Liste zeigt den
Einwerfer nicht an. Der Zeuge bewacht damit eine Optimierung ohne Nutzen und sieht in
einem halben Jahr aus wie eine Anforderung. Die Zusage stand irrtümlich in der Spec zu
Punkt 5; sie war vorher nie gebaut.

**Ungültige oder zu hohe Seitenzahl führt auf die LETZTE Seite**, nicht auf Seite 1. Das
ist Djangos `Paginator.get_page()`-Verhalten und im Alltag nützlicher: Wer auf Seite 8
steht und einen Filter setzt, der auf drei Seiten kürzt, landet auf 3 statt auf 1. Die
betroffenen Zeugen werden entsprechend umgestellt; ihre Docstrings müssen die neue Zusage
nennen, nicht die alte.

Eine nicht als Zahl lesbare Seitenzahl (`?seite=abc`) führt weiterhin auf Seite 1.

**`gunicorn` in `requirements.txt` nachtragen.**

**`.env.example`:** Bei `POSTGRES_PORT` fehlt das schließende Anführungszeichen.
Ergänzen, dazu `DJANGO_VERTRAUE_PROXY='False'` aufnehmen.

---

## 7. Reihenfolge des Baus

1. `config/settings_test.py` und das Makefile-Ziel — danach müssen alle 522 Tests mit
   `DJANGO_DEBUG=False` grün sein. **Das ist der Riegel für alles Weitere.**
2. Datenbank-Cache
3. Absender-Adresse hinter dem Proxy, samt Zeugen
4. Härtung anpassen, `STATIC_ROOT`
5. Kleinigkeiten aus Abschnitt 6

Nach jedem Schritt `make test`.

---

## 8. Gegenprobe zum Abschluss

Wie in den vorangegangenen Runden sabotieren und prüfen, ob ein Zeuge fällt. Besonders
zu prüfen:

- Den **ersten** statt den letzten Eintrag aus `X-Forwarded-For` nehmen — fällt ein Zeuge?
- Die Prüfung auf `REMOTE_ADDR == 127.0.0.1` entfernen — fällt ein Zeuge?
- `VERTRAUE_PROXY` fest auf `True` verdrahten — fällt der alte Zeuge, der besagt, dass
  der Kopf ohne Proxy ignoriert wird?
- Den Cache auf `LocMemCache` zurückdrehen — fällt etwas, oder ist die Zusage unbewacht?
  Wenn unbewacht: **so berichten, nicht künstlich einen Zeugen erfinden.** Prozesslokalität
  ist mit dem Testclient nicht erreichbar; das ist ein ehrlicher Befund.

---

## Zurückmelden

- Jede Abweichung von dieser Spezifikation, mit Begründung
- Jede Stelle, an der die Spezifikation etwas offenlässt und eine Entscheidung nötig war
- Vollständige Fehlermeldungen mit Traceback
- Den Block ab `if not DEBUG:` aus `config/settings.py` nach der Änderung
- Den `CACHES`-Block
- Die Funktion, die die Absender-Adresse ermittelt
- Testzahl vorher und nachher, Laufzeit vorher und nachher, Zahl der Sabotagen, Zahl der
  blinden Zusagen

Nicht zurückmelden: Templates, CSS, `urls.py`, Migrationsdateien.
