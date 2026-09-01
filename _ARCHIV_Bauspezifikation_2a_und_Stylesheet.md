# Bauspezifikation — Schritt 2a und Punkt 7 (Stylesheet)

> Ergänzung zu `Bauspezifikation_Oberflaeche_Schritt1.md`. Die dortigen Abschnitte
> gelten unverändert weiter, **außer** an den unter 0 genannten Stellen.
> **Stand:** 29.08.2026

---

## 0. Klarstellungen zur bestehenden Spezifikation

Diese vier Punkte lösen Widersprüche auf, die beim Lesen des Ist-Stands aufgefallen
sind. Sie sind entschieden, nicht zur Diskussion gestellt.

1. **Das Login-Rate-Limit ist gewollt.** Abschnitt 12 der alten Spezifikation führt es
   unter „Was ausdrücklich NICHT gebaut wird". Diese Zeile ist überholt: das Limit wurde
   bewusst gebaut, weil die Cache-Lösung ohne neue Abhängigkeit und ohne Migration
   auskam. Nicht zurückbauen.
2. **Das Login-Template liegt richtig** unter `templates/konten/login.html`. Der Pfad
   `templates/registration/login.html` in Abschnitt 11 ist überholt. Zugangs-URLs und
   -Template bleiben in `konten`.
3. **Das Ergänzen eines fehlenden URL-Schemas in `mit_schema()` ist gewollt**, ebenso die
   Längenprüfung vor dem Validator. Beides bleibt.
4. **Die `BesuchMiddleware` bleibt vorerst unvollständig.** `request.neu_seit` und der
   `STATIC_URL`-Ausschluss gehören zur Markierung (Punkt 6), nicht hierher. Der
   `STATIC_URL`-Ausschluss wird **auch in dieser Runde nicht** nachgezogen, obwohl das
   Stylesheet ihn nahelegt — er wird zusammen mit Punkt 6 gebaut, damit er dort mit
   seinem Zeugen zusammen entsteht.

Nicht Gegenstand dieser Runde, obwohl beim Lesen aufgefallen: Die Objektliste zeigt
**keine Votum-Übersicht**, obwohl `02_Datenmodell.md` sie als Listenspalte führt. Sie
braucht bedingte `Count`-Annotationen und gehört damit zu Punkt 5. Das Stylesheet wird
so gebaut, dass eine zusätzliche Spalte später ohne Umbau passt.

---

# Teil A — Schritt 2a: Portal und Inserats-ID aus der URL

## A1. Zweck

Die Felder `portal` und `inserats_id` existieren am Modell, ebenso der partielle
Unique-Index darüber. Beide bleiben bis heute leer, weil erst der Parser aus Schritt 2
sie füllen sollte. Damit greift der Index nie, und der Dublettenschutz hängt allein am
URL-Vergleich — der bei Sprachpräfix, Tracking-Parametern oder einem zweiten Aufruf über
eine andere Domain danebengreift.

Beide Werte stehen jedoch bereits **in der URL selbst**. Sie lassen sich ohne einen
einzigen Seitenabruf ziehen. Das macht den Dublettenschutz echt, unabhängig von Schritt 2
und unabhängig von jeder Sperre auf Portalseite.

## A2. Neues Modul `objekte/portale.py`

Ein eigenes Modul, keine weitere Funktion in `views.py`. Begründung: Der Mail-Parser aus
Schritt 3 braucht dieselbe Logik, und eine View ist kein Ort, an dem ein Parser nachsieht.

```
def portal_und_id(url: str) -> tuple[str, str]
```

Gibt `(portal, inserats_id)` zurück, beide als `str`. **Wird das Portal nicht sicher
erkannt oder keine ID gefunden, ist die Rückgabe `("", "")` — niemals nur eines von
beiden.** Ein halb gefülltes Paar ist wertlos: der Index greift nur, wenn beide gesetzt
sind.

### Regeln

**Host bestimmen.** Mit `urllib.parse.urlsplit`. Aus `netloc` Port und Zugangsdaten
entfernen, in Kleinbuchstaben wandeln, ein führendes `www.` abschneiden.

**Idealista.** Host ist `idealista.com`, `idealista.it` oder `idealista.pt`, oder endet
auf `.idealista.com`, `.idealista.it`, `.idealista.pt`. Pfad passt auf
`^/(?:[a-z]{2}/)?inmueble/(\d+)/?$`. Portal ist `idealista`, ID ist die Ziffernfolge.

