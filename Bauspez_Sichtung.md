# Bauspezifikation — Sichtung

> Objektradar. Stand 14.09.2026, Nachtrag 15.09.2026. Diese Datei liegt im
> Repo-Wurzelverzeichnis und wird nach Abnahme auf `_ARCHIV_` umbenannt.

---

## Nachtrag 15.09.2026

> Dieser Block ist nach der Abnahme entstanden und korrigiert die Spezifikation
> gegen eine Messung. **Der ursprüngliche Wortlaut weiter unten bleibt
> unverändert stehen** — er dokumentiert, was angeordnet war. Ohne ihn wäre
> nicht mehr nachvollziehbar, was diese Runde herausgefunden hat. Die
> betroffenen Stellen tragen einen Verweis hierher.

### Riegel 5 ist widerlegt und wird zur Stilregel

Unten steht, `~Q(person=person)` lasse „in SQL die NULL-Zeilen liegen", und die
Bedingung **müsse** NULL ausgeschrieben führen. Am 15.09. nachgemessen: **im
Django-ORM stimmt das nicht.**

Django hängt an jede negierte Gleichheit auf einer nullbaren Spalte selbst
einen NULL-Riegel an. Die Fassungen erzeugen dasselbe SQL:

| Fassung | erzeugtes SQL |
|---|---|
| `.exclude(person=person)` | `NOT (person_id = %s AND person_id IS NOT NULL)` |
| `filter(~Q(person=person))` | `NOT (person_id = %s AND person_id IS NOT NULL)` |
| `filter(Q(person__isnull=True) \| ~Q(person=person))` | `person_id IS NULL OR NOT (person_id = %s AND person_id IS NOT NULL)` |

Für die NULL-Zeile ist `NOT (unbekannt AND falsch)` **wahr** — sie bleibt in
jeder Fassung stehen. Gemessen an `Sichtung`, an `Statusaenderung` (ebenfalls
nullbar) und an `eingestellt_von`: überall trägt die NULL-Zeile die Marke, in
allen drei Fassungen.

Dasselbe gilt für die Dämpfung: `filter(person=None)` und
`filter(Q(person__isnull=True))` erzeugen beide `person_id IS NULL`, weil
Django einen `exact`-Lookup auf `None` intern zu `isnull` umschreibt.

**Folge:** Die ausgeschriebene Form ist ab sofort eine **Stilregel, kein
Riegel.** Sie hält keine Zeile, die nicht auch sonst bliebe. Sie bleibt
trotzdem stehen — als Regel, die man liest, statt als Nebenwirkung einer
ORM-Umschreibung, die sich mit einer Django-Version ändern kann. Tragen würde
sie erst, wenn die Abfrage einmal an Django vorbei gebaut wird: rohes SQL, eine
`RawSQL`-Annotation, ein anderes ORM.

### Sabotage 3 und 4 sind nicht gefallen — und das ist der Befund

Beide verlangen unten „muss fallen". Beide sind gelaufen, beide blieben grün:

| Sabotage | Eingriff | Ergebnis |
|---|---|---|
| 3 | Dämpfung auf `Q(person=person)` zusammengelegt | 53 Zeugen, grün |
| 4 | Besuchsmarkierung auf `.exclude(person=person)` | 48 Zeugen, grün |

**Das ist kein Mangel an Zeugen.** Es gibt nichts zu bezeugen: die Eingriffe
ändern das erzeugte SQL nicht und damit auch kein Verhalten. Ein Zeuge, der
hier fiele, müsste die Schreibweise des Quelltextes prüfen statt sein Ergebnis
— und stünde damit für eine Stilregel, nicht für einen Riegel. Beide Sabotagen
sind erledigt und nicht offen.

Was die Zeugen sehr wohl halten, bleibt unberührt: dass eine Sichtung ohne
Person eine Marke trägt, und dass NULL-Person und Person getrennte Tagestöpfe
der Dämpfung sind. Beides ist Verhalten und fällt, wenn man es antastet.

### Der Fund vom 05.09. ruhte auf derselben Prämisse

