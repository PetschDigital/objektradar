# Bauspezifikation — Immowelt in die Portal-Auswahl

**Stand:** 07.09.2026
**Umfang:** Eine Bauunde. Portal `immowelt` aufnehmen, Pfadmuster für die
Inserats-ID, Datenmigration für den Bestand.

---

## Ausgangslage

`immowelt.de` fehlt in der Portal-Auswahl. Testeinwürfe laufen deshalb als
`sonstiges` ohne Dublettenschlüssel; für sie greift nur der schwächere
URL-Vergleich.

Die Portal-Auswahl umfasst bisher: `idealista`, `fotocasa`, `milanuncios`,
`pisos`, `immoscout24`, `sonstiges`.

## Belegte URLs

Beide am 07.09.2026 aus Safari übernommen, echte Bestandsinserate:

```
https://www.immowelt.de/expose/715825d1-36ac-4c8c-99a0-10312f8c7de6
https://www.immowelt.de/expose/ecd16e27-fa20-49ce-a28f-57ee676c9eec
```

Struktur in beiden Fällen: `/expose/<kennung>`. Kein Sprachpräfix, kein
Zahlenblock im Text, die Kennung steht als einziges Segment hinter `/expose/`.

## Was gebaut wird

### 1. Portalwert

Neuer Wert `immowelt` in der Portal-Auswahl, eingeordnet zu den übrigen
Portalen. Beschriftung „Immowelt". `sonstiges` bleibt der letzte Eintrag.

Die Änderung der Auswahlliste erzeugt eine Migration (`AlterField`). Die ist
zu erzeugen und anzuwenden.

### 2. Domainzuordnung

Erkannt wird `immowelt.de`, mit und ohne `www.`.

**Ausdrücklich nicht aufgenommen:** `immowelt.at`, `immowelt.ch`,
`immonet.de`. Für keine dieser Domains liegt eine belegte Inserats-URL vor.
Ein Domaineintrag ohne belegtes Pfadmuster täuscht Abdeckung vor — dafür sind
am 03.09. `idealista.it` und `.pt` entfernt worden.

### 3. Pfadmuster für die Inserats-ID

Die Inserats-ID ist das **erste Pfadsegment hinter `/expose/`**.

Regeln:

- Query-Parameter und Fragment werden ignoriert.
- Ein abschließender Schrägstrich wird ignoriert.
- Weitere Pfadsegmente hinter der Kennung werden ignoriert, falls es sie gibt.
- Die Kennung wird **auf Kleinschreibung normalisiert**, bevor sie gespeichert
  wird.
- Ist das Segment leer, gilt kein Muster: Portal `sonstiges`, kein Schlüssel.

**Begründung Kleinschreibung:** Dieselbe Kennung in abweichender
Groß-/Kleinschreibung ergäbe zwei verschiedene Schlüssel für dasselbe
Inserat. Der Dublettenschutz fiele lautlos aus — dieselbe Fehlerart, gegen
die bei `pisos.com` der ganze Zahlenblock genommen wurde.

**Begründung für das weite Muster:** Es wird nicht auf UUID-Format geprüft.
Führt Immowelt ältere Inserate mit anderem Kennungsformat, fielen die bei
einem strengen Muster als `sonstiges` durch — das erzeugt eine sichtbare
Dublette. Ein zu breites Muster erzeugte dagegen eine stille Kollision, und
die ist hier ausgeschlossen: Das Segment hinter `/expose/` ist per Definition
die Inseratskennung, nicht die Kennung eines Anbieters.

### 4. Datenmigration für den Bestand

Alle Objekte, deren URL auf das Immowelt-Muster passt und die derzeit
`sonstiges` ohne Schlüssel tragen, bekommen Portal und Inserats-ID
nachgetragen.

**Kollisionsregel:** Tragen zwei Objekte dieselbe Kennung, bekommt das
**ältere** den Schlüssel; das jüngere bleibt ohne. Ohne festgelegte
Reihenfolge greift `Meta.ordering` absteigend, und jeder künftige Einwurf
liefe auf das jüngere Objekt, während Vota und Notizen am älteren hängen.
Entscheidung vom 29.08.2026, gilt unverändert.

Objekte, die bereits ein anderes Portal oder einen Schlüssel tragen, werden
nicht angefasst.

## Was nicht gebaut wird

- Kein Auslesen von Immowelt-Seiten. Titel, Preis, Wohnfläche und Zimmerzahl
  kommen weiterhin über das bestehende Lesezeichen aus den Open-Graph-Angaben
  und den Textmustern. An dieser Runde ändert sich daran nichts.
- Keine neuen Felder, keine Änderung am Lesezeichen-Skript.
- Keine Änderung an der Warnung bei unbekannter Domain. Sie entfällt für
  Immowelt künftig von selbst, weil die Domain jetzt bekannt ist.

## Zeugen

Für jede Zusage ein Zeuge. Es gilt durchgehend:

- Zeugen prüfen **Elemente und Klassenlisten**, nie `class="…"`-Zeichenketten.
- Ein Zeuge, der die Testumgebung misst statt die Zusage, gilt als blind.
- Die Datenform der Testdaten muss den geprüften Codepfad tatsächlich
  erzwingen.

Zu bewachen sind mindestens:

1. Beide belegten URLs ergeben Portal `immowelt` und die erwartete Kennung.
2. Groß geschriebene Kennung und klein geschriebene Kennung derselben URL
   ergeben **denselben** gespeicherten Schlüssel.
3. URL mit Query-Parameter und URL mit abschließendem Schrägstrich ergeben
   denselben Schlüssel wie die nackte Form.
4. `https://www.immowelt.de/` ohne `/expose/` ergibt **kein** Portal
   `immowelt` mit Schlüssel.
5. `immowelt.at` wird **nicht** als `immowelt` erkannt.
6. Der partielle Unique-Index greift: zwei Einwürfe derselben Immowelt-URL
   erzeugen kein zweites Objekt.
7. Die Datenmigration trägt bei einem Bestandsobjekt Portal und Kennung nach.
8. Die Kollisionsregel: bei zwei Bestandsobjekten auf derselben Kennung
   bekommt das ältere den Schlüssel. Der Zeuge muss fehlschlagen, wenn die
   Reihenfolge umgedreht wird.

## Gegenprobe

Am Ende der Runde die übliche Sabotage: jede Zusage einzeln brechen und
prüfen, ob ein Zeuge fällt. Blinde Zusagen werden gemeldet und mit einem
Zeugen geschlossen, bevor die Runde als abgeschlossen gilt.

Besonders zu prüfen ist Zeuge 2 (Kleinschreibung) und Zeuge 8
(Kollisionsregel) — beide bewachen einen Fall, der in den Testdaten leicht
strukturell erfüllt sein kann, ohne dass der Code ihn je durchläuft.

## Vor dem Bau melden

Findet sich die Portalerkennung nicht dort, wo diese Spezifikation sie
vermutet, oder weicht die Struktur der bestehenden Muster ab: **halten und
berichten**, nicht auf Annahmen weiterbauen.

Ebenso melden, falls im Bestand kein Objekt mit Immowelt-URL liegt — dann
entfällt die Datenmigration, und das gehört berichtet statt stillschweigend
übergangen.

## Nach dem Bau berichten

- Testzahl vorher und nachher
- Erzeugte Migrationen
- Jede Abweichung von dieser Spezifikation
- Jede Entscheidung, die die Spezifikation offengelassen hat
- Ergebnis der Gegenprobe, mit Zahl der Sabotagen und gefundenen blinden
  Zusagen