**ImmoScout24.** Host ist `immobilienscout24.de` oder endet auf
`.immobilienscout24.de`. Pfad passt auf `^/expose/(\d+)`. Portal ist `immoscout24`, ID
ist die Ziffernfolge. Der Pfad wird bewusst **nicht** bis zum Ende geprüft: ImmoScout24
hängt an die Expose-URL Fragmente und Unterpfade an.

**Alles andere** ergibt `("", "")`. Das schließt `sonstiges` ausdrücklich ein: für dieses
Portal gibt es kein bekanntes ID-Muster, und ein geratener Wert würde zwei verschiedene
Objekte kollidieren lassen.

**Keine weiteren Domains erfinden.** Andere Länderdomains von ImmoScout24 und weitere
Portale kommen erst dazu, wenn eine echte URL vorliegt, an der sich das Muster prüfen
lässt. Ein falsch erkanntes Paar ist schädlicher als ein leeres.

**Die Funktion ist rein:** kein Datenbankzugriff, kein Netz, keine Django-Importe außer
für nichts. Sie ist damit aus einer Migration und aus dem Mail-Parser gleichermaßen
aufrufbar.

## A3. Einbau in `objekt_anlegen()`

Die bestehende Reihenfolge in der View bleibt. Eingefügt wird **nach** der Validierung
(bisheriger Schritt 4) und **vor** der Dublettenprüfung:

```
portal, inserats_id = portal_und_id(url)
```

### Dublettenprüfung, zweistufig

**Stufe 1, stark — nur wenn beide Werte gesetzt sind:** Suche über
`Objekt.objects.filter(portal=portal, inserats_id=inserats_id)`, wie bisher über **alle**
Objekte, nicht über `sichtbar()`, sortiert `("eingestellt_am", "id")`, `.first()`.

**Stufe 2, schwach — nur wenn Stufe 1 nichts findet:** der bestehende URL-Vergleich aus
`dublette(url)`, unverändert. Er bleibt nötig für Objekte, die vor dieser Runde angelegt
wurden, für Portale ohne bekanntes Muster und für Idealista-URLs in Formen, die das
Muster nicht trifft.

Bei einem Treffer aus beiden Stufen bleibt das bisherige Verhalten: `messages.info("Das
Inserat liegt schon in der Liste.")` und Redirect auf das gefundene Objekt, ohne
anzulegen.

### Anlegen

`Objekt.objects.create(...)` bekommt zusätzlich `portal=portal,
inserats_id=inserats_id`.

### Wettlauf abfangen

Zwischen Prüfung und Insert kann ein zweiter Einwurf dasselbe Paar anlegen. Dann wirft
die Datenbank `IntegrityError` und die View liefert einen 500er. Das ist unnötig, weil
der Fall eine saubere Antwort hat:

```
try:
    objekt = Objekt.objects.create(...)
except IntegrityError:
    ...
```

Im `except`: Stufe 1 erneut ausführen. Wird ein Objekt gefunden, dieselbe
`messages.info`-Meldung und Redirect darauf — für die Person sieht es aus wie eine ganz
normal erkannte Dublette. Wird nichts gefunden, `messages.error("Das Inserat konnte
nicht angelegt werden.")` und Redirect auf die Liste. **Kein stilles Verschlucken:** der
zweite Fall darf nicht so aussehen, als sei etwas gespeichert worden.

`IntegrityError` kommt aus `django.db`. Der `create()`-Aufruf braucht dafür eine eigene
Transaktion oder muss der letzte Datenbankzugriff im Request sein — in einer offenen
Transaktion macht ein gefangener `IntegrityError` alle folgenden Abfragen unbrauchbar.
`transaction.atomic()` eng um den `create()`-Aufruf legen.

## A4. Bestandsobjekte nachtragen

Datenmigration `objekte/0003_portal_und_inserats_id_nachtragen.py`, `RunPython` mit
Rückwärtsfunktion `migrations.RunPython.noop`.

Sie importiert `portal_und_id` aus `objekte/portale.py`. Das koppelt die Migration an
Anwendungscode; das ist hier vertretbar, weil die Funktion rein und deterministisch ist
und ein späteres besseres Muster auch für Bestandsdaten das gewünschte Ergebnis wäre.

**Ablauf:** Über alle Objekte mit `portal=""` **oder** `inserats_id=""` iterieren.
`portal_und_id(objekt.url)` anwenden. Ergibt es `("", "")`, das Objekt überspringen.

