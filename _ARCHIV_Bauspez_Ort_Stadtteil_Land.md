# Bauspezifikation — Ort, Stadtteil und Land aus Titel und URL

> Ergänzt `_ARCHIV_Bauspezifikation_Schritt2_Bookmarklet.md`, dort Abschnitt 2.3 und 3.1.
> Die dortigen Festlegungen gelten unverändert weiter, **außer** an der unter 0 genannten
> Stelle. **Stand:** 14.09.2026

---

## 0. Was sich gegenüber der bisherigen Festlegung ändert

Abschnitt 2.3 der Bookmarklet-Spezifikation schließt den Ort ausdrücklich aus: er stehe
„ohne verlässliche Auszeichnung im Titel". `03_Technik.md` führt dieselbe Festlegung.

**Für Idealista ist das widerlegt.** Der Ort steht im `og:title`, an acht Inseraten
belegt, in einem Aufbau, der sich ohne Raten zerlegen lässt. Damit gilt hier dieselbe
Lage wie beim Immowelt-Preis seit dem 07.09.: keine geratene CSS-Auswahl im Markup,
sondern ein Meta-Feld, das das Lesezeichen ohnehin liest.

Die Festlegung bleibt für **Region, Baujahr, Objekttyp, Grundstücksgröße und Zustand**
unverändert bestehen. Sie stehen im Fließtext, nicht im Titel.

**Diese Änderung gehört beim nächsten Tagesabschluss in `02_Datenmodell.md` und
`03_Technik.md`.** Bis dahin sind beide Dateien an dieser Stelle überholt.

### Die Belege

Acht Idealista-Titel, erhoben am 14.09.2026, deutsche Sprachfassung, ohne
Browser-Übersetzung:

```
Wohnung zu verkaufen in Calle San Pancracio, 5, Zona Puerto Deportivo, Fuengirola — idealista
Wohnung zu verkaufen in Calle Colina Blanca, 4, Torreblanca del Sol, Fuengirola — idealista
Penthouse zu verkaufen in CL Mirasierra, Centro Ciudad, Fuengirola — idealista
Wohnung zu verkaufen in Avenida de Mijas, 10, Centro Ciudad, Fuengirola — idealista
Wohnung zu verkaufen in Zona Puerto Deportivo, Fuengirola — idealista
Wohnung zu verkaufen in CL Maria Vega Sanchez, Zona Puerto Deportivo, Fuengirola — idealista
Landhaus zu verkaufen in Calle los Ángeles, 5, Puerto de Santiago, Santiago del Teide — idealista
Casa terrera zu verkaufen in Tamaimo-Arguayo, Santiago del Teide — idealista
```

Abgedeckt: zwei Gemeinden, vier Objekttypen, Titel mit und ohne Straße, mit und ohne
Hausnummer, städtisch und ländlich. **Nicht abgedeckt:** andere Provinzen, andere
Portale. Die Provinz kam in keinem der acht Titel vor — das ist der Befund, kein Beweis.

---

# Teil A — Das Feld `stadtteil`

## A1. Zweck

Das letzte Segment eines Idealista-Titels ist die **Gemeinde**, nicht die Lage. Bei zwei
der acht Belege steht die eigentliche Lage im Segment davor: `Puerto de Santiago` liegt
am Meer, `Tamaimo-Arguayo` im Landesinneren darüber, beide in der Gemeinde
`Santiago del Teide`. Ein einzelnes Ortsfeld wirft genau den Unterschied weg, der den
Preis erklärt — und `01_Konzept.md` nennt die Vergleichbarkeit auf einen Blick als
Hauptzweck der Liste.

## A2. Modelländerung

Neues Feld am `Objekt`:

| Feld | Typ | Anmerkung |
|---|---|---|
| `stadtteil` | wie `ort` | darf leer sein, kein Pflichtfeld |

Feldtyp, Länge und `blank`-Verhalten **identisch zum vorhandenen `ort`**. Im
Bearbeiten-Formular editierbar, unmittelbar vor `ort`. Keine neue Abhängigkeit.

### Die drei Ortsebenen

Damit sie nicht durcheinandergeraten — gehört so in `02_Datenmodell.md`:

| Feld | Bedeutung | Quelle |
|---|---|---|
| `stadtteil` | Lage innerhalb der Gemeinde | Titel, abgeleitet |
| `ort` | Gemeinde | Titel, abgeleitet |
| `region` | übergeordnete Ebene (Teneriffa, Costa del Sol) | Handarbeit, bleibt es |

## A3. Schema-Migration, keine Datenmigration

Eine reine Schema-Migration. **Der Bestand bleibt leer.**

