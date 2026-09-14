# Bauspezifikation — Objektansicht zweispaltig

> Umbau der Objektansicht nach `Entwurf_Objektansicht.html` im Repo-Wurzelverzeichnis.
> **Der Entwurf ist maßgeblich.** Widerspricht diese Datei ihm, gilt der Entwurf; melde
> den Widerspruch, statt ihn selbst aufzulösen. **Stand:** 14.09.2026

---

## 0. Warum

Die Objektansicht ist zuletzt am 03.09. geordnet worden, damals einspaltig und nach zwei
Fragen: lohnt das Objekt, und was tue ich damit. Die Ordnung stimmt, die Gestalt trägt
sie nicht:

- **Zwei Drittel der Breite bleiben leer.** Der Inhaltsrahmen läuft bis `78rem`, die
  Formularfelder sind auf `30rem` begrenzt. Auf dem Rechner steht alles in einer schmalen
  Kolonne links, und die Seite scrollt entsprechend lang.
- **Knopf klebt am Feld**, an drei Stellen: Votum, Status, Notiz. Eine Regel, drei
  Auswirkungen.
- **Das Wort „Status" steht zweimal untereinander** — als Überschrift und als
  Feldbeschriftung.
- **Die vier Abschnitte sind nur durch Überschriften getrennt**, ohne Fläche oder Linie.

Der Entwurf beantwortet dieselben zwei Fragen räumlich: links das Objekt, rechts das
Handeln.

**Diese Runde ändert Gestalt und Markup, keine Funktion.** Kein Feld kommt dazu, keins
fällt weg, keine View ändert ihr Verhalten.

---

# Teil A — Aufbau

Maßgeblich ist der Entwurf. Die folgenden Punkte halten fest, was daran Entscheidung ist
und nicht Zufall der Beispieldaten.

## A1. Volle Breite oben

**Kopf:** Farbkante links in der Statusfarbe, Titel, Statuspille, Unterzeile aus
Stadtteil · Ort · Region · Land · Zustand, dazu der Link zum Inserat. Farbkante und Pille
wie in der Liste, Entscheidung vom 05.09.

**Zahlenband:** Kaufpreis als größte Zahl, daneben €/m², Wohnfläche, Grundstücksgröße,
Wert nach Renovierung. Auf abgesetzter Fläche, über die volle Breite. Die Zahlenhierarchie
ist die vom 05.09. — Kaufpreis zuerst, €/m² daneben. Leere Werte als `—`.

## A2. Zwei Spalten ab `64rem`