**Vor dem Schreiben prüfen, ob das Paar schon vergeben ist** — sonst bricht die Migration
am Unique-Index ab und hinterlässt eine halb migrierte Datenbank. Ist es vergeben, das
Objekt unangetastet lassen. Doppelte Paare innerhalb desselben Laufs zählen mit: ein im
Lauf bereits vergebenes Paar gilt als vergeben.

Die Migration ist damit idempotent und läuft auch auf einer Datenbank durch, in der
bereits zwei Objekte auf dasselbe Inserat zeigen.

## A5. Was in Teil A nicht gebaut wird

- **Kein Abruf der Inseratsseite.** Weder per `requests` noch per Headless-Browser. Der
  einfache Abruf ist bei Idealista mit HTTP 403 belegt gescheitert; welcher Weg ihn
  ersetzt, ist offen und wird getrennt entschieden.
- **Keine Normalisierung der gespeicherten URL.** Sie wird weiterhin bis auf ein
  ergänztes Schema unverändert abgelegt. Portal und ID werden aus ihr *gelesen*, sie
  wird nicht nach ihnen umgeschrieben.
- **Keine Anzeige von Portal oder ID in der Oberfläche.** Beides ist Schlüssel, nicht
  Information für den Leser.

---

# Teil B — Punkt 7: Stylesheet

## B1. Die Lücke schließen, bevor irgendetwas aussieht

`static/objektradar.css` steht in Abschnitt 11 der alten Spezifikation, existiert aber
nicht, und **es ist nicht festgelegt, wie Django die Datei finden soll.** Der Pfad liegt
auf Projektebene; der `AppDirectoriesFinder` sieht dort nicht nach. Ohne die folgende
Zeile lädt das Stylesheet im Entwicklungsbetrieb nicht, und der Fehler ist stumm — die
Seite sieht einfach weiter unformatiert aus.

In `config/settings.py`, bei den vorhandenen Static-Einstellungen:

```
STATICFILES_DIRS = [BASE_DIR / "static"]
```

`STATIC_URL` und `STATIC_ROOT` bleiben wie sie sind.

In `templates/basis.html`: `{% load static %}` als erste Zeile nach `{% extends %}`
beziehungsweise ganz oben, und im `<head>`:

```
<link rel="stylesheet" href="{% static 'objektradar.css' %}">
```

Dazu, falls noch nicht vorhanden:

```
<meta name="viewport" content="width=device-width, initial-scale=1">
```

**Ohne diese Zeile ist „mobil zuerst" wirkungslos** — der Browser rendert sonst in
Desktop-Breite und skaliert herunter.

## B2. Gestalterische Festlegung

Kein CSS-Framework, kein Build-Schritt, keine Webfonts. Webfonts fallen zusätzlich aus,
weil ein Nachladen von fremden Servern eine Datenschutzfrage aufwirft, die dieses
Werkzeug nicht braucht.

Das ist ein Arbeitsgerät für fünf Personen, die Zahlen nebeneinanderlegen. Es soll ruhig
sein und Zahlen lesbar machen. Alles, was Farbe trägt, trägt Bedeutung.

**Farben** als CSS-Variablen auf `:root`, genau diese sechs:

| Variable | Wert | Rolle |
|---|---|---|
| `--papier` | `#FBFBF9` | Seitengrund |
| `--flaeche` | `#FFFFFF` | Karten, Tabellenzeilen |
| `--linie` | `#DEDCD5` | Rahmen, Trenner |
| `--text` | `#22262B` | Fließtext |
| `--gedaempft` | `#6B7178` | Nebenangaben, Spaltenköpfe, „—" |
| `--akzent` | `#2F5D62` | Links, Fokus, aktive Bedienelemente |

Ein siebter Wert `--signal` (`#B4531F`) ist reserviert für Preissenkungen und wird in
dieser Runde **definiert, aber nicht verwendet**. Er ist die einzige laute Farbe im
Werkzeug und bleibt dem wichtigsten Kaufsignal vorbehalten.

**Schrift.** Systemstack, zwei Rollen:

- Fließtext und Überschriften: `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`
- Zahlen in Tabelle und Karte: derselbe Stack, aber mit
  `font-variant-numeric: tabular-nums` und `font-feature-settings: "tnum"`.

Das ist der einzige typografische Kunstgriff, und er ist der wichtigste: ohne
Tabellenziffern stehen 750.000 und 89.500 nicht untereinander, und genau das Untereinander
ist der Zweck der Liste. Zahlenspalten werden **rechtsbündig** gesetzt, Textspalten links.

