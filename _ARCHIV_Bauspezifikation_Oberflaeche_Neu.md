# Bauspezifikation · Oberfläche neu

> Zielzustand, keine Diffs. Eine Bauspezifikation je Runde. Stand: 04.09.2026

---

## Auftrag in einem Satz

Die Objektliste bekommt das Layout aus `Entwurf_Objektliste.html`, die Objektansicht übernimmt dessen Bausteine, und die Löschbestätigung hört auf, den Votum-Zählstand zu verraten.

**Der Entwurf liegt als Datei bei und ist maßgeblich.** Bei Widerspruch zwischen dieser Beschreibung und der Datei gilt die Datei. Sie ist eine statische Fassung mit erfundenen Daten — nicht die Datei einbauen, sondern das Layout in die bestehenden Vorlagen und das bestehende Stylesheet übertragen.

---

## Was bereits gilt und NICHT geändert wird

- **Kein JavaScript.** Der Filterblock klappt über `<details>`, nicht über ein Skript.
- **Die Besuchsmarkierung** aus Punkt 6 bleibt in Verhalten und Bedeutung unverändert. Sie sitzt im Entwurf als Punkt vor dem Titel.
- **Das verdeckte Votum** bleibt unverändert. Ohne eigenes Votum steht „Abstimmen" statt der Stimmen — der Zählstand darf weiterhin **nicht im HTML** stehen.
- **Alle Filter, Sortierungen und das Blättern** behalten ihr Verhalten, ihre Parameter und ihre Zusagen. Das ist eine Oberflächenrunde, keine Verhaltensrunde.
- **Mobil zuerst**, Umbruchpunkt 48rem.
- `konten/models.py` und `Person` werden nicht angefasst. Keine Migration erwartet.

---

## Zielzustand

### 1 · Liste

Die Tabelle entfällt. Jede Zeile ist ein Block aus Bild, Mitte und Zahlen, wie im Entwurf:

- **Ein Markup für beide Fassungen.** Kein getrenntes Karten- und Tabellen-Markup mehr.
- **Der Status** erscheint doppelt: als farbige Kante links an der Zeile und als Pille in der Unterzeile. Die Kante löst den offenen Punkt, dass fünf der sechs Farben zu dicht am Papierton liegen.
- **Ort, Region, Land, Zustand** stehen als Unterzeile beim Titel, nicht als eigene Spalten, und **entfallen einzeln, wenn sie leer sind**. Damit hält die Liste endlich die Zusage aus `02`: leere Felder zeigt die Objektansicht, die Liste nicht.
- **Der Kaufpreis ist die größte Zahl der Zeile**, €/m² steht daneben in normaler Größe. Wohnfläche und Grundstück links davon.
- **Fehlende Zahlen** stehen als Gedankenstrich in gedämpfter Farbe, nicht als Lücke — sonst rutschen die Spalten gegeneinander.
- **Kein Geldbetrag bricht um.** Weder zwischen Zahl und Währungszeichen noch innerhalb der Zahl.
- **Eine Preissenkung** steht unter dem Kaufpreis in `--signal`, mit Betrag und Datum.
- **Das Votum** steht als Punktreihe, eine je Person, plus der bisherige Text. Bei verdecktem Stand nur „Abstimmen" und ein kurzer Hinweis.
- **Objekte ohne Titel** zeigen die gekürzte URL in gedämpfter, nicht fetter Schrift — sie sind erkennbar unfertig.
- **Objekte ohne Bild** bekommen eine leere Fläche in Bildgröße, damit die Zeilen nicht springen.

### 2 · Filterblock

- Eingeklappt als `<details>`. **Aufgeklappt, wenn ein Filter abweichend von der Vorbelegung gesetzt ist** — sonst versteckt sich eine wirksame Einschränkung.
- Die Kopfzeile nennt kurz, was gerade gilt.
- Der Status wird zu einer Reihe von Marken, nicht zu einer Kästchenliste. Angehakte Marken sind farblich abgesetzt.
- Beschriftung über dem Feld, wie bisher.

### 3 · Sortierleiste

Ein Eintrag je Schlüssel, die geltende Sortierung ist deutlich abgesetzt und trägt die Richtung als Pfeil. Nicht zwei winzige Pfeile je Schlüssel.

### 4 · Objektansicht

**Nicht neu strukturieren.** Die Ordnung ist am 03.09. gebaut worden und stimmt. Übernommen werden nur die Bausteine, damit Liste und Ansicht nicht auseinanderlaufen: Schrift, Zahlenbehandlung, Statuspille, Farbkante, Abstände, Knöpfe.

### 5 · Löschbestätigung — ein Leck

Die Seite nennt heute die Zahl der Vota. Das ist ein Zählstand, für jeden erreichbar, zwei Klicks von der Liste entfernt — die Zusage der letzten Runde ist damit umgehbar.

**Zielzustand:** Die Seite sagt weiterhin, dass Vota mitgehen, aber **ohne Zahl**. Notizen, Preiseinträge und Statusänderungen behalten ihre Zahlen; sie sind keine Wertung.