Die Stelle unten beruft sich auf den Fund vom 05.09. bei `eingestellt_von`.
Dieselbe Messung gilt dort: `~Q(eingestellt_von=person)` lässt die NULL-Zeile
**nicht** liegen, Django schreibt auch dort den Riegel selbst. In rohem SQL
wäre der Fund richtig gewesen; im ORM war er es nie. Der ausgeschriebene Zweig
bei `eingestellt_von` bleibt aus demselben Grund stehen wie oben.

### Was daraufhin im Code steht

- `mit_besuchsmarke.durch_andere()` führt seit dem 15.09. alle **vier**
  personengebundenen Bewegungsarten — Votum, Notiz, Statusänderung, Sichtung —
  in einer Form. Die ausgeschriebene Sonderfassung nur für die Sichtung ist
  zurückgebaut. Die NULL-Behandlung steht damit an einer Stelle statt an vier.
- Der Zeuge heißt jetzt `test_eine_sichtung_ohne_person_zaehlt_als_fremde_bewegung`
  und sagt in seinem Docstring ausdrücklich, dass er die **Schreibweise nicht
  bewacht** — nur, dass eine Sichtung ohne Person als fremde Bewegung zählt.
- Riegel 6 bleibt ein echter Riegel: Sabotage 2 fällt, drei Zeugen fangen sie
  (`test_die_daempfung_rechnet_in_der_zeitzone_der_anwendung`,
  `test_dasselbe_ohne_person`, `test_das_modell_ruft_date_today_nirgends_auf`).
  Die Messung lief auf einem Rechner in `Asia/Bangkok` gegen eine Anwendung in
  `Europe/Berlin` — fünf Stunden Versatz, also nicht im blinden Fleck.

---

## Zweck

`zuletzt_gesehen` beantwortet die Zusage aus `01`: „Ist es noch am Markt oder
schon weg." Heute wird das Feld an genau einer Stelle gesetzt — im
Übernahme-POST, und dort nur beim Ergänzen eines Bestandsobjekts. Der
Schnelleinwurf auf ein bekanntes Inserat, der häufigste Weg ein Inserat
wiederzusehen, lässt es unberührt. Der Hilfetext am Modell behauptet etwas
anderes. Das Feld ist damit eine Auskunft, die seltener stimmt, als sie
aussieht.

Gebaut wird eine eigene Tabelle `Sichtung` — ein Eintrag je bestätigter
Existenz des Inserats, mit Person und Quelle. `zuletzt_gesehen` wird zur
Projektion dieser Tabelle.

## Was NICHT gebaut wird

- **Kein Serverabruf**, der prüft, ob das Inserat noch existiert. Bei Idealista
  mit 403 belegt gescheitert, per Entscheidung vom 29.08. ausgeschlossen.
- **Kein Negativbeleg.** Die Tabelle trägt nur „Inserat existiert". Wer
  nachsieht und nichts findet, setzt den Status auf `vom Markt` — dieser
  Wechsel wird seit dem 28.08. mit Person und Datum protokolliert.
- **Kein Knopf in der Liste.** Die Liste ist kein Aktionsort; ein Knopf je
  Zeile im Blocklayout ist am Handy nicht vertretbar.
- **Keine Sichtung aus Ansehen, Bearbeiten, Votum oder Notiz.** Das sagt etwas
  über die Gruppe, nichts über das Inserat.

## Vor dem Bau melden

1. Den Wert von `TIME_ZONE` aus `settings.py`. Die Dämpfung hängt an „heute",
   und das muss `timezone.localdate()` sein, nicht `date.today()`.
2. Welches `on_delete` die `person`-Beziehung bei `Votum` und `Notiz` trägt.
   `Sichtung.person` wird angeglichen.
3. In welchem Block der Objektansicht `zuletzt_gesehen` heute ausgegeben wird
   (`templates/objekte/objekt.html:513`).

Erst danach bauen.

---

## Datenmodell

Neue Tabelle `Sichtung`:

