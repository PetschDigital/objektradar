# Bauspezifikation · Punkt 5 — Filter, Sortierung, Blättern, Votum-Übersicht

> Für Claude Code. Aufsetzend auf den Stand nach Schritt 2 (429 Tests grün).
> Stand: 01.09.2026

---

## 0. Geltung — zuerst lesen

### Diese Datei löst ab

**Abschnitt 7 von `Bauspezifikation_Oberflaeche_Schritt1.md` ist für Filter, Sortierung
und Blättern hiermit abgelöst.** Wo diese Datei etwas anders regelt, gilt diese Datei.
Abschnitt 7 wird für diese Runde nicht mehr gelesen.

Dateien mit dem Namensteil `_ARCHIV_` sind erledigte Runden. Sie werden nicht gelesen und
nicht als Vorgabe behandelt.

### Bereits gebaut — nicht erneut melden

Die folgenden drei Punkte stehen in `Bauspezifikation_Oberflaeche_Schritt1.md` als „wird
nicht gebaut" oder anders geregelt. Sie sind seither gebaut beziehungsweise entschieden.
Ein Abgleich mit der alten Spezifikation erzeugt hier Fehlalarme — er wurde am 29.08.
bereits geführt und ist ausgewertet:

- **Das Login-Rate-Limit ist gebaut.** Fünf Fehlversuche je IP und Benutzername,
  15 Minuten Sperre, HTTP 429, über den Django-Cache.
- **Login-Template und Zugangs-URLs liegen in `konten`**, nicht in `objekte`.
- **Ein fehlendes Schema in der eingeworfenen URL wird um `https://` ergänzt.**

### Entschieden — wird nicht geändert

- Der Redirect nach dem Einwerfen geht auf die **Liste**, nicht auf die Objektansicht.
  Nach der Übernahme über das Lesezeichen geht er auf die **Objektansicht**.
- Objekte werden über ein Formular bearbeitet, kein Inline-Edit.
- Keine neuen Abhängigkeiten. Kein `django-filter`, kein CSS-Framework, kein
  JS-Framework, kein Build-Schritt.
- Keine Änderung an `konten/models.py`, an `choices.py` und an bestehenden Migrationen.
- Keine Änderung an bestehenden Modellmethoden. Wo eine Anforderung eine vorhandene
  Methode neu implementieren würde: nicht implementieren, die Methode benutzen.

### Nicht Teil dieser Runde

- **`mit_aktivitaet()` und die Markierung „seit deinem letzten Besuch"** (Punkt 6). Damit
  entfällt auch `letzte_aktivitaet` als Sortierschlüssel, obwohl die alte Spezifikation
  ihn nennt. Begründung: Der Schlüssel setzt die Annotation voraus; sie hier zur Hälfte zu
  bauen, verteilt Punkt 6 über zwei Runden.
- Markierung von Preissenkungen in der Liste (Schritt 4).
- Mail-Postfach und Parser (Schritt 3).

### Eine Annahme, die überprüft werden muss

Diese Spezifikation geht davon aus, dass `ObjektQuerySet.sichtbar()` **sowohl
`Status.RAUS` als auch `Status.VOM_MARKT`** ausblendet.

**Trifft das nicht zu: anhalten und melden.** Nicht zurechtlegen, nicht anpassen, nicht
`sichtbar()` ändern. Die Vorgabe unter Abschnitt 1 hängt daran, und eine stillschweigend
korrigierte Annahme fällt später niemandem mehr auf.

---

## 1. Statusfilter — die zentrale Regel

Der Status wird zu einem **Mehrfachauswahlfeld über alle sechs Werte** aus `Status`.
Er ersetzt das Kontrollkästchen „ausgeblendete anzeigen" aus der alten Spezifikation.

### Vorbelegung

Fehlt der Parameter `status` in der Adresse **vollständig**, gelten vier Werte:

```
NEU · IN_PRUEFUNG · BESICHTIGUNG · HEISSE_SPUR
```

`RAUS` und `VOM_MARKT` sind zuwählbar, nicht vorbelegt. Damit ist die Zusage aus
`02_Datenmodell.md` erfüllt: verworfene Objekte werden ausgeblendet, nicht gelöscht, und
sind über einen Filter wieder sichtbar.

### Steht der Parameter drin und ist leer

`?status=` liefert **null Treffer**. Das ist gewollt und ehrlich: der Filter sagt „zeige
Objekte mit keinem der folgenden Status". Es wird nicht auf die Vorbelegung
zurückgefallen — sonst ließe sich eine leere Auswahl gar nicht ausdrücken.

