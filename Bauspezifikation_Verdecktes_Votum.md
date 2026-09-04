# Bauspezifikation · Verdecktes Votum

> Zielzustand, keine Diffs. Eine Bauspezifikation je Runde. Stand: 04.09.2026

---

## Auftrag in einem Satz

Wer an einem Objekt noch nicht abgestimmt hat, sieht die Vota der anderen an diesem Objekt nicht — weder in der Liste noch in der Objektansicht. Mit der eigenen Stimme wird alles freigeschaltet.

**Das kippt eine bisher aktive Entscheidung.** `01` und `02` sagen beide: „Alle sehen alle Vota. Kein verdecktes Abstimmen." Das ist ab jetzt anders, begründet mit dem Ankereffekt: Wer „3 dafür" liest, stimmt eher zu, und dann misst das Votum eine Meinung plus vier Bestätigungen. Die alten Einträge werden beim Tagesabschluss auf `revidiert` gesetzt. **Kein Grund, die Entscheidung erneut zu diskutieren.**

---

## Was bereits gilt und NICHT geändert wird

- **Ein Votum je Person und Objekt, jederzeit änderbar.** Bleibt.
- **Jedes Votum schaltet frei**, auch „anschauen". Keine Sonderbehandlung einzelner Wertungen.
- **Der Status bleibt sichtbar**, in Liste und Objektansicht. Er ist Arbeitszustand, kein Votum.
- **Notizen bleiben sichtbar.** `02` trennt sie ausdrücklich von der Wertung.
- Die Besuchsmarkierung aus Punkt 6 bleibt unverändert. Ein fremdes Votum setzt weiterhin die Marke — die sagt nur, *dass* etwas passiert ist, nicht *wie* gestimmt wurde. Das ist gewollt und wird nicht ausgebaut.
- `konten/models.py` und `Person` werden nicht angefasst.
- **Kein JavaScript.**
- Farbpalette unverändert. Keine neue Farbe.
- Mobil zuerst: Karten unter 48rem, Tabelle darüber. Beide Fassungen tragen dieselbe Zusage.

---

## Zielzustand

### 1 · Die Freischaltung gilt je Objekt und je Person

Eine Person sieht die Vota an einem Objekt genau dann, wenn sie an **diesem** Objekt ein Votum hat. Ein Votum an Objekt A schaltet Objekt B nicht frei. Es gibt keinen globalen Schalter und keine Freischaltung durch Anzahl.

### 2 · Liste

**Ohne eigenes Votum** steht in der Votum-Spalte ein Aufruf zum Abstimmen — Wortlaut „abstimmen", verlinkt auf die Objektansicht. Keine Zahl, kein Zählstand, kein „noch kein Votum".

**Mit eigenem Votum** steht dort die Votum-Übersicht wie bisher.

### 3 · Objektansicht

**Ohne eigenes Votum:** Die Vota der anderen sind nicht da — weder Wertung noch Begründung noch Person noch Zählstand. An ihrer Stelle steht ein kurzer Hinweis, dass die Vota der anderen nach der eigenen Stimme sichtbar werden. Das Votum-Formular ist normal bedienbar.

**Mit eigenem Votum:** Alles sichtbar wie bisher.

**Das eigene Votum ist immer sichtbar**, auch vor der Freischaltung — es ist die Voraussetzung dafür, es ändern zu können.

### 4 · Verdeckt heißt: nicht im HTML

Die verdeckten Werte werden **nicht gerendert**. Sie stehen nicht im ausgelieferten Markup und werden nicht per Stylesheet, `hidden`-Attribut oder Kommentar unsichtbar gemacht. Wer den Quelltext ansieht, findet sie nicht.

Das ist der Kern der Zusage. Eine Verdeckung, die nur im Auge wirkt, ist keine.

### 5 · Abfragelast

Die Frage „hat diese Person an diesem Objekt gevotet" wird **auf der Abfrage** beantwortet, als `Exists()`-Annotation. Kein Zugriff je Zeile in der Vorlage, keine Schleife in der Ansicht. Die Abfragezahl der Liste bleibt unabhängig von der Objektzahl konstant.