| Feld | Typ | Anmerkung |
|---|---|---|
| `objekt` | FK auf `Objekt`, `CASCADE` | wie die fünf bestehenden abhängigen Tabellen |
| `person` | FK auf `konten.Person`, **nullbar**, `on_delete` wie bei `Votum`/`Notiz` | NULL ist der Regelfall ab Schritt 3 |
| `zeitpunkt` | `DateTimeField`, `auto_now_add=True` | genau **ein** Zeitfeld |
| `quelle` | Auswahl: `lesezeichen` · `von_hand` · `suchagent` | analog `Preisverlauf.quelle` |

`Meta.ordering = ("-zeitpunkt", "-id")` — zweites Kriterium zwingend, sonst ist
die Reihenfolge bei gleichem Zeitstempel unbestimmt.

### Warum nur ein Zeitfeld

`Preisverlauf` trägt zwei (`datum` und `erfasst_am`), weil dort die
Besuchsmarkierung gegen das eine und die Preisanzeige gegen das andere läuft —
die Mail nennt gestern, der Abruf läuft heute früh. An der Sichtung hängt kein
solches Signal. Ob eine Agenten-Mail von gestern als „gestern" oder „heute früh"
gesehen zählt, ändert nichts. `auto_now_add` verhindert zugleich, dass der Wert
von außen gesetzt wird.

### Warum `person` nullbar ist

Ab Schritt 3 ist die stärkste Sichtung die ohne Menschen: Meldet eine
Agenten-Mail ein bekanntes Objekt erneut, existiert das Inserat nachweislich.
Dieselbe Lage wie beim Preisverlauf (Entscheidung 05.09.).

---

## Die Methode

Sichtungen werden **ausschließlich** über `Objekt.sichtung_eintragen(person=None,
quelle=…)` angelegt. Kein direktes `Sichtung.objects.create()` im
Anwendungscode.

Ablauf:

1. Prüfen, ob für dieses Objekt **heute** (`timezone.localdate()`) bereits ein
   Eintrag derselben Person existiert. NULL-Person zählt als eigener Fall.
2. Wenn ja: **nichts tun.** Kein neuer Eintrag, `zuletzt_gesehen` bleibt stehen.
3. Wenn nein: Eintrag anlegen, danach `zuletzt_gesehen` auf den Zeitpunkt des
   jüngsten Eintrags setzen und speichern.

### Warum gedämpft wird — und für alle Quellen

Fünf Klicks erzeugen sonst fünf Einträge, die nichts Zweites sagen, und blähen
die Löschbestätigung auf. Die Dämpfung gilt **quellenübergreifend**: Wer
morgens per Lesezeichen einwirft und nachmittags auf „geprüft" klickt, erzeugt
einen Eintrag. Die zweite Bestätigung desselben Tages durch dieselbe Person ist
keine neue Information.

Folge, die so gewollt ist: Wird ein Eintrag unterdrückt, wandert auch
`zuletzt_gesehen` nicht. Feld und Tabelle laufen nie auseinander.

### `zuletzt_gesehen` als Projektion

Das Feld bekommt `editable=False` und wird nur in dieser Methode geschrieben —
dasselbe Muster wie `aktueller_preis` gegenüber dem Preisverlauf
(Entscheidung 28.08.).

Bekannte Grenze, unverändert: `editable=False` ist reine Django-Schicht, ein
`.update()` umgeht sie lautlos. Das gilt beim Kaufpreis genauso und wird
hingenommen.

Der Hilfetext am Feld wird korrigiert. Er sagt heute „wird ab Schritt 2 beim
erneuten Abruf gefüllt" und beschreibt damit etwas, das der Code nicht tut.

---

## Änderungen an bestehenden Stellen

| Stelle | Änderung |
|---|---|
| `views.py:1187` (`UebernehmenView.post`, `else`-Zweig) | Direkte Zuweisung raus, stattdessen `sichtung_eintragen(person=…, quelle="lesezeichen")` nach dem `save()` |
| `UebernehmenView.post`, `if neu`-Zweig | Sichtung **ebenfalls** eintragen. Behebt den Fehlstand vom 29.08.: Die Neuanlage über das Lesezeichen setzte das Feld bisher gar nicht |
| `objekt_anlegen()` (`views.py:679`) und Dublettenfall (`709-711`) | **Keine Sichtung.** Eine aus WhatsApp kopierte URL kann drei Wochen alt sein |
| `ObjektAdmin.readonly_fields` (`admin.py:82`) | `zuletzt_gesehen` aufnehmen. Alle Nachbarfelder derselben Gruppe sind geschützt, dieses nicht |
| Löschbestätigung | Zahl der Sichtungen nennen — wie Notizen, Preiseinträge und Statusänderungen. Eine Sichtung ist keine Wertung, die Verdeckungsregel greift nicht |
| Migration | Neue Tabelle. Dazu `zuletzt_gesehen` im Bestand auf NULL setzen (siehe unten) |