Überschriften bleiben in normaler Stärke bei ruhiger Größenstaffelung — `1rem` Grundtext,
`1.5rem` für `h1`, `1.125rem` für `h2`. Keine serifenbetonte Displayschrift, keine
Versalien-Eyebrows, keine Nummerierungen: die Liste ist keine Erzählung mit Reihenfolge.

## B3. Aufbau

**Mobil zuerst.** Alle Grundregeln gelten ohne Media Query; die Tabellendarstellung
kommt in einem `@media (min-width: 48rem)` dazu.

**Breite.** Ein Inhaltsrahmen mit `max-width: 78rem`, zentriert, `padding` seitlich
`1rem`. Die Objektliste darf breiter laufen als der Fließtext einer Objektansicht.

**Kopf.** Der Kopf aus `basis.html` als eine Zeile: Werkzeugname links, Person und
„abmelden" rechts, darunter eine Linie in `--linie`. Das Abmelden-Formular darf die Zeile
nicht umbrechen.

**Das Einwurf-Feld ist das wichtigste Bedienelement der Seite.** Es steht über der Liste,
das Eingabefeld nimmt die volle Breite, der Knopf sitzt darunter (mobil) beziehungsweise
daneben (ab `48rem`). Mindesthöhe `2.75rem` für Feld und Knopf, damit es am Daumen
bedienbar ist.

### Die Liste unter 48rem: Karten

Die vorhandene `<table>` bleibt als Markup bestehen und wird per CSS zu Karten
umgebrochen:

- `thead` wird ausgeblendet (`position: absolute; clip-path: inset(50%)` — **nicht**
  `display: none`, das nähme Screenreadern die Spaltenköpfe).
- `tr` wird `display: block`, mit Rahmen in `--linie`, Grund `--flaeche`, `border-radius`
  `4px`, Abstand darunter.
- `td` wird `display: flex` mit `justify-content: space-between`, links die Bezeichnung,
  rechts der Wert.

Die Bezeichnung kommt aus einem `data-spalte`-Attribut, das im Template an jede `<td>`
geschrieben wird, und wird per `td::before { content: attr(data-spalte); }` in
`--gedaempft` ausgegeben. **Das ist die einzige Template-Änderung in Teil B** — die
Spaltenreihenfolge und die Zellinhalte bleiben unangetastet.

Die Zelle „Objekt" mit dem Link steht in der Karte oben und ohne Bezeichnung: sie ist die
Überschrift der Karte, nicht eine Angabe unter anderen. Regel dafür:
`td:first-child::before { content: none; }`.

### Die Liste ab 48rem: Tabelle

Die Kartenregeln werden zurückgenommen (`display: table-row` beziehungsweise
`table-cell`), `thead` wird wieder sichtbar. Spaltenköpfe in `--gedaempft`, kleiner als
der Text, mit Linie darunter. Zeilen durch eine Linie in `--linie` getrennt, kein
Zebrastreifen. `border-collapse: collapse`.

Die Tabelle darf horizontal scrollen, wenn sie nicht passt: der umgebende Behälter
bekommt `overflow-x: auto`. Damit bricht eine später ergänzte Spalte das Layout nicht.

### Objektansicht, Formulare, Meldungen

- Formularzeilen untereinander, Beschriftung über dem Feld. Felder auf voller Breite,
  `max-width: 30rem`.
- Knöpfe: Grund `--akzent`, Schrift weiß, `border: none`, `border-radius: 4px`, Polsterung
  `0.6rem 1rem`. Die Votum-Knöpfe stehen nebeneinander und dürfen umbrechen.
- `messages`: Kasten über dem Inhalt, linker Balken 3px. Erfolg in `--akzent`, Fehler in
  `--signal`, Info in `--gedaempft`. Keine Symbole, keine Hintergrundfarbe außer einer
  sehr hellen Tönung.