---

## Zeugen

Je Zusage einer. Ein Zeuge, der die Testumgebung misst statt die Zusage, gilt als blind und wird ersetzt.

1. Liste ohne eigenes Votum: Der Zählstand steht **nicht im Antworttext**. Nicht „ist unsichtbar" — nicht vorhanden.
2. Liste ohne eigenes Votum: Der Aufruf „abstimmen" steht da.
3. Liste mit eigenem Votum: Der Zählstand steht da.
4. **Je Objekt getrennt:** Dieselbe Person, zwei Objekte, an einem gevotet. Ein Objekt zeigt, das andere verdeckt — in einer einzigen Antwort.
5. Objektansicht ohne eigenes Votum: Weder Wertung noch **Begründungstext** der anderen im Antworttext. Die Begründung bekommt einen eigenen Zeugen mit einer unverwechselbaren Zeichenkette.
6. Objektansicht ohne eigenes Votum: Das Votum-Formular ist bedienbar.
7. Objektansicht mit eigenem Votum: Fremde Wertungen und Begründungen sind da.
8. Das eigene Votum ist vor der Freischaltung sichtbar.
9. Nach dem Abgeben eines Votums ist im **nächsten** Aufruf alles frei.
10. Ein geändertes Votum hält die Freischaltung.
11. **Abfragelast:** `assertNumQueries` mit **50 Objekten**, davon etwa die Hälfte mit eigenem Votum und die Hälfte ohne. Eine Menge, in der alle Objekte gleich stehen, misst den anderen Zweig nicht.
12. Die Zusage gilt in **beiden** Fassungen, Karte und Tabelle.

**Zur Datenform der Zeugen:** In der letzten Runde war ein Zeuge blind, weil seine Testdaten die Unterabfrage überflüssig machten. Beim Bau von Zeuge 11 ist zu prüfen, dass keine Zeile die Frage aus sich selbst beantworten kann.

---

## Gegenprobe durch Sabotage — Pflicht, nicht Kür

Jede Zusage einzeln kaputtmachen, Ergebnis je Sabotage berichten:

- Verdeckung ins Stylesheet verlegen statt im Template weglassen → Zeuge 1 und 5 müssen fallen. **Fallen sie nicht, prüfen die Zeugen die Sichtbarkeit statt die Anwesenheit und sind blind.**
- Prüfung global statt je Objekt („hat irgendwo gevotet") → Zeuge 4 muss fallen.
- Das eigene Votum mitverdecken → Zeuge 8 muss fallen.
- Freischaltung umdrehen (nur ohne eigenes Votum sichtbar) → mehrere müssen fallen.
- `Exists()` durch eine Schleife je Objekt ersetzen → Zeuge 11 muss fallen.
- Verdeckung nur in der Liste, nicht in der Objektansicht → Zeuge 5 muss fallen.
- Begründung stehen lassen, nur die Wertung verdecken → der Begründungs-Zeuge muss fallen.

---

## Was danach ausdrücklich unbewacht bleibt

Im Abschlussbericht benennen:

- **Der Ankereffekt ist gesenkt, nicht beseitigt.** Status und Notizen bleiben sichtbar.
- Ob der Hinweistext verständlich ist, entscheidet das Auge.
- Wird ein Votum gelöscht — falls das überhaupt möglich ist —, kehrt die Verdeckung zurück, nachdem die Person alles gesehen hat. Ob dieser Weg existiert, ist beim Bau zu **berichten**, nicht zu bauen.

---

## Abschlussbericht

1. Testzahl vorher → nachher.
2. Migrationen: welche, oder keine (erwartet: keine).
3. Neue Abhängigkeiten: welche, oder keine.
4. Ergebnis jeder Sabotage.
5. Jede Abweichung von dieser Spezifikation und jede Frage, die sie offenlässt.
6. Was unbewacht blieb.
7. Ob ein Weg existiert, ein Votum zu löschen.

**Kein `git push`.** Der Push erfolgt erst nach Steffens Sichtprüfung.
