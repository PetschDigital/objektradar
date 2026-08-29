# Bauspezifikation · Oberfläche Schritt 1

> Für Claude Code. Aufsetzend auf das bestehende Datenmodell (79 Tests grün).
> Stand: 28.08.2026

---

## Ziel dieser Scheibe

Das Werkzeug wird ohne Django-Admin bedienbar: anmelden, Objekte einwerfen, Liste
filtern und sortieren, Objekt ansehen und ergänzen, Votum abgeben, Notizen schreiben.

Nach dieser Scheibe ist das Werkzeug **noch nicht** für die Gruppe freigegeben. Die
Freigabe erfolgt erst nach Schritt 2 (URL-Auslesen), weil Handeingabe das
Hauptkriterium „Erfassen dauert Sekunden" verletzt.

## Was ausdrücklich NICHT gebaut wird

- Kein Abrufen und Auslesen von Inseratsseiten (das ist Schritt 2)
- Kein Mail-Postfach, kein Parser (Schritt 3)
- Keine Markierung von Preissenkungen in der Liste (Schritt 4 — in Schritt 1 hat jedes
  Objekt höchstens einen Verlaufseintrag, es gäbe nichts zu markieren)
- Kein Login-Rate-Limit (bewusst zurückgestellt bis zum Hosting)
- Keine neuen Abhängigkeiten. Kein `django-filter`, kein CSS-Framework, kein
  JS-Framework, kein Build-Schritt. Bestehende Abhängigkeiten bleiben unverändert.
- Keine Änderung an `objekte/models.py` außer der unter „QuerySet" genannten Ergänzung.
  Keine Änderung an `konten/models.py`, an `choices.py` und an bestehenden Migrationen.

Wenn eine Anforderung hier nur eine der bestehenden Modellmethoden neu implementieren
würde: nicht implementieren, die Methode benutzen.

---

## 1. Settings

Ergänzen in `config/settings.py`:

```
USE_THOUSAND_SEPARATOR = True
```

Middleware, in dieser Reihenfolge nach `AuthenticationMiddleware`:

```
"django.contrib.auth.middleware.AuthenticationMiddleware",
"django.contrib.auth.middleware.LoginRequiredMiddleware",
"konten.middleware.BesuchMiddleware",
"django.contrib.messages.middleware.MessageMiddleware",
```

`LoginRequiredMiddleware` (Django 5.1+) schützt alle Views ohne Decorator. Die Login-View
bekommt `@method_decorator(login_not_required, name="dispatch")`. Nebenwirkung, die so
gewollt ist: `/admin/login/` leitet auf den eigenen Login um. Es gibt fünf Konten und
keinen Grund für zwei Anmeldeseiten. Ein angemeldeter Superuser erreicht den Admin normal.

`LOGIN_URL`, `LOGIN_REDIRECT_URL` und `LOGOUT_REDIRECT_URL` stehen bereits und verweisen
auf die URL-Namen `login` und `objektliste` — die Namen unten müssen exakt so heißen.

---

## 2. Besuchs-Middleware

Neu: `konten/middleware.py`, Klasse `BesuchMiddleware`.

Verhalten je Request:

1. Nichts tun, wenn `request.user` nicht authentifiziert ist.
2. Nichts tun, wenn der Pfad mit `settings.STATIC_URL` beginnt.
3. `request.user.besuch_registrieren()` aufrufen.
4. Danach `request.neu_seit = request.user.neu_seit` setzen.

Die Reihenfolge ist wesentlich: `besuch_registrieren()` rollt die Schwelle nur dann
weiter, wenn zwischen zwei Aufrufen mindestens `BESUCHSPAUSE` lag. Erst danach gelesen,
ist `neu_seit` für die gesamte Sitzung stabil.

`request.neu_seit` ist `None` beim allerersten Besuch eines Kontos. **Dann wird nichts
markiert** — nicht alles. Eine Liste, in der jede Zeile markiert ist, trägt keine
Information.

---

## 3. QuerySet-Ergänzung

Neu in `ObjektQuerySet` (`objekte/models.py`), zusätzlich zu `mit_qm_preis()` und
`sichtbar()`:

```python
def mit_aktivitaet(self):
```

Annotiert `letzte_aktivitaet` als `Greatest` aus drei Werten:

