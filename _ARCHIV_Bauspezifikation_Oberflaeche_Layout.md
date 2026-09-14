# Bauspezifikation — Oberfläche, Layout-Runde

> Gilt für Objektradar. Zielzustand, nicht Diff.
> **Stand:** 02.09.2026 · Diese Datei liegt im Repo-Ordner.

---

## 0. Was feststeht und nicht verändert wird

Diese Runde ändert **Markup-Struktur und Darstellung**. Sonst nichts.

**Nicht anfassen — auch nicht „nebenbei sinnvoll":**

- Views, Querysets, Formklassen, `urls.py`, Modelle, Migrationen
- Feldnamen und `name`-Attribute aller Formularfelder. Der Filter liest sie aus GET; ein umbenanntes Feld bricht den Filter, ohne dass die Darstellung es zeigt
- Die Erzeugung der Query-Strings für Blätter- und Sortierlinks. Sie tragen die gesetzten Statuswerte mit — das ist eine festgelegte Entscheidung, kein Zufall
- Die Vorbelegung des Statusfilters. Sie liegt im Formular, nicht im Template, und bleibt dort
- Redirect-Ziele: nach dem Einwerfen auf die Liste, nach der Übernahme auf die Objektansicht
- Die Farbpalette. `--signal` bleibt der Preissenkung vorbehalten, `--fehler` den Fehlern. Keine neue Farbe ohne Anlass
- Mobil zuerst, Karten unter 48rem, Tabelle darüber. Der Umbruchpunkt bleibt

**Ausdrücklich verboten:**

- Neue Abhängigkeiten. Kein CSS-Framework, kein Utility-Kit, kein Icon-Paket
- JavaScript. Die Oberfläche kommt ohne aus und soll das bleiben. Ausnahme ist allein das bestehende Lesezeichen — das ist kein Seiten-Skript
- Neue Migrationen
- Umbenennen bestehender CSS-Variablen

**Wenn du beim Lesen auf einen dieser drei Fälle triffst: anhalten und melden, nicht raten.**

1. Ein Formularfeld lässt sich ohne Änderung an der Formklasse nicht so rendern, wie unten beschrieben
2. Ein Sortier- oder Blätterlink baut seinen Query-String im Template zusammen statt in View oder Template-Tag
3. Diese Datei widerspricht dem, was du im Repo vorfindest

Alles andere baust du in einer Runde durch.

---

## 1. Ausgangslage

Das Werkzeug läuft. 548 Tests sind grün. Die Oberfläche ist im Browser trotzdem unbrauchbar, und zwar aus vier Gründen, die unten einzeln behandelt werden.

Der wichtigste Satz dieser Spezifikation: **Punkt 2 ist ein Fehler, die Punkte 3 bis 5 sind Gestaltung.** Der Fehler wird behoben, die Gestaltung wird nach den Regeln unten gebaut.

---

## 2. Fehler: drei Template-Kommentare stehen als Text auf der Seite

In `objektliste.html` stehen an drei Stellen Kommentare in `{# … #}`, die über mehrere Zeilen umgebrochen sind. Django wertet `{# #}` **nur einzeilig** aus. Ein umgebrochener Kommentar ist kein Kommentar, sondern Text, und wird ausgegeben.

**Zielzustand:** Kein Kommentar erscheint im gerenderten HTML.

**Umsetzung:** Entweder einzeilig schreiben oder in `{% comment %}…{% endcomment %}` fassen. Prüfe alle Templates, nicht nur `objektliste.html` — dieselbe Umbruchgewohnheit kann anderswo stehen.

---

## 3. Kopfleiste

**Ist:** „Lesezeichen  Petsch  Passwort ändern  abmelden" stehen gleichrangig nebeneinander. Ein Kontoeintrag sieht aus wie ein Navigationspunkt.

**Zielzustand:** Zwei Bereiche, klar getrennt.

- **Links:** der Werkzeugname als Link auf die Objektliste
- **Rechts:** zwei Gruppen mit sichtbarem Abstand dazwischen
  - Navigation: „Lesezeichen"
  - Konto: der angemeldete Name, „Passwort ändern", „abmelden"

Die Kontogruppe ist der Navigation **untergeordnet**, nicht gleichrangig. Sie steht in kleinerer Schrift und in `--gedaempft`, getrennt durch Abstand und eine senkrechte Linie in `--linie`. Der angemeldete Name ist kein Link.

