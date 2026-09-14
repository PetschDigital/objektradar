# Bauspezifikation — Objektansicht, restliche Seiten, Bilder

> Gilt für Objektradar. Zielzustand, nicht Diff.
> **Stand:** 03.09.2026 · Diese Datei liegt im Repo-Ordner.

---

## 0. Was feststeht und nicht verändert wird

**Nicht anfassen:**

- Feldnamen und `name`-Attribute aller Formulare. Der Schutz gegen Funktionsverlust sind die 611 bestehenden Tests
- Formklassen, Filterlogik, Sortierung, Blättern, Statuswechsel-Methode, Votum-Logik
- Der Kaufpreis bleibt eine Projektion des Preisverlaufs und `editable=False`. Ein leeres Preisfeld heißt „nicht ändern"
- Die Preissenkungsmarkierung aus der letzten Runde wird übernommen, nicht überarbeitet
- Abstandsskala, Feldeinheit (`templates/feld.html`) und Raster aus der Layout-Runde werden benutzt
- Bilder sind **nur URLs**. Es wird nichts heruntergeladen, nichts gespeichert, nichts zwischengelagert
- `--signal` bleibt der Preissenkung vorbehalten

**Verboten:**

- **JavaScript.** Keine Galerie, keine Lightbox, kein Klick-zum-Vergrößern, kein `onerror`. Die Oberfläche kommt ohne aus und bleibt so. Ausnahme ist allein das bestehende Lesezeichen
- Neue Abhängigkeiten, neue Modellfelder, neue Migrationen
- Änderungen an Views außer der einen in Abschnitt 4.3 erlaubten Queryset-Optimierung

**Anhalten und melden, nicht raten**, wenn:

1. Die Abfragezahl der Liste mit Bildern nicht konstant zu halten ist
2. Ein in dieser Datei genanntes Feld im Datenmodell anders heißt oder fehlt
3. Diese Datei dem widerspricht, was du im Repo vorfindest

---

## 1. Warum diese Runde

Die Layout-Runde hat die Liste repariert, weil der Screenshot die Liste zeigte. Die Objektansicht hat nie jemand angesehen — und dort arbeitet die Gruppe am längsten.

Was auf ihr heute nachweislich falsch ist:

- Die volle URL steht als Überschrift, sprengt die Seite und erzeugt einen waagerechten Scrollbalken
- „Bearbeiten" ist die zentrale Handlung der Seite und steht als kleiner Textlink, während „Notiz speichern" ein fetter Knopf ist. Steffen hat den Link übersehen — das ist die Antwort auf die Frage, was hier wichtig aussieht
- Unter der Überschrift „Daten" steht ein einziges Feld
- Preis, Fläche und €/m² — die Vergleichsgrundlage aus `01_Konzept.md` — stehen nirgends prominent
- Votum, Status und Notiz sehen gleich wichtig aus

---

## 2. Objektansicht

Die Seite beantwortet zwei Fragen: **Lohnt dieses Objekt?** und **Was tue ich damit?** In dieser Reihenfolge.

### 2.1 Kopf

**Die Bezeichnung folgt der festgelegten Rückfall-Regel:** Titel, ersatzweise Portal und Inserats-ID, ersatzweise die URL. Sie steht bereits im Modell — benutze sie, baue sie nicht nach.

Fällt sie auf die URL zurück, wird sie **gekappt**: eine Zeile, `overflow: hidden`, `text-overflow: ellipsis`. Die Seite darf unter keinen Umständen waagerecht scrollen. Das gilt für jede Breite.

Darunter eine ruhige Zeile mit Ort und Land, dem Status als abgesetzter Marke und dem Link zum Inserat. Der Status ist eine Marke, kein Fließtext — er wird gesucht, nicht gelesen.

### 2.2 Zahlen zuerst

Direkt unter dem Kopf der Block, der die Kaufentscheidung trägt:

- **Kaufpreis**, deutlich größer als alles andere auf der Seite
- darunter die **Preisänderung**, exakt wie in der Liste gebaut (Senkung `--signal`, Erhöhung `--gedaempft`)
- **€/m²**, **Wohnfläche**, **Grundstücksgröße**, **Wert nach Renovierung**

`01_Konzept.md` nennt €/m² die einzige Zahl, mit der sich zwei Objekte vergleichen lassen. Sie gehört sichtbar hierher, nicht in eine Tabellenzeile weiter unten.

Fehlt ein Wert, steht dort ein gedämpfter Strich, nicht nichts. Man muss sehen, dass die Angabe fehlt — sonst hält man sie für Null.

### 2.3 Bilder

Siehe Abschnitt 4.

### 2.4 Daten vollständig

Heute steht dort nur der Zustand. Der Block zeigt künftig **alle** am Objekt hinterlegten Felder aus `02_Datenmodell.md`: Objekttyp, Zimmer, Baujahr, Region, Portal, Inserats-ID, Quelle, Beschreibung.

**Leere Felder werden angezeigt, nicht ausgeblendet.** Ein leeres Feld ist die Aufforderung, es zu füllen — genau dafür gibt es die Ansicht. Leere Werte in `--gedaempft` als Strich.

### 2.5 Handlungen

**„Bearbeiten" wird ein Knopf**, kein Textlink, und steht sichtbar am Datenblock. Es ist die häufigste Handlung dieser Seite.

Darunter, in dieser Reihenfolge und mit abnehmendem Gewicht:

1. **Votum** — die Kernfunktion der Gruppe. Bleibt funktional wie gebaut, bekommt aber sichtbares Gewicht: eigener Block, die drei Wertungen als klar unterscheidbare Wahl, die eigene Wertung erkennbar markiert
2. **Vota der anderen** — falls heute nicht sichtbar, melden. `02` sagt: alle sehen alle Vota. Eine Ansicht, die nur das eigene zeigt, wäre ein Fehlstand
3. **Status** — Auswahl und Knopf, ruhiger als das Votum
4. **Notizen** — bestehende Notizen chronologisch mit Person und Datum, darunter das Eingabefeld

