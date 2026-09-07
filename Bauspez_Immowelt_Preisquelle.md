# Bauspezifikation — Preisquelle bei Immowelt

**Stand:** 07.09.2026
**Umfang:** Eine Bauunde. Betrifft ausschließlich das Lesezeichen-Skript
und die Übernahme, nicht das Datenmodell.

---

## Anlass — gemessen, nicht vermutet

Seit der Aufnahme von Immowelt in die Portal-Auswahl (07.09.2026) sieht das
Portal abgedeckt aus. Der Dublettenschlüssel ist belegt, **das Preislesen war
es nie.** Es wurde am 29.08. an Idealista entwickelt und nie an Immowelt
geprüft.

Vier echte Objekte aus dem Bestand:

| Ort | Inserat sagt | gespeichert | Aufschlag |
|---|---|---|---|
| Bad Herrenalb (BW) | 139.000 € | 153.692 € | 10,57 % |
| Hofgeismar (HE) | 210.000 € | 233.058 € | 10,98 % |
| Stralsund (MV) | 138.000 € | 153.967 € | 11,57 % |
| Neu Lüdershagen (MV) | 290.000 € | 323.553 € | 11,57 % |

Die beiden MV-Objekte liegen auf identischen 11,57 %, Baden-Württemberg
exakt einen Prozentpunkt darunter — die Differenz der Grunderwerbsteuer
(5 statt 6 %). Das Lesezeichen greift auf Immowelt-Seiten den **Kaufpreis
inklusive Kaufnebenkosten**, nicht den Kaufpreis.

Der richtige Preis steht auf der Seite ganz oben, groß, mit €/m² daneben.
Das Textmuster findet ihn nicht.

**Warum das schwerer wiegt als der Lesefehler an einem Neubauprojekt:**
30 Millionen fallen auf. Elf Prozent fallen niemandem auf. Sie liegen mitten
im plausiblen Bereich, die Plausibilitätswarnung von heute Vormittag kann sie
nicht fangen, und `01_Konzept.md` nennt €/m² die einzige Zahl, mit der sich
zwei Objekte vergleichen lassen. Jedes Immowelt-Objekt sortiert sich still zu
teuer ein.

## Was gebaut wird

### Preisquelle für Immowelt: `og:title`

Auf Immowelt-Seiten wird der Preis aus dem Open-Graph-Titel gelesen, nicht
über das Textmuster im sichtbaren Seiteninhalt.

Belegtes Format, fünf Fälle aus zwei Tagen:

```
Wohnung 68 m² 139000 € zum Kauf Bernbach,Bad Herrenalb (76332)
Haus 115 m² 290000 € zum Kauf Neu Lüdershagen,Wendorf (18442)
Wohnung 76 m² 140000 € zum Kauf Bad Herrenalb,Bad Herrenalb (76332)
Haus 95 m² 210000 € zum Kauf Hofgeismar,Hofgeismar (34369)
Wohnung 62 m² 138000 € zum Kauf Großer Diebsteig 11,Frankenvorstadt,Stralsund
```

Die Zahl vor dem `€` ist der Kaufpreis. Die Zahl vor `m²` ist die Wohnfläche
— sie wird **nicht** aus dieser Quelle gelesen, das bestehende Verfahren für
die Wohnfläche bleibt unverändert.

### Der Riegel — der eigentliche Kern dieser Runde

Das Muster greift **nur, wenn im Titel genau eine Zahl mit `€` steht.**

- Keine Zahl mit `€` → **kein Preis gelesen**, Feld bleibt leer.
- Mehr als eine Zahl mit `€` → **kein Preis gelesen**, Feld bleibt leer.

Kein Rückfall auf das alte Textmuster, kein Ratenraten, kein „die erste
nehmen".

**Begründung:** Der heutige Fehler ist still — es kommt ein falscher Wert an,
und niemand merkt es. Ändert Immowelt das Titelformat, muss der Weg auf
„kein Preis" fallen, nicht auf „irgendein Preis". Ein leeres Feld sieht man,
einen um elf Prozent falschen nicht. Ein Inserat auf Anfrage ohne Preisangabe
ist genau der Fall, für den der Riegel gebaut ist.

`03_Technik.md` verlangt ohnehin: Ein Lesefehler darf nie dazu führen, dass
ein Objekt verloren geht. Das Objekt wird auch ohne Preis angelegt.

### Das alte Textmuster gilt für Immowelt nicht mehr

Auf Immowelt-Seiten wird das Fließtext-Preismuster **nicht** mehr angewandt —
weder als erster Versuch noch als Rückfall. Es liefert dort belegt den
falschen Wert.

Für alle anderen Portale bleibt es unverändert.

## Bewusste Abweichung von 03_Technik.md

