# Bauspezifikation · Immowelt-Titelregel, PLZ-Feld, Etikett „Ortsteil"

**Stand:** 2026-09-15 · Für Claude Code · Eine Runde, vier Änderungen

---

## Zweck

Bei Immowelt bleiben Ort und Ortsteil heute leer, obwohl beide im `og:title`
stehen. Dasselbe gilt für Postleitzahl und Objekttyp. Diese Runde liest die vier
Angaben aus dem Titel, legt ein Feld für die Postleitzahl an und ändert die
Beschriftung des Feldes `stadtteil` auf „Ortsteil".

---

## Was NICHT geändert wird

Diese Liste ist bindend. Jede Berührung dieser Punkte ist eine Abweichung und
gehört gemeldet, nicht ausgeführt.

- **Das Bookmarklet-Skript** (`objekte/lesezeichen.py`, Konstante `SKRIPT`).
  Es liefert den `og:title` bereits. Die gesamte Auswertung sitzt serverseitig.
  Eine Skriptänderung zwänge alle fünf Personen, das Lesezeichen neu zu setzen.
- **Die Idealista-Ortsregel.** Sie bleibt unverändert, auch wenn Teile ähnlich
  aussehen. Kein gemeinsamer Helfer mit Portalparameter, kein Umbau der
  bestehenden Funktion. Ein zweiter Aufrufer rechtfertigt keine Verallgemeinerung.
- **Der URL-Einwurf (Schnellerfassung).** Dort liegt kein Titel vor. Es bleibt
  beim Land aus der Domain.
- **Der Bestand.** Keine Datenmigration, die vorhandene Objekte nachträgt. Die
  Objekte sind Testdaten und werden vor dem Scharfstellen geleert.
- **Die Region.** Sie wird bei keinem Portal abgeleitet und bleibt Freitext,
  manuell.
- **Das Preislesen bei Immowelt.** Unverändert: genau eine €-Zahl im Titel,
  sonst kein Preis.

---

## Änderung 1 · Neues Feld `plz`

Am Modell `Objekt`:

- Name: `plz`
- Typ: Zeichenfeld, `max_length=10`, `blank=True`, leer erlaubt
- **Kein Zahlenfeld.** `01067` verlöre als Zahl die führende Null.
- `verbose_name`: `PLZ`

Anzeige:

- Im **Datenblock der Objektansicht**, als eigene Zeile direkt **vor** dem
  Ortsteil.
- Im **Bearbeiten-Formular**, ebenfalls vor dem Ortsteil.
- **Nicht** in der Übersichtsliste. Der Zahlenblock der Liste bleibt bei vier
  Angaben, die Unterzeile bei Ortsteil, Ort, Region, Land, Zustand.

Für den Datenblock gilt die bestehende Regel unverändert: leere Felder werden
angezeigt, nicht weggelassen.

---

## Änderung 2 · Etikett `stadtteil` → „Ortsteil"

**Der Spaltenname bleibt `stadtteil`.** Keine Umbenennung des Feldes, keine
Datenmigration, keine Anpassung von Filtern oder Freitextsuche.

Geändert wird allein die Beschriftung:

- Am Modellfeld `stadtteil`: `verbose_name="Ortsteil"`. Damit zieht das
  Formular-Label automatisch nach.
- Im Template der Objektansicht, wo die Zeilenbeschriftung des Datenblocks
  hartkodiert ist.
- An jeder weiteren Stelle, an der das Feld für Menschen beschriftet wird.

Grund: Das Feld ist als „Lage innerhalb der Gemeinde" definiert. Das trifft auf
einen Hamburger Stadtteil ebenso zu wie auf ein Dorf in einer Amtsgemeinde —
das Wort „Stadtteil" trifft nur den ersten Fall. Ein Feld, das bei Dörfern falsch
beschriftet ist, bleibt leer, und genau diese Angabe erklärt laut `02` den
Preisunterschied.