Das ist ausdrücklich so gewollt und keine Auslassung: Die vorhandenen Objekte sind
Testdaten und werden vor dem Scharfstellen gelöscht. Eine Datenmigration, die
`stadtteil` rückwirkend aus gespeicherten Titeln zerlegt, wendet die Regel auf Daten an,
die niemand geprüft hat.

> **Hinweis, weil die Konstellation täuscht:** Die lokale Datenbank ist leer, die auf dem
> Server nicht. Am 07.09. war daraus abgeleitet worden, dass eine Datenmigration nötig
> ist — richtig damals, falsch hier. Der Unterschied ist, dass der Bestand diesmal
> ohnehin verworfen wird. Nicht aus dem Muster heraus doch eine bauen.

---

# Teil B — Ort und Stadtteil aus dem Titel

## B1. Wo das sitzt

**In der Übernahme, nicht im Lesezeichen.** Das Skript aus Abschnitt 2.2 der
Bookmarklet-Spezifikation wird **nicht angefasst**; es liefert den Titel bereits als
Parameter `titel`. Begründung wie am 07.09. bei der Preisregel: Ein geändertes Skript
muss auf jedem Gerät neu gesetzt werden.

**Die Zuordnung hängt an derselben Portaltabelle wie die Immowelt-Preisregel**, nicht an
einer Verzweigung in der View. Die Tabelle liegt vermutlich in `objekte/portale.py` — sie
ist dort zu suchen und die neue Zuordnung daneben zu legen, nicht neu zu erfinden. Findet
sich keine solche Tabelle, ist das eine Abweichung und **vor dem Bau zu melden**.

Gesetzt wird die Titelregel vorerst **nur für Idealista**.

## B2. Die Funktion

Neue reine Funktion in `objekte/portale.py`, neben `portal_und_id`:

```
def ort_und_stadtteil(titel: str, portal: str) -> tuple[str, str]
```

Gibt `(ort, stadtteil)` zurück, beide als `str`. Kein Datenbankzugriff, kein Netz — damit
aus dem Mail-Parser aus Schritt 3 gleichermaßen aufrufbar, wie es `portal_und_id` schon
ist.

Trägt das übergebene Portal in der Tabelle keine Titelregel, ist die Rückgabe `("", "")`.

## B3. Das Verfahren

1. **Am letzten Vorkommen von ` — ` trennen** (Geviertstrich U+2014, von je einem
   Leerzeichen umgeben). Der Teil davor ist der Arbeitstext. Fehlt der Trenner:
   `("", "")`.
2. **Im Arbeitstext die Marke ` zu verkaufen in ` suchen.** Der Text dahinter ist die
   Adresse. Fehlt die Marke: `("", "")`.
3. **Die Adresse am Komma in Segmente zerlegen**, jedes Segment an den Rändern von
   Leerzeichen befreien, leere Segmente verwerfen.
4. **Letztes Segment ist der Ort, vorletztes der Stadtteil.**
   - Genau ein Segment: Ort gefüllt, Stadtteil leer.
   - Kein Segment: beide leer.

Anders als bei `portal_und_id` ist ein halb gefülltes Paar hier **zulässig und richtig**:
Ein Titel ohne Stadtteil ist der Normalfall, und der Ort allein ist brauchbar. Die beiden
Werte hängen an keinem gemeinsamen Index.

## B4. Riegel

- **Ein Stadtteil-Kandidat, der nur aus Ziffern besteht, wird verworfen** — Stadtteil
  bleibt leer, der Ort bleibt gesetzt. Grund: Die Hausnummer ist ein eigenes
  Komma-Segment. Bei `Calle Mirasierra, 5, Fuengirola` wäre das Vorletzte die `5`.
- **Überschreitet ein Wert die Feldlänge, bleibt das betroffene Feld leer.** Nicht
  abschneiden.
- **Kein Rückfall auf eine andere Quelle.** Liefert das Verfahren nichts, bleiben die
  Felder leer.

Begründung für alle drei, wie am 07.09.: Ein leeres Feld sieht man, einen falschen nicht.

## B5. Einbau — nur im Vorbelegungspfad

Die Ableitung läuft **ausschließlich dort, wo `GET /uebernehmen/` das Formular aus den
Parametern vorbelegt.** Sie läuft **nicht** in `_zeigen()`.

Das ist der ausdrückliche Gegensatz zur Plausibilitätswarnung, die am 07.09. genau
umgekehrt nach `_zeigen()` verschoben wurde. Der Unterschied: Die Warnung redet über den
Wert, der gerade im Feld steht, und muss nach einem abgewiesenen POST erneut greifen. Die
Ableitung **schreibt** einen Wert — liefe sie in `_zeigen()`, überschriebe sie nach einem
aus anderem Grund abgewiesenen POST die Korrektur, die der Mensch gerade eingetragen hat.