### Bestand

Die Migration **leert `zuletzt_gesehen` für alle vorhandenen Objekte.** Sonst
trüge ein Altobjekt ein Datum, zu dem keine Sichtung existiert — Feld und
Tabelle widersprächen sich ab Tag eins.

Kein Informationsverlust: Die Objekte in der Liste sind Testdaten und werden
vor dem Scharfstellen ohnehin geleert (Entscheidung 14.09.).

---

## Oberfläche

### Knopf

In der **rechten Spalte der Objektansicht** — sie trägt seit dem 14.09. „das
Handeln", die linke die Information. Beschriftung: **„Inserat geprüft — noch
da"**.

- **POST mit CSRF-Token**, eigene URL, danach Umleitung zurück auf die
  Objektansicht (POST-Redirect-GET) mit Meldung.
- Ein GET auf diese Adresse legt **nichts** an.
- Hat dieselbe Person heute bereits gesichtet, meldet die Antwort das ruhig,
  ohne Fehlerfarbe. Die Meldungsstufe ist der neutrale Hinweis, nicht
  `--fehler` und nicht `--warnung`.

Der Verweis „zum Inserat" steht bereits im Kopf der Objektansicht. Der Ablauf
ist: hinsehen, zurückkommen, bestätigen.

### Anzeige

An der Stelle, an der `zuletzt_gesehen` heute ausgegeben wird, steht künftig
**Zeitpunkt und Person der jüngsten Sichtung**, gelesen aus der Tabelle, nicht
aus dem Feld. Bei einer Sichtung ohne Person (Suchagent) steht die Quelle
statt des Namens.

Gibt es keine Sichtung, steht der Platzhalter — die Regel „in der Objektansicht
werden leere Felder angezeigt" (Entscheidung 03.09.) gilt.

**Nur die jüngste, keine Liste.** Bei wöchentlicher Kontrolle wären es nach
drei Monaten zwölf Zeilen, die niemand liest.

---

## Besuchsmarkierung

Eine Sichtung zählt als Bewegung im Sinne von „seit deinem letzten Besuch".
Damit ist erkennbar, dass jemand nachgesehen hat — das verhindert, dass drei
Leute dasselbe Inserat am selben Tag prüfen.

Verglichen wird `zeitpunkt` gegen die Besuchsschwelle.

**Die eigene Bewegung setzt die Marke nicht** (Entscheidung 05.09.). Anders als
beim Preisverlauf ist das hier durchsetzbar, weil eine Person am Eintrag hängt —
außer bei den Einträgen aus dem Mail-Parser, und genau die sind die Falle:

Ein `~Q(person=person)` lässt in SQL die NULL-Zeilen liegen. Die Bedingung
**muss** NULL ausgeschrieben führen. Identisch zum Fund vom 05.09. bei
`eingestellt_von`; ausgerechnet die Sichtungen aus dem künftigen Mail-Parser
trügen sonst nie eine Marke.

> **Widerlegt am 15.09.2026 — siehe „Nachtrag 15.09.2026" oben.** Django setzt
> den NULL-Riegel selbst; die NULL-Zeile bleibt in jeder Fassung stehen. Der
> Absatz steht hier unverändert, weil er festhält, was angeordnet war.

---

## Riegel

1. Kein Schreibzugriff auf `zuletzt_gesehen` außerhalb von
   `sichtung_eintragen()`. `editable=False` gesetzt.
2. Kein `Sichtung.objects.create()` im Anwendungscode außerhalb der Methode.
3. Der URL-Einwurf legt keine Sichtung an — auch nicht im Dublettenfall.
4. Die Dämpfung behandelt NULL-Person ausgeschrieben.
   → **Stilregel seit 15.09.2026, kein Riegel. Siehe Nachtrag oben.**