- `zuletzt_geaendert_am` (Feld, nie NULL)
- Subquery: `geaendert_am` des jüngsten Votums zu diesem Objekt
- Subquery: `erstellt_am` der jüngsten Notiz zu diesem Objekt

Begründung: Votum und Notiz fassen das Objekt nicht an, `zuletzt_geaendert_am` bleibt bei
einem neuen Votum also stehen. Preisänderung und Statuswechsel laufen dagegen über
`objekt.save()` und sind bereits enthalten.

**Subqueries, keine Aggregate.** Aggregate über zwei verschiedene Relationen erzeugen ein
Kreuzprodukt und liefern falsche Zahlen. Das betrifft auch die Votum-Zählung unten: die
darf mit Aggregaten arbeiten, weil sie nur *eine* Relation anfasst — aber nicht gemeinsam
mit einem zweiten Aggregat über `notizen`.

`Greatest` überspringt auf PostgreSQL NULL-Werte und liefert den größten gesetzten Wert.
Auf anderen Backends verhielte sich das anders; das ist eine bewusste
Postgres-Abhängigkeit und gehört als Kommentar an die Methode.

---

## 4. URLs

Alle in `objekte/urls.py`, eingebunden in `config/urls.py` unter der Wurzel. `/admin/`
bleibt bestehen.

| Name | Pfad | Methode |
|---|---|---|
| `login` | `/anmelden/` | GET, POST |
| `logout` | `/abmelden/` | POST |
| `objektliste` | `/` | GET |
| `objekt_anlegen` | `/einwerfen/` | POST |
| `objekt` | `/objekt/<int:pk>/` | GET |
| `objekt_bearbeiten` | `/objekt/<int:pk>/bearbeiten/` | GET, POST |
| `votum_setzen` | `/objekt/<int:pk>/votum/` | POST |
| `status_setzen` | `/objekt/<int:pk>/status/` | POST |
| `notiz_anlegen` | `/objekt/<int:pk>/notiz/` | POST |

Alle POST-Views antworten mit Redirect, nie mit gerendertem HTML (Reload-Schutz). Bei
Fehlern: `messages.error()` und Redirect zurück.

---

## 5. Anmeldung

`django.contrib.auth.views.LoginView` mit `templates/registration/login.html`.
Abmelden über `LogoutView`, nur per POST (Django 5 erlaubt kein GET mehr) — im Kopf
also ein Formular mit Button, kein Link.

Kein Registrierungsweg, kein Passwort-Reset (es gibt keinen Mailversand). Konten werden
weiterhin über `make superuser` angelegt.

---

## 6. Schnellerfassung

Ein einzelnes URL-Feld, dauerhaft sichtbar über der Liste, mit einem Button „Einwerfen".
Kein zweites Feld, keine Aufklappmaske.

Ablauf in `objekt_anlegen`:

1. URL trimmen. Leer oder ungültig (`URLValidator`) → `messages.error`, zurück zur Liste.
2. Vorher-Blick auf Dubletten: Vergleich der eingegebenen URL gegen bestehende, jeweils
   ohne abschließenden Schrägstrich. Treffer → **nicht anlegen**, `messages.info`
   („Das Inserat liegt schon in der Liste"), Redirect auf das bestehende Objekt.
3. Sonst anlegen mit: `url` (im Original, unverändert), `quelle=Quelle.URL_EINGEWORFEN`,
   `eingestellt_von=request.user`, `zuletzt_geaendert_von=request.user`. Status und
   Zustand bleiben auf ihren Defaults.
4. `messages.success` mit Link „ergänzen" auf die Objektansicht, Redirect zur Liste.

Zu 2: Der partielle Unique-Index über `portal` + `inserats_id` greift in Schritt 1 nie,
weil beide Felder erst der Parser in Schritt 2 füllt. Dieser Vergleich ist ein bewusst
schwacher Ersatz — Sprachpräfix und Tracking-Parameter umgehen ihn. Er ist keine
Garantie und wird auch nicht als solche beschriftet.

Zu 4: Redirect zur Liste, nicht zur Objektansicht. In Schritt 1 kostet das einen Klick,
wenn man sofort ergänzen will. Ab Schritt 2 füllt der Parser die Felder, und dann ist
„mehrere hintereinander einwerfen" der Normalfall. Der Weg bleibt damit auch später richtig.

---

## 7. Objektliste

### Queryset

`Objekt.objects.mit_qm_preis().mit_aktivitaet()`, dazu `.sichtbar()`, solange der Filter
für ausgeblendete Status nicht gesetzt ist.

Votum-Zählung je Objekt als bedingte `Count`-Annotationen über `vota` (`dafuer`,
`anschauen`, `raus`). „offen" wird in der Ansicht gerechnet: Anzahl aktiver Personen
minus Summe der drei. Die Personenzahl ist eine Abfrage pro Seite, nicht pro Zeile.

`select_related("eingestellt_von")`. Keine Schleife über `objekt.vota` im Template.

### Spalten

Ort · Land · Wohnfläche · Grundstücksgröße · Kaufpreis · €/m² · Zustand · Status ·
Votum-Übersicht. Jede Zeile verlinkt auf die Objektansicht.

Zeigt ein Objekt keinen Titel und keinen Ort, steht die URL da (`__str__` liefert das
bereits).

### Markierung „seit deinem letzten Besuch"

Ein Punkt am Zeilenanfang, wenn `letzte_aktivitaet > request.neu_seit`. Beschriftung im
Titel-Attribut und als sichtbare Legende über der Liste: „seit deinem letzten Besuch".

**Das Wort „neu" wird hier nicht verwendet.** Es ist für `Status.NEU` reserviert, das
etwas anderes bedeutet („noch von niemandem angesehen") und in derselben Zeile steht.

Ist `request.neu_seit` `None`, wird nichts markiert.

### Filter

Als normales `forms.Form`, gelesen aus GET, damit ein gefilterter Stand teilbar ist:

Freitext (über `titel`, `ort`, `region`, `beschreibung`, `icontains`) · Land ·
Objekttyp · Zustand · Preis von/bis · Wohnfläche von/bis · Region (Freitext) ·
Kontrollkästchen „ausgeblendete anzeigen".

**Jeder Filter greift nur bei gesetztem Wert.** Land, Portal und Objekttyp sind
`blank=True, default=""`; in Schritt 1 ist praktisch jedes Objekt genau so angelegt. Ein
Filter, der leere Werte mitprüft, versteckt den kompletten Bestand.

Ist mindestens ein Filter aktiv, steht über der Liste: Trefferzahl, Gesamtzahl und ein
Link „Filter zurücksetzen".

### Sortierung

Über GET-Parameter `sortierung`. Zulässig: `eingestellt_am` (Standard, absteigend),
`aktueller_preis`, `qm_preis`, `wohnflaeche`, `letzte_aktivitaet`, je auf- und absteigend.
Unbekannter Wert fällt still auf den Standard zurück.

**Immer mit `nulls_last=True`.** `mit_qm_preis()` liefert für Objekte ohne Wohnfläche
korrekt NULL; absteigend sortiert schöbe PostgreSQL diese sonst nach vorn, und dann
stehen Grundstücke ohne Flächenangabe über allem.

### Blättern

`Paginator`, 50 je Seite. Filter- und Sortierparameter bleiben beim Blättern erhalten.

---

## 8. Objektansicht

Ein Template, vier voneinander getrennte Aktionen. Kein Inline-Edit.

**Kopf:** Titel oder Ort · Kaufpreis · €/m² · Status · Link zum Inserat
(`target="_blank" rel="noopener noreferrer"`).

**Daten:** alle gefüllten Felder. Leere Felder werden ausgelassen, nicht als „—" gezeigt.
Dazu ein Button „Bearbeiten".

**Bilder:** falls vorhanden, als `<img>` aus den gespeicherten URLs, nach `reihenfolge`.
`loading="lazy"`. Fällt eine URL aus, bleibt die Seite benutzbar.

**Votum:** Drei Buttons (dafür / anschauen / raus) und ein Begründungsfeld. Das eigene
Votum ist als gewählt erkennbar. Darunter die Vota der anderen mit Name, Wertung und
Begründung.

**Status:** Auswahlfeld mit allen Werten aus `Status` und ein Button. Darunter der
Verlauf aus `statusaenderungen` (wer, wann, von wo nach wo).

**Preisverlauf:** Tabelle aus `preise` (Datum, Preis, Quelle), jüngster oben. Nur Anzeige
— geändert wird der Preis im Bearbeiten-Formular.

**Notizen:** Textfeld mit Button, darunter alle Notizen chronologisch mit Name und Datum.

**Fußzeile:** Quelle · eingestellt von/am · zuletzt geändert von/am.

---

## 9. Bearbeiten-Formular

`ModelForm` auf `Objekt`. Felder:

`url`, `portal`, `inserats_id`, `titel`, `ort`, `land`, `region`, `objekttyp`,
`wohnflaeche`, `grundstuecksgroesse`, `zimmer`, `baujahr`, `beschreibung`, `zustand`,
`wert_nach_renovierung`.

**Nicht im Formular:** `aktueller_preis` (ist `editable=False` und fällt ohnehin heraus),
`status` (läuft über `status_setzen()`), `quelle`, `eingestellt_von`, `eingestellt_am`,
`zuletzt_geaendert_von`, `zuletzt_geaendert_am`, `zuletzt_gesehen`.

### Zusätzliches Feld `kaufpreis`

Ein `forms.DecimalField(required=False, min_value=0)`, **kein Modellfeld**. Vorbelegt mit
`instance.aktueller_preis`.

Hintergrund: `aktueller_preis` ist `editable=False`, und `Objekt.save()` legt einen ersten
Verlaufseintrag nur an, wenn beim Anlegen bereits ein Preis dransteht — was über ein
Formular nie passiert. Ohne dieses Feld ließe sich von Hand nie ein Preis erfassen.

Ablauf in `form_valid`, in dieser Reihenfolge:

1. `objekt = form.save(commit=False)`
2. `objekt.zuletzt_geaendert_von = request.user`
3. `objekt.save()` — liest den Preis dabei aus der Datenbank nach, wie vorgesehen
4. Nur wenn `kaufpreis` gesetzt **und** ungleich `objekt.aktueller_preis`:
   `objekt.preis_setzen(request.user, kaufpreis)`

Ein **leeres** Preisfeld bedeutet „nicht ändern", nicht „Preis löschen". Ein Preis lässt
sich über die Oberfläche nicht zurücknehmen — `Preisverlauf.preis` ist nicht nullbar, und
ein Verlauf, aus dem Einträge verschwinden, wäre kein Verlauf. Das gehört als Hinweistext
ans Feld.

### Constraint-Verletzung

Trägt jemand ein Portal und eine Inserats-ID ein, die es schon gibt, greift der partielle
Unique-Index. Django 5.2 prüft Constraints bereits im `ModelForm`, das muss also als
Formularfehler ankommen und nicht als 500er. Ein Test belegt das.

---

## 10. Votum, Status, Notiz

**`votum_setzen`:** `Votum.objects.update_or_create(objekt=…, person=request.user,
defaults={"wertung": …, "begruendung": …})`. Ungültige Wertung → `messages.error`,
Redirect. Kein zweites Votum derselben Person, das erzwingt bereits der Constraint.

**`status_setzen`:** ruft `objekt.status_setzen(request.user, neuer_status)`. Gibt die
Methode `None` zurück (Status unverändert), keine Erfolgsmeldung.

**`notiz_anlegen`:** `Notiz.objects.create(…)`. Leerer Text wird abgewiesen.

Keine dieser drei Aktionen schreibt `zuletzt_geaendert_am` am Objekt. Das ist so gewollt
und der Grund für `mit_aktivitaet()`.

---

## 11. Templates und Stylesheet

```
templates/basis.html
templates/registration/login.html
templates/objekte/objektliste.html
templates/objekte/objekt.html
templates/objekte/objekt_bearbeiten.html
static/objektradar.css
```

`basis.html` trägt Kopf mit Navigation, Abmelden-Formular und die Ausgabe von `messages`.

**Ein handgeschriebenes Stylesheet, mobil zuerst.** Das Einwerfen einer URL passiert
realistisch am Handy, nicht am Schreibtisch. Die Liste ist auf schmalen Schirmen deshalb
keine Tabelle, sondern je Objekt eine Karte mit den wichtigsten Zahlen; ab etwa 48rem
Breite eine Tabelle. Das URL-Feld der Schnellerfassung ist auf dem Handy in Daumenreichweite.

Kein JavaScript außer, falls nötig, wenigen Zeilen für das Aufklappen des Filterblocks.
Ohne JavaScript muss alles funktionieren.

### Zahlenformat

Mit `USE_THOUSAND_SEPARATOR = True` erscheinen Preise als `285.000,00 €`. In Formularen
kollidiert das mit `<input type="number">` — der Browser räumt einen lokalisierten Wert
kommentarlos leer. Deshalb bekommen alle Dezimalfelder in Formularen `localize=True` und
ein `TextInput` mit `inputmode="decimal"` statt `NumberInput`. Ein Test deckt den
Rundlauf ab: Wert speichern, Formular erneut laden, unverändert absenden, Wert steht noch.

---

## 12. Tests

Die bestehenden 79 Tests bleiben unverändert und grün. Neu, mindestens:

**Zugang**
- Unangemeldeter Aufruf der Liste leitet auf `login`
- Nach Anmeldung führt der Redirect auf `objektliste`

**Schnellerfassung**
- Legt an mit `Quelle.URL_EINGEWORFEN` und `eingestellt_von = angemeldete Person`
- Dieselbe URL ein zweites Mal legt **kein** zweites Objekt an und leitet auf das
  bestehende
- Dieselbe URL mit abschließendem Schrägstrich wird als Dublette erkannt
- Ungültige Eingabe legt nichts an

**Bearbeiten**
- Preisänderung erzeugt genau einen neuen Verlaufseintrag und aktualisiert
  `aktueller_preis`
- Speichern ohne Preisänderung erzeugt **keinen** Verlaufseintrag
- Leeres Preisfeld lässt einen bestehenden Preis unangetastet
- `zuletzt_geaendert_von` wird gesetzt
- Kollidierendes Paar aus Portal und Inserats-ID erscheint als Formularfehler, nicht als
  Serverfehler
- Rundlauf des Zahlenformats (siehe 11)

**Liste**
- Objekte mit `Status.RAUS` und `Status.VOM_MARKT` fehlen im Standard, erscheinen mit
  gesetztem Kontrollkästchen
- Absteigende Sortierung nach `qm_preis` stellt Objekte ohne Wohnfläche ans Ende
- Ein Filter mit leerem Wert schränkt nicht ein
- Ein Filter auf `Land.ES` verbirgt Objekte ohne Land — und der Test hält fest, dass
  das gewollt ist
- `assertNumQueries`: Die Zahl der Abfragen ist bei 5 und bei 50 Objekten gleich

**Aktivität und Besuch**
- Ein Objekt, das nur ein neues Votum bekommen hat, gilt als aktiv, obwohl
  `zuletzt_geaendert_am` älter als die Schwelle ist. Gleiches für eine neue Notiz
- Erster Aufruf eines frischen Kontos markiert nichts
- Zweiter Aufruf zehn Minuten später verschiebt die Schwelle nicht
- Aufruf nach 31 Minuten verschiebt sie

**Votum**
- Zweites Votum derselben Person ersetzt das erste, legt kein zweites an

---

## 13. Reihenfolge des Baus

1. Settings, Middleware, Login, `basis.html` — anmelden und eine leere Seite sehen
2. Liste ohne Filter und Sortierung, Schnellerfassung — einwerfen und wiederfinden
3. Objektansicht mit Bearbeiten-Formular
4. Votum, Status, Notiz
5. Filter, Sortierung, Blättern
6. Aktivitäts-Annotation und Markierung
7. Stylesheet

Nach jedem Schritt `make test`. Nach Schritt 2 ist das Werkzeug bereits ohne Admin
benutzbar; alles danach macht es brauchbar.

## Zurückmelden

- Jede Abweichung von dieser Spezifikation, mit Begründung
- Jede Stelle, an der die Spezifikation etwas offenlässt und eine Entscheidung nötig war
- Vollständige Fehlermeldungen mit Traceback
- Die Modelldatei, falls `mit_aktivitaet()` anders ausfällt als hier beschrieben
- Testzahl vorher und nachher

Nicht zurückmelden: Templates, CSS, `urls.py`, Migrationsdateien.
