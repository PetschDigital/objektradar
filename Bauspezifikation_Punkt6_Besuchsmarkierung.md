# Bauspezifikation · Punkt 6 — Markierung „seit deinem letzten Besuch"

> Zielzustand, keine Diffs. Eine Bauspezifikation je Runde. Stand: 04.09.2026

---

## Auftrag in einem Satz

In der Objektliste erkennt jede Person auf einen Blick, an welchen Objekten seit ihrem letzten Besuch etwas passiert ist — durch **andere**, nicht durch sie selbst.

---

## Was bereits gilt und NICHT geändert wird

Diese Punkte sind entschieden. Sie stehen hier, damit sie nicht als offene Fragen behandelt werden. Wer meint, einer davon sei falsch, **meldet das und baut nicht um**.

- `konten/models.py` ist **fertig und wird nicht angefasst**: `Person.letzter_besuch`, `Person.besuch_davor`, `Person.neu_seit`, `besuch_registrieren()` und `BESUCHSPAUSE = 30 Minuten` bleiben, wie sie sind. Keine Migration an `Person`.
- Die Lücke wird gegen die **letzte Aktivität** gemessen, nicht gegen den Besuchsbeginn. Das ist gemessen und begründet im Docstring. Nicht umstellen.
- `neu_seit` gibt `besuch_davor` zurück und darf `None` sein. Die Property entscheidet ausdrücklich **nicht**, was `None` bedeutet — das entscheidet die Ansicht. Diese Aufgabenteilung bleibt.
- Das Wort **„neu" ist in der Oberfläche gesperrt**. Es gehört dem Status `NEU` („von niemandem angesehen"). Die Besuchsmarke heißt nicht „neu" und trägt überhaupt kein Wort in der Liste.
- **Kein JavaScript.** Die Oberfläche kommt ohne aus, das bleibt so.
- Farbpalette: `--papier`, `--flaeche`, `--linie`, `--text`, `--gedaempft`, `--akzent`, `--signal`, `--fehler`, `--warnung`. `--signal` gehört der Preissenkung, `--fehler` den Fehlern. Keine neue Farbe.
- Mobil zuerst: Karten unter 48rem, Tabelle darüber. Beide Fassungen tragen die Marke.

---

## Vorprüfung — zuerst, mit Bericht, vor jedem Bau

Zwei Befunde erheben und **berichten**. Nicht selbst auflösen.

### V1 · Feldtypen der Zeitstempel

Für `Votum`, `Notiz`, `Statusaenderung`, `Preisverlauf` und das Objekt selbst (Einstellzeitpunkt): Ist der jeweilige Zeitstempel ein `DateTimeField` oder ein `DateField`?

**Wenn auch nur einer ein `DateField` ist: anhalten und melden, nicht bauen.** Eine Schwelle von 14:30 Uhr lässt sich gegen ein reines Datum nicht prüfen; die Liste markierte dann alles vom selben Tag, auch längst Gesehenes. Das wäre eine Migration und eine Entscheidung, die im Chat fällt, nicht hier.

### V2 · Ist-Stand der Besuchs-Middleware

Was tut sie heute tatsächlich? Insbesondere:

- Ruft sie `besuch_registrieren()` auf, und bei welchen Anfragen?
- Setzt sie bereits etwas an `request`?
- Schließt sie statische Dateien aus?

Berichten, was da ist. Ältere Projektnotizen behaupten, `request.neu_seit` und der `STATIC_URL`-Ausschluss fehlten — das ist womöglich überholt.

---

## Zielzustand

### 1 · Middleware

Nach der Anmeldung gilt für jede Anfrage einer angemeldeten Person:

- `besuch_registrieren()` läuft **einmal je Anfrage**, und zwar bevor die View arbeitet.
- Danach trägt `request` die Schwelle dieser Anfrage. Sie wird **nach** dem Fortschreiben gelesen, damit im ersten Aufruf eines neuen Besuchs bereits die frische Schwelle gilt und nicht die des vorletzten.
- Anfragen auf statische Dateien lösen **kein** Fortschreiben aus. Jeder Aufruf schreibt in die Datenbank; für Stylesheets ist das ein Schreibzugriff ohne Gegenwert. Im Betrieb liefert Caddy die statischen Dateien ohnehin selbst aus, lokal nicht.
- Für nicht angemeldete Anfragen passiert nichts, und es fliegt nichts.

### 2 · Welche Objekte die Marke tragen

Ein Objekt ist markiert, wenn **nach der Schwelle** mindestens eines davon passiert ist:

| Bewegung | Von der eigenen Person ausgelöst? |
|---|---|
| Objekt eingestellt | zählt **nicht** |
| Votum abgegeben oder geändert | zählt **nicht** |
| Notiz erstellt | zählt **nicht** |
| Statusänderung | zählt **nicht** |
| Eintrag im Preisverlauf | zählt (siehe unten) |

**Eigene Bewegung zählt nicht.** Sonst leuchtet der Person ihr eigenes Tun entgegen, und die Marke wird wertlos.

**Ausnahme Preisverlauf, bewusst:** Dort hängt keine Person am Eintrag (`02` führt nur Objekt, Datum, Preis, Quelle). Eine von Hand eingetragene Preisänderung ist deshalb nicht zuzuordnen und markiert auch für die eintragende Person. Das wird **so hingenommen** — kein Personenfeld nachziehen, keine Migration. Ab Schritt 3 kommen Preisänderungen ohnehin überwiegend aus den Suchagenten-Mails, wo es keine Person gibt.

