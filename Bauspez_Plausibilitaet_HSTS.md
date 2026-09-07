# Bauspezifikation — Plausibilitätswarnung beim Zulauf, HSTS-Anhebung

**Stand:** 07.09.2026
**Umfang:** Eine Bauunde, zwei unabhängige Teile. Teil A ist der Bau,
Teil B eine Zahl.

---

# Teil A — Plausibilitätswarnung in der Übernahme-Vorschau

## Anlass

Ein Bestandsobjekt trägt 30.239.000 € bei 84 m² — 359.988 €/m². Lesefehler
des Lesezeichens an einem Neubauprojekt, bei dem der Gesamtpreis des
Projekts statt des Wohnungspreises im sichtbaren Text stand.

`01_Konzept.md` nennt den €/m²-Preis die einzige Zahl, mit der sich zwei
Objekte überhaupt vergleichen lassen. Ein falsch gelesener Kaufpreis
vergiftet genau diese Zahl. Bei 359.988 €/m² fällt es auf. Bei einem um den
Faktor zehn verlesenen Preis fällt es niemandem auf, und das Objekt sortiert
sich still an die falsche Stelle der Liste.

## Was gebaut wird

Die Übernahme-Vorschau (GET `/uebernehmen/`) prüft die übermittelten Werte
auf Plausibilität und zeigt bei Auffälligkeit eine **Warnung** in der
bestehenden vierten Meldungsstufe (`--warnung`, seit 03.09.2026).

### Prüfung 1 — €/m²

Läuft nur, wenn ein Preis **und** eine Wohnfläche übermittelt wurden und die
Wohnfläche größer als 0 ist.

Errechnet wird Preis ÷ Wohnfläche. Gewarnt wird bei einem Wert

- **unter 250 €/m²** oder
- **über 12.000 €/m²**

### Prüfung 2 — absoluter Preis

Läuft immer, wenn ein Preis übermittelt wurde — auch ohne Wohnfläche.
Gewarnt wird **über 3.000.000 €**.

Grund für die zweite Prüfung: Die €/m²-Prüfung läuft ins Leere, wenn keine
Wohnfläche gelesen wurde. Genau dann bleibt ein absurder Preis unbemerkt.

Beide Prüfungen sind unabhängig und können gleichzeitig anschlagen. Dann
erscheinen zwei Warnungen.

### Die Grenzwerte sind geschätzt, nicht gemessen

Es gibt keine Datenbasis — die Liste ist zu klein. Die Werte sind bewusst
**weit** gefasst: Eine Warnung, die zu oft falsch anschlägt, wird nach zwei
Wochen ignoriert, und dann ist sie schlechter als keine. Ein spanisches
Ruinenobjekt kann echte 300 €/m² haben.

Die Grenzwerte stehen als benannte Modulkonstanten an einer Stelle, nicht als
Zahlen im Code verstreut, und tragen einen Kommentar, dass sie geschätzt sind
und nach den ersten Betriebswochen gegen echte Daten zu prüfen.

## Was ausdrücklich nicht gebaut wird

**Es wird nicht gesperrt.** Die Übernahme bleibt in jedem Fall speicherbar.
Dieselbe Linie wie bei der Warnung zur unbekannten Domain, und
`03_Technik.md` verlangt ausdrücklich: Ein Lesefehler darf nie dazu führen,
dass ein Objekt verloren geht.

**Es wird nichts korrigiert oder verworfen.** Kein Umrechnen, kein Kappen,
kein Nullsetzen. Die Werte stehen unverändert im Formular und lassen sich von
Hand ändern.

**Die Prüfung gilt nur für die Übernahme-Vorschau**, nicht für das
Bearbeiten-Formular und nicht für die Schnellerfassung. Dort gibt ein Mensch
die Zahl ein und sieht sie dabei an; der Fehler, um den es geht, entsteht
beim maschinellen Auslesen.

**Nichts wird gespeichert.** Die Warnung ist eine Anzeige auf der Vorschau,
kein Feld am Objekt.

## Wortlaut

Die Warnung nennt den errechneten Wert und benennt sie als Verdacht, nicht
als Feststellung. Sinngemäß: „Der errechnete Preis je m² liegt bei
359.988 €/m². Das deutet auf einen Lesefehler hin — bitte Kaufpreis und
Wohnfläche prüfen."

Formulierung im Detail liegt bei Claude Code. Sie darf nicht nach Fehler
klingen: Ein Objekt mit ungewöhnlichem Preis muss ohne schlechtes Gewissen
speicherbar sein.