| Spalte | Inhalt |
|---|---|
| links, 3fr | Bilder · Daten (mit „Bearbeiten") · Preisverlauf |
| rechts, 2fr | Votum · Status · Notizen |

Die rechte Spalte ist flächig hinterlegt, die linke nicht. Das trennt Information von
Bedienung, ohne es zu beschriften.

**Der Umbruchpunkt ist `64rem`, nicht `48rem`.** Zwei Spalten bei 48rem wären auf beiden
Seiten zu eng. Unter `64rem` fällt alles untereinander.

**Die Markup-Reihenfolge ist zugleich die Reihenfolge auf dem Handy**, denn das Raster
ordnet nicht um. Damit ändert sich gegenüber heute, dass der **Preisverlauf vor das
Votum** rückt. Das ist gewollt: erst das Objekt beurteilen, dann handeln. Es ist aber
eine Änderung und keine Nebenwirkung.

## A3. Fuß

Herkunftsangabe („eingestellt von … am …", „zuletzt gesehen") und **„Objekt löschen"**,
über die volle Breite, durch eine Linie abgesetzt, Löschen in `--fehler`.

Löschen steht damit nicht mehr zwischen den Bedienelementen. Es bleibt derselbe Weg über
die Bestätigungsseite, Entscheidung vom 03.09.

## A4. Abstände und Beschriftungen

- **Zwischen Feld und zugehörigem Knopf liegt Luft** — im Entwurf `1.25rem`, als eine
  Regel für alle drei Stellen, nicht dreimal einzeln.
- **Die Statusauswahl verliert ihre sichtbare Beschriftung**, weil die Überschrift
  darüber dasselbe Wort trägt. Sie bekommt stattdessen eine zugängliche Beschriftung.
  Ersatzlos streichen ist keine Option.
- Die Anmeldeseite trägt Djangos Standard-Doppelpunkte hinter den Beschriftungen; das
  betrifft auch Formulare hier. Wo sie auftreten, fallen sie weg.

## A5. Was nicht angefasst wird

- **Kein JavaScript.** Aufklappbares nur über `<details>`, wie beim Filterblock.
- **Keine neue Farbe, keine neue Variable.** Der Entwurf definiert Farbwerte unter
  `:root` — das sind **Platzhalter** aus der Spezifikation vom 29.08. Gebaut wird
  ausschließlich gegen die vorhandenen Variablen in `objektradar.css`. Weicht der Bestand
  ab, gilt der Bestand; die Statusfarben im Entwurf sind geraten.
- **Die Schrift** ist Archivo wie bisher; der Systemstack im Entwurf ist ein Platzhalter.
- Liste, Übernahme-Vorschau, Schnellerfassung, Bearbeiten-Formular und
  Löschbestätigung bleiben unverändert.

---

# Teil B — Zusagen und Gegenprobe

Nach jeder Runde `make test`. Jede Zusage braucht einen Zeugen, der beim Ausbau umfällt.

**Diese Runde ist die, die kein Test abnimmt.** Das Abnahmekriterium ist der Blick auf
den Bildschirm, und der geschieht danach bei Steffen. Die Zeugen bewachen deshalb nicht
die Gestalt, sondern das, was ein Markup-Umbau still verlieren kann.

**Kein Zeuge misst Struktur oder Schreibweise des Stylesheets** — Entscheidung vom
03.09. Ein Test, der einen einzigen Media-Block oder eine bestimmte Rasterangabe
erzwingt, diktiert eine Bauentscheidung und wird nicht geschrieben.

1. Die Objektansicht führt weiterhin sämtliche Angaben: Titel, Status, Link zum Inserat,
   Kaufpreis, €/m², Wohnfläche, Grundstücksgröße, Wert nach Renovierung, Bilder, alle
   Felder des Datenblocks, Preisverlauf, Herkunftsangabe. **Das ist der wichtigste Zeuge
   der Runde** — ein Umbau verliert leicht ein Feld, und niemand merkt es.
2. Leere Felder werden weiterhin angezeigt, dargestellt als `—`. Entscheidung vom 03.09.
3. Der Stadtteil steht weiterhin im Datenblock, auch wenn leer. Zusage 17 der vorigen
   Runde bleibt unverändert grün.
4. **Die Vota der anderen sind weiterhin nicht im ausgelieferten HTML**, solange die
   betrachtende Person an diesem Objekt nicht abgestimmt hat — weder Wertung noch
   Begründung noch Zählstand. Entscheidung vom 05.09. Nach dem Umbau erneut zu bezeugen,
   auch wenn der vorhandene Zeuge grün bleibt: Er kann strukturell passieren, wenn der
   Block umzieht.
5. Die Statusauswahl trägt eine zugängliche Beschriftung, obwohl keine sichtbare daneben
   steht.
6. Das Wort „Status" erscheint genau einmal sichtbar über der Auswahl.
7. Das Löschformular steht im Fuß und nicht innerhalb der Bedienspalte.
8. Votum, Statuswechsel und Notiz funktionieren unverändert — je ein Zeuge, der absendet
   und die Wirkung prüft, nicht nur das Vorhandensein des Formulars.
9. `:focus-visible` ist an Knöpfen, Links und Feldern sichtbar, mit Versatz. Qualitätsboden
   aus der Spezifikation vom 29.08.

## Gegenprobe

Jede Zusage einzeln sabotieren. Besonders zu prüfen sind **1, 4 und 8**.

- **Bei 1** einzelne Felder aus dem Template entfernen und prüfen, dass der Zeuge je Feld
  fällt. Ein Zeuge, der nur die Seite lädt und den Statuscode prüft, ist blind.
- **Bei 4** ist die Falle bekannt: Zeugen prüfen Elemente und Klassenlisten, nie
  `class="…"`-Zeichenketten — 05.09.
- **Behauptungen über die Seite werden auf den Block eingegrenzt, um den es geht.** Der
  Objektname steht im `<title>`, die Statusbezeichnung sowohl in der Pille als auch in
  der Auswahl, ein Ortsname im Titel und in der Unterzeile. Ein Zeuge, der gegen die
  ganze Antwort prüft, ist blind — zweimal belegt am 03.09.

---

# Teil C — Reihenfolge und Abgrenzung

1. Markup-Umbau von `objekt.html` nach dem Entwurf, mit den Zusagen 1 bis 8. `make test`.
2. Stylesheet: Raster, Blockflächen, Abstände, Fuß, Zusage 9. `make test`.

Markup zuerst, weil die Zusagen daran hängen und ohne Umbau nicht prüfbar sind.

**Nicht Gegenstand dieser Runde:**

- Jede Änderung an der Funktion. Kein neues Feld, keine geänderte View-Logik.
- Die Liste, die Übernahme-Vorschau, die Schnellerfassung, das Bearbeiten-Formular, die
  Löschbestätigung.
- Ob die Preissenkung im Preisverlauf farbig markiert wird — der Entwurf zeigt es, es ist
  **nicht entschieden**. Bleibt wie im Bestand, bis Steffen es entscheidet.
- Jede Änderung an `00_Master.md` bis `03_Technik.md` und `_Log.md`.

---

## Nach der Abnahme

**Der Entwurf wird auf `_ARCHIV_Entwurf_Objektansicht.html` umbenannt**, sobald Steffen
die gebaute Seite abgenommen hat. Grund ist der Vorfall vom 05.09.: Ein Entwurf, der nach
dem Bau im Repo liegen bleibt, gilt bei der nächsten Runde weiter als maßgeblich und
dreht die Entscheidung samt Zeugen zurück. Das ist **kein Teil des Baus**, sondern ein
Schritt danach — nicht vorwegnehmen.

## Was diese Runde offen lässt

- **Der Umbruchpunkt `64rem` ist gesetzt, nicht gemessen.** Ein 13-Zoll-Laptop liegt
  knapp darüber. Ob die zweispaltige Fassung dort noch trägt, zeigt erst der Blick.
- **Die rechte Spalte läuft nicht mit.** `position: sticky` wäre ohne JavaScript möglich
  und ist bewusst weggelassen, weil es bei kleinen Fenstern stört. Nachrüstbar.
- **Der Entwurf ist an einem Objekt mit vier Bildern und zwei Preiseinträgen gezeichnet.**
  Ein Objekt ohne Bild und ohne Preisverlauf lässt die linke Spalte kurz werden, während
  die rechte steht. Wie das aussieht, ist ungeprüft.
