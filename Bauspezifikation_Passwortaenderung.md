# Bauspezifikation · Passwort ändern

> Für Claude Code. Aufsetzend auf den Stand nach Schritt 6 (527 Tests grün).
> Stand: 02.09.2026

---

## 0. Geltung — zuerst lesen

### Worum es geht

Fünf Konten sind angelegt, das Werkzeug läuft öffentlich erreichbar unter
`objektradar.petsch-digital.com`. Vier Personen haben ein von Hand gesetztes
Startpasswort und **keine Möglichkeit, es zu ändern**. Diese Runde baut genau das.

Eine kleine Runde. Keine neuen Abhängigkeiten, keine Migration, kein Modellfeld.

### Was ausdrücklich nicht gebaut wird

- **Kein Passwort-Reset per Mail.** Es gibt keinen ausgehenden Mailversand. Wer sein
  Passwort vergisst, bekommt von Steffen über `manage.py changepassword` ein neues.
- **Kein Zwang zur Änderung beim ersten Login.** Das wäre eine Zustandsmarkierung am
  Konto, eine Middleware und ein Umleitungspfad quer durch alle Views. Bei fünf Personen,
  die ihr Passwort einzeln überreicht bekommen, steht der Aufwand nicht dafür.
- **Keine Registrierung, keine Selbstanlage.** Konten legt Steffen an.
- **Kein Profilbereich.** Es gibt genau eine neue Seite.

### Was bereits gilt und nicht geändert wird

- Login-Template und Zugangs-URLs liegen in `konten`. Die neue Seite gehört ebenfalls
  dorthin.
- `LoginRequiredMiddleware` schützt alle Views ohne Decorator.
- Das Login-Rate-Limit bleibt unberührt. Die Passwortänderung liegt hinter der Anmeldung
  und braucht keins.
- `SESSION_COOKIE_AGE` steht auf einem Jahr und bleibt so.

---

## 1. Die Seite

`django.contrib.auth.views.PasswordChangeView` mit
`django.contrib.auth.views.PasswordChangeDoneView`.

| Name | Pfad |
|---|---|
| `passwort_aendern` | `/passwort/` |
| `passwort_geaendert` | `/passwort/geaendert/` |

Templates:

```
templates/registration/password_change_form.html
templates/registration/password_change_done.html
```

Beide erben von `basis.html` und folgen dem bestehenden Stylesheet. Formularfehler
bekommen die Fehlerfarbe, nicht `--signal` — die bleibt der Preissenkung vorbehalten.

Nach erfolgreicher Änderung führt der Weg auf `passwort_geaendert`. Dort steht eine
Bestätigung und ein Verweis zurück auf die Liste.

### Die Sitzung darf nicht abreißen

`PasswordChangeView` ruft `update_session_auth_hash()` selbst auf; wer sein Passwort
ändert, bleibt angemeldet. Das ist so gewollt und wird nicht umgebaut.

**Sitzungen auf anderen Geräten laufen dabei aus.** Das ist die richtige Wirkung und der
halbe Zweck der Sache: Wer sein Startpasswort ändert, soll damit auch alles beenden, was
mit dem alten offen war.

---

## 2. Der Weg dorthin

In `basis.html`, im Kopf neben dem Abmelden-Formular: ein Verweis auf
`passwort_aendern`, beschriftet **„Passwort ändern"**.

Er steht nur für angemeldete Personen da — auf der Anmeldeseite erscheint er nicht.

---

## 3. Passwortregeln

`AUTH_PASSWORD_VALIDATORS` prüfen, ob sie in `config/settings.py` aktiv sind.

- **Stehen sie dort:** unverändert lassen. Sie greifen im Änderungsformular automatisch.
- **Fehlen sie:** Djangos vier Standardprüfungen eintragen (Ähnlichkeit zum
  Benutzernamen, Mindestlänge, häufige Passwörter, rein numerisch) und **melden**, dass
  sie gefehlt haben.

Keine eigenen Regeln erfinden.

---

## 4. `make passwort`

Neues Makefile-Ziel für den Fall, dass jemand sein Passwort vergisst:

```
passwort:
	.venv/bin/python manage.py changepassword $(BENUTZER)
```

Aufruf: `make passwort BENUTZER=Nico`

`changepassword` bringt Django mit und fragt verdeckt ab — das Passwort landet damit
nicht in der Shell-Historie. Deshalb kein eigener Befehl und kein Passwort als Argument.

---

## 5. Tests

**Zugang**
- Die Seite ohne Anmeldung leitet auf `login`
- Angemeldet ist sie mit 200 erreichbar

**Ändern**
- Richtiges altes Passwort und zwei gleiche neue: Das Passwort ist danach geändert
  (`check_password` gegen das neue ist wahr, gegen das alte falsch)
- Die Person bleibt danach angemeldet — `_auth_user_id` steht noch in der Sitzung
- Der Weg führt auf `passwort_geaendert`
- **Falsches altes Passwort ändert nichts** und erscheint als Formularfehler, nicht als
  Serverfehler
- **Zwei verschiedene neue Passwörter ändern nichts** und erscheinen als Formularfehler
- Ein Passwort, das an den Prüfregeln scheitert (etwa `12345678`), ändert nichts

**Verweis**
- Der Verweis auf `/passwort/` steht auf der Liste
- Auf der Anmeldeseite steht er **nicht**

**Fremde Sitzung**
- Eine zweite, mit demselben Konto angemeldete Sitzung ist nach der Änderung nicht mehr
  angemeldet. Das ist die eigentliche Sicherheitszusage und braucht einen eigenen Zeugen.

---

## 6. Gegenprobe

Wie in den vorangegangenen Runden sabotieren und prüfen, ob ein Zeuge fällt. Besonders:

- `update_session_auth_hash()` entfernen — fällt der Zeuge „bleibt angemeldet"?
- Die Prüfung des alten Passworts aushebeln — fällt ein Zeuge?
- Den Verweis aus `basis.html` nehmen — fällt ein Zeuge?
- Die Passwortprüfregeln abschalten — fällt der Zeuge auf `12345678`?

Blinde Zusagen vor Abschluss der Runde schließen.

---

## Zurückmelden

- Jede Abweichung von dieser Spezifikation, mit Begründung
- Jede Stelle, an der die Spezifikation etwas offenlässt und eine Entscheidung nötig war
- Vollständige Fehlermeldungen mit Traceback
- Ob `AUTH_PASSWORD_VALIDATORS` vorhanden waren oder ergänzt werden mussten
- Testzahl vorher und nachher, Zahl der Sabotagen, Zahl der blinden Zusagen

Nicht zurückmelden: Templates, CSS, `urls.py`.