**Daraus folgt zwingend:** Jeder Blätter- und jeder Sortierlink muss die gesetzten
Statuswerte mittragen. Fällt `status` beim Sortieren aus der Adresse, greift die
Vorbelegung und die Auswahl des Benutzers ist stillschweigend weg. Siehe Abschnitt 4.

### Verhältnis zu `sichtbar()`

Die Listenansicht ruft `sichtbar()` **nicht mehr** auf. Der Statusfilter erledigt das
jetzt vollständig und ist die einzige Stelle, die entscheidet, welche Status erscheinen.
Zwei Mechanismen nebeneinander würden sich gegenseitig verdecken.

`sichtbar()` bleibt im Modell **unverändert stehen**. Andere Aufrufer bleiben unberührt.

---

## 2. Filterformular

Ein normales `forms.Form`, gelesen aus GET, damit ein gefilterter Stand teilbar ist.
Kein `ModelForm`, kein `django-filter`.

| Parameter | Typ | Wirkt auf |
|---|---|---|
| `suche` | Text | `titel`, `ort`, `region`, `beschreibung` — `icontains`, ODER-verknüpft |
| `status` | Mehrfachauswahl | `status` — siehe Abschnitt 1 |
| `land` | Auswahl | `land` |
| `portal` | Auswahl | `portal` |
| `objekttyp` | Auswahl | `objekttyp` |
| `zustand` | Auswahl | `zustand` |
| `preis_von` | Dezimal | `aktueller_preis__gte` |
| `preis_bis` | Dezimal | `aktueller_preis__lte` |
| `flaeche_von` | Dezimal | `wohnflaeche__gte` |
| `flaeche_bis` | Dezimal | `wohnflaeche__lte` |
| `region` | Text | `region__icontains` |

Alle Felder `required=False`.

`portal` ist gegenüber der alten Spezifikation **neu**. Grund: Seit Schritt 2 wird
`portal` aus der URL abgeleitet und ist damit erstmals gefüllt. Vorher stand dort nichts,
und ein Filter auf ein leeres Feld ist wertlos.

### Jeder Filter greift nur bei gesetztem Wert

`land`, `portal`, `objekttyp` sind `blank=True, default=""`. Ein Filter, der den leeren
Wert mitprüft, verbirgt den kompletten Bestand. Ein nicht gesetzter Filter darf die
Abfrage nicht anfassen — auch nicht mit einem `Q()`, das „alles" bedeutet.

**Gewollte Nebenwirkung, die festgehalten wird:** Ein gesetzter Filter auf `land=ES`
verbirgt Objekte **ohne** Land. Das ist richtig so und wird durch einen Test belegt,
damit es später niemand für einen Fehler hält.

### Trefferanzeige

Ist mindestens ein Parameter außer `sortierung` und `seite` gesetzt, steht über der
Liste: Trefferzahl, Gesamtzahl und ein Link „Filter zurücksetzen". Der Link zeigt auf
die nackte Listen-URL ohne jeden Parameter.

Die Gesamtzahl ist die Zahl aller Objekte ohne jeden Filter — nicht die Zahl der
sichtbaren.

---

## 3. Sortierung

Ein GET-Parameter `sortierung`. Führendes Minus bedeutet absteigend:
`?sortierung=-qm_preis`.

Zulässig sind genau vier Schlüssel, jeweils auf- und absteigend:

```
eingestellt_am · aktueller_preis · qm_preis · wohnflaeche
```

Standard ist `-eingestellt_am`. **Ein unbekannter oder ungültiger Wert fällt still auf den
Standard zurück** — keine Fehlermeldung, kein 500er. Die Prüfung läuft gegen eine
Positivliste, niemals durch Durchreichen des Parameters an `order_by()`.

### Immer `nulls_last=True`

Ausnahmslos, in beide Richtungen. `mit_qm_preis()` liefert für Objekte ohne Wohnfläche
korrekt NULL; absteigend sortiert schöbe PostgreSQL diese sonst nach vorn, und dann
stehen Grundstücke ohne Flächenangabe über allem. `aktueller_preis` und `wohnflaeche`
sind ebenfalls nullbar.

Ein einheitlicher Codepfad, keine Sonderfälle je Schlüssel.

### Jede Sortierung endet auf `-id`