**Erwartete Nebenwirkung:** Bestehende Zeugen, die auf die Zeichenkette
„Stadtteil" prüfen, werden rot. Das ist das gewünschte Ergebnis und **kein**
Fehler. Diese Zeugen werden auf „Ortsteil" umgestellt, nicht das Etikett
zurückgedreht.

---

## Änderung 3 · Ort, Ortsteil, PLZ und Objekttyp aus dem Immowelt-Titel

### Ort der Änderung

An derselben Stelle wie die Idealista-Ortsregel, portalabhängig verzweigt. Die
Immowelt-Regel ist eine eigene Funktion; die Idealista-Funktion wird nicht
angefasst.

Sie greift **nur** bei Portal `immowelt` und **nur** beim Zulauf über das
Lesezeichen (Neuanlage und Ergänzung).

### Belegtes Titelformat

```
<Typ> [<Fläche> m²] <Preis> € zum Kauf <Adressteil> (<PLZ>)
```

Die Flächenangabe fehlt belegt bei mindestens einem Titel. Der Adressteil trägt
ein bis drei durch Komma getrennte Segmente, teils ohne Leerzeichen nach dem
Komma.

### Verfahren

1. **Marke suchen:** das **letzte** Vorkommen von `" zum Kauf "`. Fehlt sie,
   bleiben Ort, Ortsteil und PLZ leer — ohne Rückfall auf eine andere Quelle.
2. **PLZ:** Am Ende des Titels genau fünf Ziffern in runden Klammern,
   `(\d{5})` als Abschluss der Zeichenkette. Trifft das Muster nicht, bleibt die
   PLZ leer. Der Klammerausdruck wird für Schritt 3 abgeschnitten.
3. **Adressteil:** alles zwischen Marke und Klammerausdruck. An Kommata
   zerlegen, jedes Segment beidseitig trimmen, leere Segmente verwerfen.
4. **Zuordnung, von hinten gezählt:**
   - letztes Segment → `ort` (die Gemeinde)
   - vorletztes Segment → `stadtteil` (der Ortsteil)
   - alle weiteren Segmente davor werden **verworfen**
   - nur ein Segment: `ort` gefüllt, `stadtteil` leer
5. **Objekttyp:** die Zeichen des Titels bis zum ersten Leerzeichen. Der Wert
   wird **nur** übernommen, wenn er — Groß- und Kleinschreibung ignoriert —
   **wörtlich** einem der Auswahlwerte entspricht: Villa, Haus, Reihenhaus,
   Wohnung, Finca, Grundstück, sonstiges. Jede Abweichung lässt das Feld leer.
   **Keine Abbildungstabelle, kein Rückfall auf „sonstiges", kein Raten.**

### Riegel

- **Sind Ortsteil und Ort nach dem Trimmen identisch, bleibt der Ortsteil leer.**
  Derselbe Wert zweimal sagt nichts Zweites.
- **Ein rein numerisches vorletztes Segment wird verworfen**, der Ortsteil bleibt
  leer. Übernommen aus der Idealista-Regel.
- **Ein Wert über der Feldlänge lässt das jeweilige Feld leer**, statt zu kürzen.
- Fehlt die Marke, bleiben alle vier Angaben leer. Ein leeres Feld sieht man,
  ein falsches nicht.

---

## Änderung 4 · Migration

Eine Schemamigration für `plz` (neues Feld) und für den geänderten
`verbose_name` von `stadtteil`. Beides ohne Datenwirkung. **Keine
Datenmigration.**

---

## Belegmaterial — acht echte `og:title`

Diese acht Titel stammen aus dem Bestand vom 15.09. und sind die Belegbasis der
Regel. Sie gehören als Testfälle in die Zeugen.