**Kein Aufklappmenü.** Es bräuchte JavaScript oder `<details>`, und für drei Elemente bei fünf Personen steht der Aufwand nicht dafür.

**Keine Mittelpunkte als Trenner.** Gruppen werden durch Abstand und Linie getrennt, nicht durch Satzzeichen.

**Unter 48rem:** Die Leiste bricht um. Werkzeugname oben, darunter beide Gruppen in einer Reihe. Sie darf nicht waagerecht scrollen und keinen Eintrag abschneiden.

Die Kopfleiste steht im Basis-Template und gilt damit für alle Seiten. Auf Login und Passwortänderung ist sie entsprechend reduziert oder nicht vorhanden — ändere dort nichts, was heute schon stimmig ist.

---

## 4. Filterblock

**Ist:** „Suche" steht allein links. „Status:" sitzt senkrecht versetzt neben seinen sechs Kontrollkästchen. „Land:" hängt rechts oben. Zwischen den Reihen klaffen große Lücken.

Die versetzten Beschriftungen kommen daher, dass Beschriftung und Feld nebeneinander stehen und bei unterschiedlich hohen Feldern auseinanderlaufen. Das ist die Wurzel, nicht die Abstände.

**Zielzustand:**

**Beschriftung steht über ihrem Feld**, nicht daneben. Beschriftung und Feld bilden zusammen eine Einheit, die als Ganzes im Raster sitzt. Damit kann nichts mehr versetzen.

**Ein Raster, keine gestapelten Zeilen.** CSS Grid. Unter 48rem eine Spalte. Darüber mehrere Spalten fester Mindestbreite, sodass die Feldpaare nebeneinander liegen. Zusammengehörige Felder — Preis von/bis, Fläche von/bis — bleiben nebeneinander und werden nicht getrennt umbrochen.

**Der Statusfilter ist ein Sonderfall.** Sechs Kontrollkästchen sind kein Feld wie die anderen. Er bekommt eine eigene Zeile über die volle Rasterbreite, als `<fieldset>` mit `<legend>Status</legend>`. Die sechs Kästchen liegen darin als umbrechende Reihe. Jedes `<input>` steckt in seinem `<label>`, damit Kästchen und Wort nie auseinanderfallen und das Wort anklickbar ist.

**Abstände nach einer Skala.** Definiere im Stylesheet drei bis vier Abstandsstufen als Variablen und verwende ausschließlich diese. Die großen Lücken kommen von Margins ohne System; eine Skala löst das dauerhaft. Der Abstand zwischen zwei Feldern im Raster ist kleiner als der Abstand zwischen Filterblock und Ergebnisliste — der Block muss als ein Ding lesbar sein.

**Die Aktionen** („Filtern", „Zurücksetzen" oder wie sie heute heißen) stehen unten im Block, rechtsbündig ab 48rem, volle Breite darunter. Beschriftungen bleiben, wie sie sind — Umbenennen ist nicht Teil dieser Runde.

**Der Block ist optisch abgesetzt** von der Ergebnisliste: Hintergrund `--flaeche` gegen `--papier` oder eine Linie in `--linie`. Eines von beidem, nicht beides.

---

## 5. Sortierleiste

**Ist:** „eingeworfen ↑↓ Kaufpreis ↑↓ …" steht ohne erkennbare Zuordnung über der Tabelle. Man sieht nicht, dass es zur Tabelle gehört, und nicht, wonach gerade sortiert ist.

**Zielzustand:**

Die Leiste bleibt eine eigene Leiste und wandert **nicht** in die Tabellenkopfzeile. Grund: Unter 48rem gibt es keine Tabelle, sondern Karten. Sortierung im Tabellenkopf bräuchte einen zweiten Mechanismus für die Kartenansicht.

- Eine vorangestellte Beschriftung „Sortieren nach" macht die Funktion lesbar
- Die Leiste sitzt direkt über der Liste, mit deutlich kleinerem Abstand nach unten als nach oben. Nähe ist die Zuordnung
- **Die aktive Sortierung ist erkennbar:** aktiver Schlüssel in `--text` und hervorgehoben, inaktive in `--gedaempft`. Die aktive Richtung ist als einzelner Pfeil sichtbar; inaktive Schlüssel zeigen keinen oder einen gedämpften Pfeil. Heute stehen offenbar beide Pfeile überall — dann sieht man nie, was gilt
- Unter 48rem bricht die Leiste um oder scrollt waagerecht in sich. Sie darf die Seite nicht breiter machen