- Der Fußtext der Objektansicht („eingestellt von … am …") in `--gedaempft`, kleiner.

### Qualitätsboden

Ohne Ausnahme einzuhalten:

- `:focus-visible` mit sichtbarem Umriss in `--akzent`, `outline-offset: 2px`. Der
  Standardumriss darf **nicht** ohne Ersatz entfernt werden.
- Anklickbare Flächen mindestens `2.75rem` hoch.
- Kein JavaScript. Die alte Spezifikation erlaubt wenige Zeilen für einen aufklappbaren
  Filterblock — den gibt es noch nicht, also gibt es auch das JavaScript nicht.
- `@media (prefers-reduced-motion: reduce)`: Übergänge auf `none`. Es gibt ohnehin
  höchstens Farbübergänge an Knöpfen.
- Keine festen Pixelhöhen an Textbehältern.

## B4. Leerer Zustand

Der vorhandene Text „Noch kein Objekt in der Liste." wird ersetzt durch:

> Noch kein Objekt in der Liste. Wirf oben den Link zu einem Inserat ein.

Ein leerer Bildschirm sagt, was als Nächstes zu tun ist. Der Satz steht in `--gedaempft`,
zentriert, mit Abstand.

---

# Teil C — Zusagen und Gegenprobe

Nach jeder Runde `make test`. Jede der folgenden Zusagen braucht einen Zeugen; ein Test,
der auch nach dem Ausbau der Zusage grün bleibt, zählt nicht.

## C1. Zusagen aus Teil A

1. `portal_und_id` erkennt eine Idealista-URL mit Sprachpräfix, ohne Sprachpräfix, mit und
   ohne abschließenden Schrägstrich, mit Query-Parametern.
2. `portal_und_id` erkennt eine ImmoScout24-Expose-URL, auch mit angehängtem Fragment.
3. `portal_und_id` gibt bei einer unbekannten Domain, bei einem passenden Pfad auf
   fremder Domain und bei fehlender Ziffernfolge `("", "")` zurück — **beide** Werte leer.
4. Ein Einwurf über die Oberfläche schreibt Portal und ID ans Objekt.
5. Zwei Einwürfe desselben Inserats über **verschieden geschriebene URLs** (Sprachpräfix
   verschieden, Tracking-Parameter angehängt) legen genau **ein** Objekt an und leiten
   beim zweiten Mal auf das bestehende um. Dieser Test ist der eigentliche Zweck der
   Runde.
6. Ein Einwurf auf einer Domain ohne Muster funktioniert weiterhin und legt ein Objekt mit
   leerem Portal an — der schwache URL-Vergleich trägt ihn.
7. Ein `IntegrityError` beim Anlegen führt zu einer Umleitung auf das bestehende Objekt,
   nicht zu einem 500er, und legt kein zweites Objekt an. Zu bezeugen, indem der
   Wettlauf nachgestellt wird, nicht durch bloße Codebetrachtung.
8. Die Datenmigration trägt an einem Bestandsobjekt Portal und ID nach.
9. Die Datenmigration lässt ein Objekt unangetastet, dessen Paar bereits vergeben ist, und
   bricht nicht ab.

## C2. Zusagen aus Teil B

10. `basis.html` verweist auf das Stylesheet, und die Datei ist über die
    Static-Files-Konfiguration auffindbar. Der Zeuge prüft die **Auffindbarkeit**, nicht
    nur das Vorkommen der Zeichenkette im Template — sonst bleibt er grün, wenn
    `STATICFILES_DIRS` wieder verschwindet.
11. Jede `<td>` der Objektliste trägt ein `data-spalte`-Attribut, dessen Wert dem
    zugehörigen Spaltenkopf entspricht. Ohne diesen Zeugen fällt eine später ergänzte
    Spalte in der Kartenansicht ohne Bezeichnung heraus, und niemand merkt es.
12. Der leere Zustand nennt den Einwurf.

## C3. Gegenprobe

Wie in den vorigen Runden: jede Zusage einzeln sabotieren und prüfen, ob ihr Zeuge
umfällt. Besonders zu prüfen sind 5, 7, 10 und 11 — das sind die vier, bei denen ein Test
plausibel aussieht und trotzdem blind sein kann.

Bei Zusage 5 zusätzlich prüfen, dass der Test **nicht** schon durch den alten
URL-Vergleich grün wird: die beiden URLs müssen sich so unterscheiden, dass `rstrip("/")`
sie nicht zusammenführt.

---

# Teil D — Reihenfolge und Abgrenzung

1. Teil A vollständig, mit Migration und Tests. `make test`.
2. Teil B vollständig. `make test`.

Teil A zuerst, weil er ohne Sichtprüfung im Browser vollständig bezeugbar ist. Teil B ist
umgekehrt der Teil, den kein Test wirklich abnimmt — dort ist der Blick auf den Bildschirm
das Abnahmekriterium, und der geschieht danach bei Steffen.

**Nicht Gegenstand dieser Runde:** Filter, Sortierung, Blättern (Punkt 5), die
Aktivitäts-Markierung samt `mit_aktivitaet()` und `request.neu_seit` (Punkt 6), die
Votum-Übersicht in der Liste, jeder Abruf einer Inseratsseite, jede Änderung an
`00_Master.md` bis `03_Technik.md`.