**Ist die Schwelle `None`, ist nichts markiert.** Das ist der erste Besuch einer Person oder ein Konto von vor der Einführung der Besuchszeiten. Die Gegenlesart — „alles ist neu" — ließe beim ersten Login die komplette Liste leuchten, und danach schaut niemand mehr hin.

### 3 · Wie die Marke aussieht

- Ein farbiger Punkt in `--akzent`, in der Zeile beziehungsweise auf der Karte.
- Kein Text daneben. Die Erklärung steht als `title`-Attribut: „seit deinem letzten Besuch".
- Der Punkt kostet keine Spaltenbreite in der Tabellenfassung.
- Nicht markierte Objekte bekommen **keinen** Platzhalter, der die Zeilenhöhe verändert.

### 4 · Abfragelast — der eigentliche Bauteil

Fünf Bewegungsarten je Objekt, fünfzig Objekte je Seite: naiv sind das 250 Abfragen pro Seitenaufruf. Das ist der Grund, warum dieser Punkt nicht trivial ist.

**Die Markierung wird auf der Abfrage berechnet, nicht in Python.** Als Annotation über `Exists()`-Unterabfragen, sodass die Seitenabfrage unabhängig von der Objektzahl konstant viele Abfragen braucht. Kein Auswerten je Objekt in der Vorlage, kein Zugriff, der je Zeile nachlädt.

---

## Zeugen

Je Zusage einer. Ein Zeuge, der die Testumgebung misst statt die Zusage, gilt als blind und wird ersetzt.

1. Bewegung durch eine andere Person nach der Schwelle → markiert.
2. Dieselbe Bewegung durch die eigene Person → **nicht** markiert.
3. Bewegung **vor** der Schwelle → nicht markiert.
4. Schwelle `None` → nichts markiert, auch bei frischer Bewegung.
5. Je Bewegungsart einer (Objekt, Votum, Notiz, Statusänderung, Preisverlauf) — nicht ein Sammelzeuge für alle fünf.
6. Zwei Aufrufe im Abstand unter 30 Minuten: Die Schwelle bleibt stehen, die Markierung verschwindet zwischendurch nicht.
7. Zwei Aufrufe im Abstand über 30 Minuten: Die Schwelle rückt nach.
8. Erster Aufruf eines neuen Besuchs: Die Liste zeigt bereits die **frische** Schwelle, nicht die des vorletzten Besuchs.
9. Eine Anfrage auf eine statische Datei schreibt nichts fort.
10. **Abfragelast:** `assertNumQueries` mit **50 Objekten**, nicht mit fünf. Eine kleine Menge fängt ein N+1 nicht — das ist in diesem Projekt schon einmal passiert und steht als bekannter Fehler in den Projektnotizen.
11. Die Marke steht in **beiden** Fassungen, Karte und Tabelle.
12. Behauptungen über eine Seite werden auf deren eigenen Inhalt eingegrenzt, nicht gegen die ganze Antwort geprüft. In diesem Projekt haben zwei Zeugen ihre Zeichenkette im Basis-Template gefunden.

---

## Gegenprobe durch Sabotage — Pflicht, nicht Kür

Nach dem Bau jede Zusage einzeln kaputtmachen und prüfen, ob ein Zeuge rot wird. Mindestens:

- Personenfilter bei „eigene Bewegung" entfernen → Zeuge 2 muss fallen.
- Vergleich auf `>=` statt `>` bei der Schwelle drehen → ein Zeuge muss fallen.
- Bei Schwelle `None` alles markieren statt nichts → Zeuge 4 muss fallen.
- `besuch_registrieren()` vor statt nach dem Lesen der Schwelle → Zeuge 8 muss fallen.
- `Exists()`-Annotation durch eine Schleife je Objekt ersetzen → Zeuge 10 muss fallen. **Fällt er nicht, ist die Objektzahl im Zeugen zu klein.**
- Marke aus der Kartenfassung entfernen → Zeuge 11 muss fallen.
- Eine der fünf Bewegungsarten aus der Abfrage nehmen → genau ein Zeuge muss fallen, nicht null.

Ergebnis je Sabotage berichten: welcher Zeuge fiel, oder dass keiner fiel.

---

## Was danach ausdrücklich unbewacht bleibt

Im Abschlussbericht benennen, nicht verschweigen:

- Ob die Marke im Auge tatsächlich auffällt, entscheidet die Sichtprüfung, nicht der Test.
- Nebenläufigkeit: zwei gleichzeitige Anfragen derselben Person sind mit dem Testclient nicht erreichbar.
- Die Preisverlaufs-Unschärfe aus Abschnitt 2 ist eine Entscheidung, kein Fehler.

---

## Abschlussbericht

1. Befunde der Vorprüfung V1 und V2, wörtlich.
2. Testzahl vorher → nachher.
3. Migrationen: welche, oder keine.
4. Neue Abhängigkeiten: welche, oder keine.
5. Ergebnis jeder Sabotage.
6. Jede Abweichung von dieser Spezifikation und jede Frage, die sie offenlässt.
7. Was unbewacht blieb.

**Kein `git push`.** Der Push erfolgt erst nach Steffens Sichtprüfung.