**Die Vorbelegungsregel aus Abschnitt 3.1 gilt unverändert und wird nicht neu gebaut:**
Bei einem bestehenden Objekt gewinnt der Bestandswert, der abgeleitete Wert erscheint als
Hinweis unter dem Feld. Nichts wird stillschweigend überschrieben. Sobald `stadtteil` im
Formular steht, erbt es dieses Verhalten. Zu bezeugen, nicht zu implementieren.

---

# Teil C — Land aus der URL

## C1. Die Funktion

Das Land muss nicht gelesen werden. Es folgt aus dem Portal, das `portal_und_id` bereits
aus der URL ableitet. Zweite reine Funktion in `objekte/portale.py`:

```
def land_aus_portal(portal: str) -> str
```

| Portal | Land |
|---|---|
| `idealista` | ES |
| `fotocasa` | ES |
| `milanuncios` | ES |
| `pisos` | ES |
| `immoscout24` | DE |
| `immowelt` | DE |
| alles andere, einschließlich `""` | *leerer Rückgabewert* |

Die Rückgabe ist der Wert aus der Land-Auswahl in `02_Datenmodell.md`, nicht ein
Klartext. Bei leerem Portal bleibt das Feld leer — `sonstiges` ausdrücklich
eingeschlossen, wie schon bei `portal_und_id`.

## C2. Einbau

**In `GET /uebernehmen/`:** im selben Vorbelegungspfad wie Teil B, mit derselben Regel
(Bestandswert gewinnt).

**In `objekt_anlegen()`:** Die Schnellerfassung kennt die URL und damit das Portal, aber
keinen Titel. Dort wird das Land beim Anlegen gesetzt, Ort und Stadtteil bleiben leer.
Der Aufruf gehört an dieselbe Stelle wie `portal_und_id` — nach der Validierung, vor der
Dublettenprüfung.

---

# Teil D — Darstellung

**Liste.** Der Stadtteil kommt in die Unterzeile beim Titel, **vor** dem Ort. Die
Reihenfolge ist damit Stadtteil · Ort · Region · Land · Zustand, von fein nach grob. Er
entfällt einzeln, wenn leer — wie die übrigen Bestandteile der Unterzeile, Entscheidung
vom 05.09.

**Objektansicht.** Stadtteil erscheint bei den Daten, auch wenn leer, dargestellt wie die
übrigen leeren Felder. Entscheidung vom 03.09.: In der Objektansicht ist ein leeres Feld
die Aufforderung, es zu füllen.

Keine Stylesheet-Änderung nötig. Wird eine gebraucht, ist das eine Abweichung und zu
melden.

---

# Teil E — Zusagen und Gegenprobe

Nach jeder Runde `make test`. Jede Zusage braucht einen Zeugen; ein Test, der auch nach
dem Ausbau der Zusage grün bleibt, zählt nicht.

## E1. Zusagen aus Teil B

1. `ort_und_stadtteil` zerlegt einen Titel mit vier Segmenten in Ort aus dem letzten und
   Stadtteil aus dem vorletzten Segment.
2. Ein Titel mit zwei Segmenten liefert beide Werte.
3. Ein Titel mit genau einem Segment liefert den Ort, der Stadtteil bleibt leer.
4. Ein Titel ohne ` — ` liefert `("", "")`.
5. Ein Titel ohne ` zu verkaufen in ` liefert `("", "")`.
6. Ein rein numerisches vorletztes Segment lässt den Stadtteil leer und den Ort gesetzt.
7. Ein Wert über der Feldlänge lässt das betroffene Feld leer, ohne zu kürzen.
8. Ein Portal ohne gesetzte Titelregel liefert `("", "")`, auch bei einem Titel im
   Idealista-Aufbau.
9. `GET /uebernehmen/` belegt Ort und Stadtteil aus dem übergebenen Titel vor.
10. Bei einem bestehenden Objekt mit gefülltem Ort ist das Formular mit dem
    **Bestandswert** vorbelegt, nicht mit dem abgeleiteten. Die beiden Werte müssen sich
    im Test unterscheiden, sonst passiert der Zeuge strukturell.

## E2. Zusagen aus Teil C

11. `land_aus_portal` liefert für `idealista` ES, für `immowelt` DE, für `""` einen
    leeren Wert.
12. `GET /uebernehmen/` belegt das Land aus der URL vor.
13. Ein Einwurf über die Schnellerfassung schreibt das Land ans Objekt und lässt Ort und
    Stadtteil leer.
14. Ein Einwurf auf einer Domain ohne Muster legt weiterhin ein Objekt an, ohne Land.