## Zeugen

Es gilt durchgehend:

- Zeugen prüfen **Elemente und Klassenlisten**, nie `class="…"`-Zeichenketten.
- Behauptungen über eine Seite werden auf deren eigenen Inhalt eingegrenzt.
- Ein Zeuge, der die Testumgebung misst statt die Zusage, gilt als blind.

Zu bewachen sind mindestens:

1. Ein Wert unter 250 €/m² erzeugt eine Warnung.
2. Ein Wert über 12.000 €/m² erzeugt eine Warnung.
3. Ein Wert dazwischen erzeugt **keine** Warnung.
4. Der echte Fall: 30.239.000 € bei 84 m² erzeugt eine Warnung.
5. Wohnfläche 0 erzeugt **keine** Division durch Null und keine
   €/m²-Warnung. Auf PostgreSQL ist das kein theoretischer Fall — eine
   Division durch Null wirft dort `psycopg.errors.DivisionByZero`.
6. Fehlende Wohnfläche: die €/m²-Prüfung entfällt, die Preisprüfung läuft.
7. Ein Preis über 3.000.000 € ohne Wohnfläche erzeugt eine Warnung.
8. **Preis 0 wird gegen `is not None` geprüft, nicht gegen den
   Wahrheitswert.** Eine 0 ist ein übermittelter Preis und ergibt 0 €/m² —
   also eine Warnung, nicht ein stilles Überspringen.
9. Die Warnung erscheint in der Stufe **Warnung**, nicht als Fehler.
10. Die Warnung **sperrt nicht**: Der anschließende POST legt das Objekt an.
11. Ohne übermittelten Preis erscheint keine der beiden Warnungen.

## Gegenprobe

Die übliche Sabotage: jede Zusage einzeln brechen und prüfen, ob ein Zeuge
fällt.

Besonders zu prüfen sind Zeuge 5 und Zeuge 8. Beide bewachen einen Fall, der
in Testdaten leicht strukturell erfüllt sein kann, ohne dass der Code ihn je
durchläuft — die Wahrheitsprüfung auf einen Preis von 0 ist in diesem Projekt
bereits einmal aufgetreten und hätte eine Eingabe wortlos verworfen.

---

# Teil B — HSTS von 300 auf 2.592.000 Sekunden

In `config/settings.py`, Zeile 171:

```
SECURE_HSTS_SECONDS = 300
```

wird zu

```
SECURE_HSTS_SECONDS = 2592000
```

Das sind 30 Tage.

**Unverändert bleiben:**

- `SECURE_HSTS_INCLUDE_SUBDOMAINS = True` in Zeile 172. Der Kopf gilt für
  `objektradar.petsch-digital.com` und dessen Unterdomains — nicht für
  `petsch-digital.com` und nicht für die VVL-Demo.
- `SECURE_HSTS_PRELOAD` bleibt **nicht gesetzt**. Preloading gilt für die
  Hauptdomain; eine Unterdomain kommt weder in die Browser-Liste noch soll
  sie das.
- `SECURE_HSTS_SECONDS = 0` in `config/settings_test.py` bleibt bei 0.

**Der begleitende Kommentar über der Zeile ist anzupassen.** Er begründet
derzeit die 300 Sekunden mit „für die ersten Betriebstage". Neue Begründung:
Fünf Betriebstage ohne Zwischenfall, aber die erste automatische
Zertifikatserneuerung durch Caddy steht noch aus — sie fällt bei 90 Tagen
Laufzeit und Erneuerung bei rund 30 Tagen Rest auf Anfang November. Ein Jahr
kommt danach, nicht davor.

## Zeugen

12. `SECURE_HSTS_SECONDS` steht in `config/settings.py` auf 2592000.
13. `SECURE_HSTS_SECONDS` steht in `config/settings_test.py` weiterhin auf 0.
14. `SECURE_HSTS_PRELOAD` ist in `config/settings.py` nicht gesetzt.

---

## Vor dem Bau melden

Weicht der Aufbau der Übernahme-Vorschau oder der Meldungsstufen von dieser
Spezifikation ab: **halten und berichten**, nicht auf Annahmen weiterbauen.

## Nach dem Bau berichten

- Testzahl vorher und nachher
- Erzeugte Migrationen (hier: es sollten keine anfallen)
- Jede Abweichung von dieser Spezifikation
- Jede Entscheidung, die die Spezifikation offengelassen hat
- Ergebnis der Gegenprobe, mit Zahl der Sabotagen und gefundenen blinden
  Zusagen