---

## 6. Liste, Objektansicht, Formulare

Diese Seiten waren nicht Anlass der Runde, erben aber Kopfleiste und Abstandsskala. Bringe sie mit, ohne sie umzubauen:

- **Zahlenspalten rechtsbündig**, Text linksbündig. Preise und Flächen sind nur vergleichbar, wenn die Stellen untereinander stehen
- **Die Objektspalte wird gekappt**, wenn kein Titel da ist und die volle URL steht. `max-width` auf einer Zelle wirkt bei `table-layout: auto` nur als Hinweis — setze `table-layout: fixed` für die Tabelle oder kappe im Zellinhalt über ein inneres Element mit `overflow: hidden` und `text-overflow: ellipsis`
- **Formularseiten** (Schnellerfassung, Bearbeiten, Übernahme-Vorschau, Login, Passwortänderung) bekommen dieselbe Feldeinheit wie der Filterblock: Beschriftung über dem Feld, gleiche Abstandsskala, begrenzte Zeilenlänge. Keine neuen Felder, keine geänderte Reihenfolge
- **Fehlermeldungen** bleiben in `--fehler`. Prüfe im Vorbeigehen, dass sie nirgends in `--signal` stehen

---

## 7. Was diese Runde bezeugen kann

Layout ist per Test praktisch nicht bewachbar. Genau ein echter Zeuge ist möglich:

**`assertNotContains` auf den Kommentartext** aus Abschnitt 2. Nimm eine wörtliche Zeichenfolge aus einem der drei Kommentare und prüfe, dass sie im gerenderten HTML der Objektliste nicht vorkommt. Der Zeuge muss auf dem Kommentartext messen, nicht auf `{#` — sonst fängt er den Fall nicht, in dem der Kommentar in anderer Form wieder auftaucht.

**Der zweite Schutz sind die 548 bestehenden Tests.** Sie sind der Grund, warum Feldnamen und Query-Strings unangetastet bleiben: Bricht dort etwas, hast du Funktion verändert, nicht Darstellung.

**Erfinde keine weiteren Zeugen.** Ein Test, der Klassennamen zählt oder prüft, ob ein `<fieldset>` existiert, misst dein eigenes Markup und nicht die Zusage. Er wäre grün, während die Seite unbrauchbar ist — genau das Muster, das in diesem Projekt schon dreimal aufgetreten ist und ausdrücklich als blind gilt.

**Die Sabotage-Gegenprobe läuft in dieser Runde weitgehend leer.** Führe sie für den einen Zeugen durch und berichte offen, dass der Rest unbewacht ist. Das ist die richtige Antwort, kein Mangel.

---

## 8. Abschluss der Runde

Berichte am Ende:

1. Welche Dateien geändert wurden
2. Testzahl vorher und nachher, und ob alle grün sind
3. Ob einer der drei Konfliktfälle aus Abschnitt 0 aufgetreten ist
4. Was du gebaut hast, das in dieser Spezifikation nicht steht, und warum
5. Welche Zusagen unbewacht bleiben

Kein `git push`. Das macht Steffen nach der Sichtprüfung.

---

## 9. Sichtprüfung durch Steffen

Nach der Runde lokal im Browser, **an beiden Breiten** — Fenster schmal ziehen unter 48rem und wieder breit:

- Kein Kommentartext auf der Seite
- Kopfleiste: Konto von Navigation abgesetzt, schmal umgebrochen ohne Abschneiden
- Filterblock: alle Beschriftungen sitzen über ihren Feldern, nichts versetzt, keine klaffenden Lücken
- Statusfilter: sechs Kästchen als eine Gruppe, Wort anklickbar
- Sortierleiste: erkennbar zur Liste gehörig, aktive Sortierung und Richtung sichtbar
- Ein Objekt ohne Titel: Zeile bleibt in der Spalte, die URL sprengt sie nicht
- Filtern und Blättern funktionieren wie vorher, Status bleibt beim Blättern gesetzt