**Das ist keine Kosmetik.** Ohne zweites Kriterium ist die Reihenfolge bei gleichen Werten
unbestimmt. In Verbindung mit dem Paginator heißt das: PostgreSQL darf bei zwei Abfragen
verschieden sortieren, und dann erscheint ein Objekt auf Seite 1 **und** auf Seite 2 —
oder auf keiner.

Der Fall tritt sofort ein, nicht theoretisch: Drei Objekte ohne Wohnfläche haben alle
`qm_preis = NULL`. Beim Einwerfen mehrerer Objekte in einer Minute ist auch
`eingestellt_am` gleich.

`Meta.ordering` am Objekt trägt `-eingestellt_am, -id` bereits. Ein ausdrückliches
`order_by()` ersetzt `Meta.ordering` vollständig — der Zusatz muss deshalb hier erneut
gesetzt werden und darf nicht als „steht ja schon im Modell" weggelassen werden.

---

## 4. Blättern

`Paginator`. Die Seitengröße steht als **Modulkonstante**, nicht als Zahl im Code:

```python
OBJEKTE_JE_SEITE = 50
```

Grund: Der Zeuge für die Sortierstabilität (Abschnitt 7) braucht kleine Seiten. Eine fest
verdrahtete 50 macht ihn unbaubar.

Parameter ist `seite`. Eine ungültige oder außerhalb liegende Seitenzahl fällt still auf
Seite 1 zurück — gleiche Haltung wie bei der Sortierung.

### Parametererhalt

Filter- und Sortierparameter bleiben beim Blättern erhalten, Blätterparameter bleiben
beim Sortieren erhalten. Dafür wird der in **Django 5.1 eingebaute Template-Tag
`{% querystring %}`** benutzt — keine eigene Hilfsfunktion, kein eigener Template-Tag,
keine Abhängigkeit.

Beispiel: `{% querystring seite=seite_obj.next_page_number %}` übernimmt die übrigen
Parameter unverändert und tauscht nur `seite` aus. Beim Sortierlink umgekehrt:
`sortierung` setzen, `seite` auf `None` setzen, damit die Seitenzahl fällt.

Ein Mehrfachparameter wie `status` wird dabei vollständig mitgeführt — genau das ist der
Grund, es nicht von Hand zu bauen.

---

## 5. Votum-Übersicht als Listenspalte

Die Spalte fehlt bisher, obwohl `02_Datenmodell.md` sie als Listenfeld führt. Sie wird
hier nachgezogen.

### Zählung

Drei bedingte `Count`-Annotationen über `vota`:

```python
Count("vota", filter=Q(vota__wertung=Wertung.DAFUER))
Count("vota", filter=Q(vota__wertung=Wertung.ANSCHAUEN))
Count("vota", filter=Q(vota__wertung=Wertung.RAUS))
```

**Das ist zulässig, weil alle drei dieselbe Relation anfassen.** Ein zweites Aggregat über
eine andere Relation — etwa `notizen` — erzeugt ein Kreuzprodukt und liefert falsche
Zahlen. Kommt hier nicht vor und darf auch nicht dazukommen.

### „offen"

Anzahl aktiver Personen (`is_active=True`) minus Summe der drei Zählungen.

**Eine Abfrage je Seite, nicht je Zeile.** Die Personenzahl wird einmal in der View
ermittelt und in den Kontext gelegt. Keine Schleife über `objekt.vota` im Template, kein
`.count()` in der Zeile.

### Darstellung

Beispiel: `3 dafür · 1 raus · 1 offen`

Kategorien mit dem Wert 0 werden **weggelassen**. Sonst steht in jeder Zeile „0 raus" und
die Spalte trägt keine Information mehr.

Hat niemand abgestimmt, steht dort `noch kein Votum` — nicht „5 offen", und nicht leer.

---

## 6. Abfragezahl

`select_related("eingestellt_von")` bleibt.

Die Zahl der Abfragen muss bei 5 und bei 50 Objekten **gleich** sein. Der bestehende
`assertNumQueries`-Zeuge vergleicht 1 gegen 7 Objekte; das ist zu wenig, um ein
N+1-Problem sichtbar zu machen.

**Dieser Zeuge wird in dieser Runde auf 5 gegen 50 gezogen.** Punkt 5 ist die Runde, in
der es zählt: Paginator, drei Aggregate über `vota` und die Personenzahl kommen
gleichzeitig dazu.

---

## 7. Tests

Bestehende Tests bleiben unverändert und grün. Neu, mindestens:

**Statusfilter**
- Ohne Parameter erscheinen weder `RAUS` noch `VOM_MARKT`
- Ohne Parameter erscheinen alle vier übrigen Status
- `?status=raus` zeigt ausschließlich `RAUS`
- `?status=raus&status=vom_markt` zeigt beide
- `?status=` (leer) liefert null Treffer — der Test hält ausdrücklich fest, dass das
  gewollt ist und kein Fehler
- Ein unbekannter Statuswert wird abgewiesen, ohne 500er

**Sortierung**
- Absteigend nach `qm_preis` stellt Objekte ohne Wohnfläche ans Ende
- **Aufsteigend** nach `qm_preis` stellt sie ebenfalls ans Ende
- Unbekannter Wert für `sortierung` fällt auf den Standard zurück, ohne Fehler
- Ein Wert, der wie ein Feldname aussieht, aber nicht auf der Positivliste steht
  (etwa `passwort`), wird nicht durchgereicht

**Sortierstabilität — der wichtigste neue Zeuge**
- `OBJEKTE_JE_SEITE` wird im Test auf 2 gesetzt. Fünf Objekte mit identischem
  Sortierwert anlegen, alle Seiten durchgehen, die IDs sammeln: Es müssen genau fünf
  verschiedene sein. Kein Objekt doppelt, keines fehlt.

**Filter**
- Ein Filter mit leerem Wert schränkt nicht ein
- `land=ES` verbirgt Objekte ohne Land — der Test hält fest, dass das gewollt ist
- Freitext trifft in `titel`, in `ort`, in `region` und in `beschreibung`
- Preis- und Flächengrenzen wirken je einzeln und gemeinsam
- Filter auf `portal` trifft

**Blättern**
- Ungültige Seitenzahl fällt auf Seite 1, ohne Fehler
- Ein Blätterlink trägt gesetzte Filter- und Sortierparameter mit
- Ein Sortierlink trägt gesetzte Statuswerte mit — **mehrfach gesetzte `status` bleiben
  vollständig erhalten**
- Ein Sortierlink setzt die Seitenzahl zurück

**Votum-Übersicht**
- Zählung stimmt bei gemischten Vota
- „offen" stimmt und berücksichtigt nur aktive Personen
- Eine auf `is_active=False` gesetzte Person zählt nicht mehr in „offen"
- Kategorie mit 0 erscheint nicht
- Objekt ohne jedes Votum zeigt „noch kein Votum"

**Abfragezahl**
- `assertNumQueries`: gleiche Zahl bei 5 und bei 50 Objekten, mit gesetztem Filter und
  gesetzter Sortierung

---

## 8. Reihenfolge des Baus

1. Filterformular und Statusfilter — Liste filterbar, Vorbelegung greift
2. Sortierung mit `nulls_last` und `-id`
3. Blättern mit `{% querystring %}`
4. Votum-Übersicht als Spalte
5. `assertNumQueries` auf 5 gegen 50 ziehen

Nach jedem Schritt `make test`.

---

## 9. Gegenprobe zum Abschluss

Wie in den vorangegangenen Runden: einzelne Zusagen gezielt sabotieren und prüfen, ob ein
Zeuge sie fängt. Besonders zu prüfen sind die Stellen, an denen zwei Mechanismen dasselbe
Ergebnis erzeugen können und der Zeuge deshalb den falschen bewacht:

- `-id` aus der Sortierung nehmen — fällt der Stabilitätszeuge?
- Die Vorbelegung des Statusfilters entfernen — oder fängt den Fall in Wahrheit noch ein
  übrig gebliebener `sichtbar()`-Aufruf ab?
- `nulls_last` in **einer** Richtung entfernen — fallen beide Zeugen oder nur einer?
- Die Personenzahl aus der Zeile statt aus dem Kontext holen — fällt `assertNumQueries`?

Blinde Zusagen werden vor Abschluss der Runde geschlossen.

---

## Zurückmelden

- Jede Abweichung von dieser Spezifikation, mit Begründung
- Jede Stelle, an der die Spezifikation etwas offenlässt und eine Entscheidung nötig war
- Vollständige Fehlermeldungen mit Traceback
- Die Modelldatei, falls an `ObjektQuerySet` etwas geändert wurde
- Testzahl vorher und nachher, Zahl der Sabotagen, Zahl der blinden Zusagen
- Den Befund zur Annahme über `sichtbar()` aus Abschnitt 0 — auch wenn sie zutrifft

Nicht zurückmelden: Templates, CSS, `urls.py`, Migrationsdateien.