`03` sagt: „Portalspezifische CSS-Auswahlen gibt es bewusst nicht — das
Markup ist nicht dokumentiert und geratene Auswahlen brechen unbemerkt."

Das hier ist eine portalspezifische Regel. Der Unterschied: Es ist keine
geratene CSS-Auswahl im Markup, sondern ein Meta-Feld, aus dem das Lesezeichen
ohnehin schon Titel, Beschreibung und Bilder liest, mit an fünf echten
Inseraten belegtem Format.

Der Satz aus `03` bleibt für CSS-Auswahlen gültig. Die Abweichung ist bewusst
und gehört ins Entscheidungs-Log.

## Was nicht gebaut wird

- **Keine Umrechnung.** Aus 323.553 € wieder 290.000 € zu rechnen hieße, den
  Aufschlag zu erraten. Er hängt am Bundesland und an der Courtage.
- **Keine Änderung an den anderen Portalen.**
- **Keine Änderung an Wohnfläche, Zimmerzahl, Titel, Beschreibung, Bildern.**
- **Keine Reparatur des Bestands.** Die vier falschen Objekte räumt Steffen
  von Hand weg. Eine Korrektur über das Bearbeiten-Formular legte einen
  Preisverlaufseintrag an, und ein Rückgang von 323.553 auf 290.000 sähe aus
  wie eine Preissenkung — das wichtigste Kaufsignal des Werkzeugs, belegt mit
  einem Korrekturwert.

## Zeugen

Es gilt durchgehend:

- Zeugen prüfen **Elemente und Klassenlisten**, nie `class="…"`-Zeichenketten.
- Ein Zeuge, der die Testumgebung misst statt die Zusage, gilt als blind.
- Die Datenform muss den geprüften Codepfad erzwingen.

Zu bewachen sind mindestens:

1. Alle fünf oben genannten Titel ergeben den jeweils richtigen Preis.
2. **Der echte Fehlerfall:** Eine Immowelt-Seite, deren Titel 290000 € nennt
   und deren Fließtext 323.553 € enthält, ergibt **290.000 €**. Dieser Zeuge
   muss fallen, wenn das alte Textmuster für Immowelt wieder greift.
3. Titel ohne `€`-Zahl → kein Preis. Das Objekt ist trotzdem anlegbar.
4. Titel mit zwei `€`-Zahlen → kein Preis. Das Objekt ist trotzdem anlegbar.
5. Der Riegel fällt **nicht** auf das Fließtext-Muster zurück: Auch wenn im
   sichtbaren Inhalt ein Preis steht, bleibt das Feld bei null oder mehreren
   Titel-Zahlen leer.
6. Für die anderen Portale ist das Fließtext-Muster unverändert wirksam.
   Dieser Zeuge muss fallen, wenn die neue Regel versehentlich global greift.
7. Wohnfläche und Zimmerzahl kommen bei Immowelt unverändert aus dem
   bisherigen Verfahren — die Zahl vor `m²` im Titel wird **nicht** als
   Wohnfläche gelesen.
8. Ein Preis von 0 im Titel wird gegen `is not None` behandelt, nicht gegen
   den Wahrheitswert.

## Gegenprobe

Die übliche Sabotage: jede Zusage einzeln brechen, prüfen, ob ein Zeuge fällt.

Besonders zu prüfen sind **Zeuge 2 und Zeuge 5**. Beide bewachen den Riegel,
und beide lassen sich leicht so bauen, dass sie strukturell passieren, ohne
den Codepfad je zu durchlaufen — etwa mit Testdaten, deren Fließtext gar
keinen Preis enthält. Dann wäre grün, dass kein falscher Wert ankommt,
obwohl nie geprüft wurde, dass der falsche Wert ignoriert wird.

Die Testdaten für Zeuge 2 und 5 müssen **beide** Werte enthalten, den
richtigen im Titel und den falschen im Fließtext.

## Vor dem Bau melden

Weicht der Aufbau des Lesezeichen-Skripts oder der Übernahme von dieser
Spezifikation ab: **halten und berichten**, nicht auf Annahmen weiterbauen.

Insbesondere: Findet sich keine Stelle, an der die Preisquelle je Portal
unterschieden werden kann, ohne die Struktur zu verbiegen — berichten, bevor
gebaut wird.

## Nach dem Bau berichten

- Testzahl vorher und nachher
- Erzeugte Migrationen (hier sollten keine anfallen)
- Jede Abweichung von dieser Spezifikation
- Jede Entscheidung, die die Spezifikation offengelassen hat
- Ergebnis der Gegenprobe, mit Zahl der Sabotagen und gefundenen blinden
  Zusagen
- **Ob das Lesezeichen-Skript selbst geändert werden musste.** Falls ja, muss
  Steffen das Lesezeichen im Browser neu setzen — das ist keine Sache des
  Deploys und geht sonst unter.