## E3. Zusagen aus Teil A und D

15. Die Migration legt `stadtteil` an und trägt an einem Bestandsobjekt **nichts** nach.
16. Die Liste zeigt den Stadtteil in der Unterzeile vor dem Ort und lässt ihn bei leerem
    Wert weg — zwei getrennte Zeugen.
17. Die Objektansicht zeigt den Stadtteil auch bei leerem Wert.
18. Das Bearbeiten-Formular nimmt eine Änderung am Stadtteil an und speichert sie.

## E4. Gegenprobe

Wie in den vorigen Runden: jede Zusage einzeln sabotieren und prüfen, ob ihr Zeuge
umfällt. Besonders zu prüfen sind **6, 8, 10 und 16** — das sind die vier, bei denen ein
Test plausibel aussieht und trotzdem blind sein kann.

Dazu die bekannten Fallen:

- **Bei Zusage 6** muss das vorletzte Segment der Testdaten tatsächlich numerisch sein.
  Fehlt der Fall in den Daten, bleibt der Zeuge nach dem Ausbau des Riegels grün.
- **Bei Zusage 10** müssen Bestandswert und abgeleiteter Wert verschieden sein.
- **Bei Zusage 16** wird auf Elementen und Klassenlisten geprüft, nie auf
  `class="…"`-Zeichenketten — die Falle vom 05.09.
- **Behauptungen über eine Seite werden auf deren eigenen Inhalt eingegrenzt.** Ein
  Ortsname steht auch im Seitentitel; ein Zeuge, der gegen die ganze Antwort prüft, ist
  blind.

---

# Teil F — Reihenfolge und Abgrenzung

1. Teil A (Modell und Migration). `make test`.
2. Teil B und C (beide Funktionen in `portale.py` samt Einbau), mit Zusagen 1 bis 14.
   `make test`.
3. Teil D (Darstellung), mit Zusagen 16 bis 18. `make test`.

Teil B und C zusammen, weil beide dieselbe Stelle im Vorbelegungspfad anfassen und eine
getrennte Runde die Stelle zweimal umbaute. Teil D zuletzt, weil er ohne die Felder
nichts zu zeigen hat.

**Nicht Gegenstand dieser Runde:**

- **Der Objekttyp aus dem Titel.** Er steht dort sauber, aber `Casa terrera` ist in der
  deutschen Fassung unübersetzt geblieben und `Penthouse` ist kein Wert der Auswahl aus
  `02_Datenmodell.md`. Es bräuchte eine Zuordnungstabelle, die deutsche und
  stehengebliebene spanische Begriffe kennt.
- **Die Titelregel für andere Portale.** Für Fotocasa, Pisos, Milanuncios, ImmoScout24
  und Immowelt ist kein einziger Titel belegt. Nach der Lehre vom 07.09. wird nichts
  aufgenommen, was nicht belegt ist — sonst täuscht die Tabelle Abdeckung vor.
- **Das Zahlenformat des Preises.** Es ist als Verfahrensregel gelöst (deutsche
  Sprachfassung auf Idealista, keine Browser-Übersetzung) und kein Bauauftrag.
- Region, Baujahr, Objekttyp, Grundstücksgröße und Zustand als Auslesefelder.
- Der Mail-Parser, das Postfach, jeder Abruf einer Inseratsseite durch den Server.
- **Jede Änderung an `00_Master.md` bis `03_Technik.md`.**

---

## Was diese Runde offen lässt

- **Die Segmentregel ruht auf acht Titeln aus zwei Gemeinden.** Steht bei einem Inserat
  doch die Provinz hinten an, wandert die Gemeinde in den Stadtteil und die Provinz in
  den Ort. Das sähe plausibel aus. Korrigierbar im Bearbeiten-Formular und dort ohne
  Nebenwirkung — anders als beim Preis, wo eine Korrektur eine falsche Preissenkung
  erzeugt.
- **Die Marke ` zu verkaufen in ` ist sprachgebunden.** Auf der englischen oder
  spanischen Fassung greift sie nicht, die Felder bleiben leer. Das ist gewollt und der
  Grund für die Verfahrensregel zur Sprachfassung.
- **Der Titel kann aus `document.title` statt aus `og:title` stammen** — das Lesezeichen
  fällt laut Abschnitt 2.3 darauf zurück, und die Übernahme kann beides nicht
  unterscheiden. Dieselbe dünne Stelle wie bei der Immowelt-Preisregel.
- **Ob die Spracheinstellung bei Idealista am Konto oder am Browser hängt**, ist
  ungeklärt. Für die Verteilung der Zugänge ist das relevant, für diesen Bauschritt nicht.