### 2.6 Fußzeile

Eingestellt von/am, zuletzt geändert von/am, zuletzt gesehen. Klein, gedämpft, am Seitenende. Das ist Beleg, nicht Inhalt.

---

## 3. Die übrigen Seiten

Gleiche Ordnungsprinzipien, kein Umbau der Funktion.

**Bearbeiten-Formular:** Feldeinheit aus der Layout-Runde, begrenzte Zeilenlänge, sinnvolle Gruppen statt einer langen Kette. Das Preisfeld bekommt einen sichtbaren Hinweis, dass leer „nicht ändern" bedeutet — diese Regel ist sonst unauffindbar. Keine neuen Felder, keine geänderte Reihenfolge innerhalb der Gruppen.

**Schnellerfassung:** Das URL-Feld ist die Hauptsache und sieht auch so aus. Der Dublettenhinweis ist deutlich, aber nicht in `--fehler`, solange er nur ein Hinweis ist.

**Übernahme-Vorschau (`/uebernehmen/`):** Sie ist die Kontrolle vor dem Speichern. Was gelesen wurde, muss von dem unterscheidbar sein, was leer blieb. Der Speichern-Knopf ist die einzige betonte Handlung der Seite.

**Login und Passwortänderung:** nur die Feldeinheit, sonst unverändert.

---

## 4. Bilder

### 4.1 Grundsatz

Bilder werden **verlinkt, nicht gespeichert**. Das steht so in `02_Datenmodell.md` und bleibt.

Daraus folgen drei Dinge, die bewusst in Kauf genommen werden:

- Der Browser lädt sie beim Portal. Deshalb `loading="lazy"` auf jedem Bild — geladen wird, was sichtbar ist
- Verschwindet ein Inserat, verschwindet das Bild. Es bleibt eine ruhige Fläche, kein kaputtes Symbol
- Portale können Bildadressen sperren. Setze `referrerpolicy="no-referrer"`; das ist eine übliche Einstellung, keine Umgehung einer Sperre

### 4.2 Wo Bilder erscheinen

**Objektliste, Tabellenfassung ab 48rem:** das erste Bild als schmale erste Spalte, etwa 4rem breit, feste Höhe, `object-fit: cover`. Größer zerreißt die Zeilenhöhe und macht den Zahlenvergleich kaputt, um den es in der Tabelle geht.

**Objektliste, Kartenfassung unter 48rem:** das erste Bild als Kopf der Karte, volle Kartenbreite, festes Seitenverhältnis.

**Objektansicht:** alle Bilder als einfaches Raster. Keine Galerie, kein Vergrößern. Wer groß sehen will, klickt aufs Inserat.

### 4.3 Der kritische Punkt

Liegen die Bild-URLs in einer eigenen Tabelle, ist die Liste ein N+1-Problem — dasselbe Muster wie beim Preisverlauf.

**Das wird über die Abfrage gelöst.** Die Abfragezahl der Liste muss konstant bleiben, gemessen bei 5 gegen 50 Objekten **mit** Bildern. Der bestehende `assertNumQueries`-Zeuge wird entsprechend erweitert.

Gelingt das nicht: anhalten und melden.

### 4.4 Kein Bild vorhanden

Der Normalfall auf absehbare Zeit. Bilder kommen nur über das Lesezeichen; beim URL-Einwurf gibt es keine, und der Bestand ist so entstanden.

Dann steht eine ruhige Fläche in `--flaeche` in derselben Größe. **Das Raster darf nicht springen** — eine Liste, in der jede zweite Zeile eine andere Höhe hat, ist unlesbar. Kein Text in der Fläche, kein Symbol, kein „kein Bild".

---

## 5. Zeugen

Diese Runde ist teilweise bewachbar. Was geht:

- Bezeichnung in der Objektansicht folgt der Rückfall-Regel — drei Fälle: mit Titel, ohne Titel mit Portal und ID, nur URL
- Objektansicht zeigt alle Felder aus 2.4, auch wenn sie leer sind
- Objektansicht zeigt die Vota **aller** Personen, nicht nur das eigene
- Objekt ohne Bilder: Seite rendert, kein `<img>` mit leerer Adresse, kein Fehler
- Objekt mit Bildern: Objektansicht zeigt alle, Liste genau eines
- Jedes `<img>` trägt `loading="lazy"`
- **Abfragezahl konstant bei 5 gegen 50 Objekten mit Bildern und Preisverlauf**
- Die bestehenden 611 Tests bleiben grün — das ist der Schutz gegen kaputte Feldnamen

**Nicht bewachbar und so zu berichten:** jede Aussage über Aussehen, Gewichtung, Größenverhältnisse, Kappung. Erfinde dafür keine Zeugen, die Klassennamen zählen. Das Kriterium aus der letzten Runde gilt: Ein Test misst eine Zusage an den Nutzer, nicht die Schreibweise des Stylesheets.

---

## 6. Abschluss

Berichte:

1. Geänderte Dateien
2. Testzahl vorher und nachher, alle grün
3. Ob einer der drei Konfliktfälle aus Abschnitt 0 auftrat
4. Wie die Bild-URLs im Modell liegen und welchen Weg du für 4.3 gewählt hast, mit gemessener Abfragezahl
5. Ob die Vota aller Personen schon sichtbar waren oder erst jetzt
6. Was du gebaut hast, das hier nicht steht, und warum
7. Welche Zusagen unbewacht bleiben

Sabotage-Gegenprobe je Zeuge. Kein `git push`.