| Titel | Ortsteil | Ort | PLZ | Objekttyp |
|---|---|---|---|---|
| `Haus 70 m² 435000 € zum Kauf Bergstedt,Hamburg (22395)` | Bergstedt | Hamburg | 22395 | Haus |
| `Haus 170 m² 1450000 € zum Kauf Volksdorf,Hamburg (22359)` | Volksdorf | Hamburg | 22359 | Haus |
| `Haus 2490000 € zum Kauf Berchtesgaden,Berchtesgaden (83471)` | *leer* | Berchtesgaden | 83471 | Haus |
| `Haus 124 m² 257000 € zum Kauf Roßfeld,Crailsheim (74564)` | Roßfeld | Crailsheim | 74564 | Haus |
| `Haus 118 m² 429000 € zum Kauf Heidingsfeld,Würzburg (97084)` | Heidingsfeld | Würzburg | 97084 | Haus |
| `Haus 120 m² 150000 € zum Kauf Niederelsungen,Wolfhagen (34466)` | Niederelsungen | Wolfhagen | 34466 | Haus |
| `Haus 115 m² 290000 € zum Kauf Neu Lüdershagen,Wendorf (18442)` | Neu Lüdershagen | Wendorf | 18442 | Haus |
| `Haus 90 m² 167500 € zum Kauf Rosenkreuzstr. 7,Neumagen,Neumagen-Dhron (54347)` | Neumagen | Neumagen-Dhron | 54347 | Haus |

**Drei dieser Titel tragen die Last der Regel:**

- **Bergstedt/Hamburg** und **Volksdorf/Hamburg** belegen, dass hinten die
  Gemeinde steht. Ohne sie wäre die Reihenfolge ungeklärt.
- **Berchtesgaden,Berchtesgaden** belegt den Gleichheitsfall und trägt zugleich
  **keine Flächenangabe** im Titel — die Regel darf sie nicht voraussetzen.
- **Rosenkreuzstr. 7,Neumagen,Neumagen-Dhron** belegt den Dreisegmentfall mit
  vorangestellter Straße und Hausnummer. Von vorn gezählt bräche die Regel hier.

Die PLZ des letzten Titels ist im Screenshot abgeschnitten gewesen und hier als
`54347` ergänzt; wenn der Wert im Bestand abweicht, gilt der Bestand — dann den
Testfall korrigieren und die Abweichung melden.

---

## Zeugen

Verlangt sind Zeugen für:

1. Jeden der acht Titel: Ortsteil, Ort, PLZ und Objekttyp am angelegten Objekt.
2. **Fehlende Marke** → alle vier Felder leer, kein Rückfall.
3. **Fehlende oder abweichende Klammer am Ende** → PLZ leer, Ort und Ortsteil
   trotzdem korrekt.
4. **Objekttyp, der keinem Auswahlwert entspricht** (z. B. `Einfamilienhaus …`)
   → Feld leer, nicht „sonstiges".
5. **Ein Segment allein** → Ort gefüllt, Ortsteil leer.
6. **Rein numerisches vorletztes Segment** → Ortsteil leer.
7. **PLZ mit führender Null** (z. B. `(01067)`) → als Zeichenkette erhalten.
8. **Idealista bleibt unberührt:** mindestens ein Zeuge, der die bestehende
   Idealista-Ableitung nach dieser Runde erneut prüft.
9. **Datenblock der Objektansicht:** PLZ- und Ortsteil-Zeile vorhanden, auch bei
   leerem Wert.

**Zur Form der Zeugen:** Nicht auf Zeichenketten wie `class="…"` prüfen und
nicht auf ein Wort, das an mehreren Stellen der Seite vorkommen kann. Das Etikett
„Ortsteil" steht nach dieser Runde im Datenblock **und** im Bearbeiten-Formular —
ein `assertContains` auf das bloße Wort ist blind. Auf das Element im Datenblock
prüfen.

---

## Was zurückgemeldet wird

- Die geänderte Modelldatei vollständig.
- Jede Abweichung von dieser Spezifikation, mit Begründung.
- Jede Entscheidung, die diese Spezifikation offenlässt.
- Fehlermeldungen komplett, mit Traceback.
- Testzahl vorher und nachher.

Nicht zurückmelden: Templates, CSS, `urls.py`, generierte Migrationsdateien.

**Nicht committen.** Steffen übernimmt Git.