### 6 · Schrift

Die Oberfläche bekommt **Archivo**. Die Schriftdateien liegen **selbst gehostet** in `static/`, als `woff2`, eingebunden über `@font-face` mit `font-display:swap` und einem System-Stack als Rückfall.

**Kein Einbinden von Google Fonts oder einem anderen fremden Server.** Das Projekt speichert bewusst keine Kontaktdaten und hält sich datenschutzrechtlich zurück; ein Schriftabruf bei jedem Seitenaufruf widerspräche dem.

Benötigt werden die Schnitte 400, 500, 600 und 700. Ziffern durchgehend tabellarisch, damit Beträge untereinander stehen.

**Wenn die Dateien nicht beschaffbar sind: melden, nicht auf einen fremden Server ausweichen.** Dann bleibt es beim System-Stack, und alles andere wird trotzdem gebaut.

---

## Zeugen

**Diese Runde fasst Vorlagen und Stylesheet vollständig an. Bestehende Zeugen werden fallen. Die Regel dafür steht in `03` und gilt:**

> Misst ein Zeuge eine **Zusage an den Nutzer**, bleibt er und wird bei Bedarf auf das neue Markup nachgezogen. Misst er **Struktur oder Schreibweise** des Stylesheets oder der Vorlage, ohne eine Zusage zu bewachen, fliegt er.

Für jeden entfernten Zeugen ist im Bericht zu begründen, welche Zusage er **nicht** bewacht hat. Ein Zeuge wird nicht entfernt, weil er stört.

**Diese Zusagen müssen nach der Runde weiterhin bezeugt sein — sie sind die Zusagen der beiden letzten Runden und dürfen nicht mit dem Markup verschwinden:**

1. Die Besuchsmarke erscheint bei fremder Bewegung nach der Schwelle und nicht bei eigener.
2. Ohne eigenes Votum steht der Zählstand **nicht im ausgelieferten HTML**.
3. Mit eigenem Votum steht er da.
4. Die Freischaltung gilt je Objekt, nicht global.
5. Die Abfragezahl der Liste bleibt bei 50 Objekten konstant.

**Neue Zeugen dieser Runde:**

6. Ein Objekt ohne Ort, Region, Zustand rendert dafür **keine leere Zelle und keinen Gedankenstrich** in der Unterzeile.
7. Ein Objekt mit Ort rendert ihn.
8. Der Filterblock ist aufgeklappt, wenn ein Filter abweichend von der Vorbelegung gesetzt ist — und zu, wenn nicht.
9. Die Löschbestätigung nennt **keine Votum-Zahl**, wohl aber die übrigen Zahlen.
10. Die Statusfarbe steht als Kante an der Zeile, je Status verschieden.
11. Eine Preissenkung erscheint mit Betrag; ohne Senkung erscheint nichts.
12. Die Schrift wird aus `static/` geladen. Kein `@font-face` und kein `<link>` zeigt auf eine fremde Domain.

---

## Gegenprobe durch Sabotage

- Verdeckung ins Stylesheet verlegen → die Zeugen zu 2 müssen fallen.
- Besuchsmarke immer rendern → der Zeuge zu 1 muss fallen.
- Leeren Ort als Gedankenstrich rendern → Zeuge 6 muss fallen.
- Filterblock immer zu → Zeuge 8 muss fallen.
- Votum-Zahl in die Löschbestätigung zurückschreiben → Zeuge 9 muss fallen.
- `Exists()` durch eine Schleife ersetzen → Zeuge 5 muss fallen.
- Schrift auf eine fremde Domain umstellen → Zeuge 12 muss fallen.

**Zur Datenform:** In der Punkt-6-Runde war ein Zeuge blind, weil seine Testdaten die Unterabfrage überflüssig machten. In der Votum-Runde war einer blind, weil er eine **Zeichenkette** statt eines **Elements** suchte und ein erweiterter Klassenname an ihm vorbeilief. Beide Fehlerarten sind beim Bau der neuen Zeugen zu vermeiden: auf Elemente prüfen, nicht auf `class="…"`-Zeichenketten.

---

## Was danach unbewacht bleibt

Im Bericht benennen:

- Wie das Ergebnis **aussieht**. Kein Test misst Abstand, Kontrast oder Rhythmus.
- Das Verhalten unter 48rem im echten Browser.
- Ob die sechs Statusfarben als Kante deutlich genug unterscheidbar sind.

---

## Abschlussbericht

1. Testzahl vorher → nachher.
2. **Jeder entfernte Zeuge, mit Begründung, welche Zusage er nicht bewachte.**
3. Jeder nachgezogene Zeuge.
4. Migrationen und neue Abhängigkeiten (erwartet: keine).
5. Ergebnis jeder Sabotage.
6. Ob die Schriftdateien beschafft wurden — und woher.
7. Jede Abweichung von Entwurf und Spezifikation.
8. Was unbewacht blieb.

**Kein `git push`.** Erst nach Steffens Sichtprüfung.