5. Die Besuchsmarkierung behandelt NULL-Person ausgeschrieben.
   → **Stilregel seit 15.09.2026, kein Riegel. Siehe Nachtrag oben.**
6. „Heute" ist `timezone.localdate()`, nie `date.today()`.
7. Der Knopf ist POST mit CSRF. GET legt nichts an.
8. Sichtungen sind keine Wertung und werden **nicht** verdeckt.

## Zeugen

Je Riegel mindestens einer. Dazu:

- Der Zeuge für die Dämpfung misst die **Zahl der Einträge in der Tabelle**,
  nicht die Antwort der Seite. Zwei Klicks derselben Person am selben Tag
  ergeben einen Eintrag.
- Ein zweiter Zeuge belegt, dass zwei **verschiedene** Personen am selben Tag
  zwei Einträge ergeben — sonst misst der erste eine Dämpfung, die auch dann
  grün bliebe, wenn gar nichts angelegt würde.
- Der Zeuge für den URL-Einwurf misst, dass die Tabelle **leer bleibt**, und
  daneben einen Wächter, der belegt, dass derselbe Aufbau über das Lesezeichen
  einen Eintrag erzeugt. Ohne den zweiten misst der erste ein Fehlen.
- Der Zeuge für die Besuchsmarkierung braucht eine Datenform, die die
  NULL-Zeile erzwingt: mindestens ein Objekt, dessen einzige Sichtung ohne
  Person ist.
- Oberflächen-Zeugen lesen Adresse, Feldnamen und Knopfwerte **aus der
  ausgelieferten Seite**, statt sie selbst hinzuschreiben (Entscheidung 14.09.).
- Zeugen prüfen Elemente und Klassenlisten, nie `class="…"`-Zeichenketten
  (Entscheidung 05.09.).
- Behauptungen über die Objektansicht werden auf deren eigenen Inhalt
  eingegrenzt, nicht gegen die ganze Antwort geprüft (Entscheidung 03.09.).
- Keine zwei Testklassen gleichen Namens in derselben Datei — sie überschreiben
  einander lautlos (Entscheidung 14.09.).

## Sabotagen

Mindestens diese, jede einzeln, mit Befund:

1. Dämpfung entfernen → muss fallen.
2. Dämpfung auf `date.today()` umstellen → prüfen, ob ein Zeuge das fängt.
   Fängt ihn keiner, ist das zu berichten, nicht stillschweigend zu beheben.
3. NULL-Behandlung in der Dämpfung durch `person=person` ersetzen → muss fallen.
   → **Gelaufen am 15.09.2026, NICHT gefallen — das ist der Befund, kein
   Mangel. Siehe Nachtrag oben.**
4. NULL-Behandlung in der Besuchsmarkierung durch `~Q(person=person)` ersetzen
   → muss fallen.
   → **Gelaufen am 15.09.2026, NICHT gefallen — das ist der Befund, kein
   Mangel. Siehe Nachtrag oben.**
5. Sichtung im URL-Einwurf ergänzen → muss fallen.
6. Sichtung im `if neu`-Zweig der Übernahme entfernen → muss fallen.
7. `zuletzt_gesehen` nach dem Anlegen nicht mehr setzen → muss fallen.
8. CSRF-Token aus dem Knopf-Formular entfernen → muss fallen.
9. Knopf-View zusätzlich auf GET öffnen → muss fallen.
10. Die Anzeige auf das Feld statt auf die Tabelle umstellen → prüfen, ob ein
    Zeuge das fängt.

## Abnahme

Testlauf allein genügt nicht. Die Objektansicht wird nach dem Deploy am
Bildschirm angesehen, in mindestens zwei Breiten — drei Fehler der Runde vom
14.09. hätte kein Zeuge gefunden (Entscheidung 14.09.).

Ausdrücklich anzusehen: ein Objekt **ohne** jede Sichtung, weil dort der
Platzhalter greift und die rechte Spalte dann ihre kürzeste Form hat.
