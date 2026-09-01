# Bauspezifikation — Schritt 2: Bookmarklet-Zulauf

> Ergänzung zu `Bauspezifikation_Oberflaeche_Schritt1.md` und
> `Bauspezifikation_2a_und_Stylesheet.md`. **Stand:** 29.08.2026

---

## 0. Was sich gegenüber der bisherigen Planung geändert hat

`03_Technik.md` beschreibt Zulaufweg 1 als Abruf der Inseratsseite **durch den Server**,
mit JSON-LD als Datenquelle und Playwright als Ausweg bei Idealista. Beide Annahmen sind
widerlegt:

- Ein Serverabruf der Idealista-Seite liefert **HTTP 403** (DataDome), auch mit
  Browser-Kennung.
- Die geladene Seite enthält **kein** `application/ld+json` — im Browser gemessen,
  Ergebnis 0.

Playwright ist verworfen: es müsste eine aktive Schutzmaßnahme aushebeln, und das ist
etwas anderes als der einzelne menschliche Abruf, den `03_Technik.md` für zulässig
erklärt.

**Neuer Weg:** Die Seite wird von dem Browser gelesen, in dem sie ohnehin offen ist. Ein
Lesezeichen mit einer Zeile JavaScript sammelt die Felder und übergibt sie an das
Werkzeug. Es gibt keine Sperre zu überwinden, weil kein Automat abruft.

**Diese Änderung gehört beim nächsten Tagesabschluss in `03_Technik.md`.** Bis dahin
ist die Datei an dieser Stelle überholt.

---

## 1. Der Weg der Daten

Drei Stationen, bewusst so geschnitten:

1. **Lesezeichen** — liest die offene Seite, öffnet ein neues Fenster auf dem Werkzeug,
   die Felder als Query-Parameter. Legt nichts an.
2. **Vorschau** (`GET /uebernehmen/`) — zeigt, was gelesen wurde, als ausgefülltes
   Formular. Legt nichts an.
3. **Übernahme** (`POST /uebernehmen/`) — legt an oder ergänzt, mit Preisverlaufseintrag.

### Warum drei Stationen und nicht eine

**Der Anmeldezustand.** Das Sitzungs-Cookie steht auf `SameSite=Lax`. Bei einer
Top-Level-Navigation per GET wird es mitgeschickt, bei einem POST von fremder Seite
nicht. Ein direkter POST vom Inserat aus käme unangemeldet an. Der GET-Umweg löst das,
ohne dass an Cookie-Einstellungen oder am CSRF-Schutz gedreht werden muss.

**Der zweite Schritt ist eine echte Kontrolle, keine Formalie.** Die gelesenen Werte
stammen aus einer Heuristik über fremdes Markup. Der Mensch sieht vor dem Speichern, was
ankommt, und korrigiert. Damit ist ein Lesefehler ein Ärgernis und kein falscher
Datensatz.

**GET legt nichts an.** Ein Aufruf der Vorschau-URL — auch versehentlich, auch zweimal —
verändert nichts.

---

## 2. Das Lesezeichen

### 2.1 Ausgabe im Werkzeug

Neue Seite `GET /lesezeichen/`, im Kopf von `basis.html` verlinkt. Sie enthält:

- eine kurze Anleitung: Lesezeichenleiste einblenden, den Link hineinziehen, auf einer
  Inseratsseite anklicken;
- den fertigen Link zum Ziehen, dessen `href` das vollständige `javascript:`-Skript ist;
- denselben Text zusätzlich in einem `<textarea>` zum Kopieren, für Browser, in denen
  das Ziehen nicht geht.

**Die Zieladresse wird gerendert, nicht hartkodiert:** das Skript enthält
`request.build_absolute_uri(reverse("uebernehmen"))`. Damit stimmt das Lesezeichen
lokal auf Port 8347 genauso wie später auf dem VPS. Ändert sich die Adresse, zieht man
das Lesezeichen neu.

Auf der Seite steht als Hinweis, dass das Lesezeichen nur im Desktop-Browser
funktioniert.

### 2.2 Was das Skript tut

In dieser Reihenfolge:

1. Felder aus dem Dokument lesen (2.3).
2. Leere Felder weglassen.
3. Query-String bauen, jeder Wert `encodeURIComponent`.
4. `window.open(ziel + "?" + query, "_blank")`.

Mehr nicht. Kein `fetch`, kein `XMLHttpRequest`, kein Nachladen eines Skripts von außen:
alle drei scheitern an der Content-Security-Policy der Portalseiten oder an CORS.
`window.open` ist eine Navigation und von beidem nicht betroffen.

**Längengrenze.** Der zusammengebaute Query-String wird auf **6000 Zeichen** begrenzt.
Wird er länger, fallen zuerst die Bild-URLs weg, dann die Beschreibung. Grund: Server
und Proxys begrenzen die Anfragezeile typischerweise auf 8 KB, und ein abgeschnittener
Aufruf wäre ein stiller Fehler.

Das Skript wird als eine Zeile ausgeliefert, `javascript:(function(){…})();`. Es darf
keine Anführungszeichen enthalten, die das `href`-Attribut sprengen — im Template mit
`{{ … }}` ausgeben, damit Django escapet, und im Skript selbst nur einfache
Anführungszeichen verwenden.

### 2.3 Welche Felder gelesen werden

**Zuerst die generischen Quellen.** Sie sind stabiler als jede Klassenauswahl, weil
Portale sie für Suchmaschinen und Social-Media pflegen:

| Feld | Quelle |
|---|---|
| `url` | `location.origin + location.pathname` — ohne Query, ohne Fragment |
| `titel` | `meta[property="og:title"]`, ersatzweise `document.title` |
| `beschreibung` | `meta[property="og:description"]`, auf 1000 Zeichen gekürzt |
| `bilder` | alle `meta[property="og:image"]`, höchstens 5 |

**Dann die Zahlen, per Texterkennung über den sichtbaren Seitentext.** Portalspezifische
CSS-Auswahlen sind hier bewusst **nicht** vorgegeben: das Markup ist nicht dokumentiert,
und geratene Auswahlen brechen unbemerkt. Stattdessen:

- **Preis:** im Text von `document.body` nach `\d{1,3}(?:[.\s]\d{3})+\s*€` suchen, alle
  Treffer sammeln, den **größten** nehmen. Begründung: Nebenzahlen auf Inseratsseiten
  (Nebenkosten, Monatsrate, Preis pro Quadratmeter) sind kleiner als der Kaufpreis.
- **Wohnfläche:** erster Treffer auf `(\d{2,5})\s*m²`, sofern der Wert zwischen 10 und
  10000 liegt.
- **Grundstücksgröße:** wird **nicht** gelesen. Sie ist von der Wohnfläche im Fließtext
  nicht sicher zu unterscheiden; ein verwechselter Wert wäre schlimmer als ein leerer.
- **Zimmer:** erster Treffer auf `(\d{1,2})\s*(?:bed|hab|Zimmer|Schlafzimmer)`, Wert
  zwischen 1 und 20.
- **Baujahr, Ort, Region, Objekttyp, Zustand:** werden **nicht** gelesen. Ort und Region
  stehen ohne verlässliche Auszeichnung im Titel; Objekttyp und Zustand sind Auswahlen,
  die eine Fehlzuordnung teuer macht.

**Kein Feld wird geraten.** Was die Muster nicht sicher treffen, bleibt leer und wird im
Vorschauformular von Hand ergänzt.

**Keine Kontaktdaten.** Maklername, Telefonnummer und Inserentendaten werden nicht
gelesen, nicht übertragen und nicht gespeichert — auch dann nicht, wenn sie im Markup
stehen. Das ist die Festlegung aus `03_Technik.md` und gilt hier unverändert.

---

## 3. Vorschau und Übernahme

### 3.1 `GET /uebernehmen/`

Erwartet die Parameter aus 2.3. `url` ist Pflicht; fehlt sie, Fehlermeldung und Umleitung
auf die Liste.

Die URL durchläuft **dieselbe Behandlung wie beim Einwurf**: `mit_schema()`,
Längenprüfung, `URLValidator`, dann `portal_und_id()`. Keine zweite Wahrheit — die
vorhandenen Funktionen werden aufgerufen, nicht nachgebaut.

Dann die zweistufige Dublettensuche aus `objekt_anlegen()`, ebenfalls durch Aufruf der
vorhandenen Funktionen.

Gerendert wird ein Formular mit allen Feldern aus `02_Datenmodell.md`, die von Hand
pflegbar sind, vorbelegt mit den gelesenen Werten:

- **Kein Treffer:** Überschrift „Neues Objekt übernehmen". Alle Felder aus den
  Parametern vorbelegt.
- **Treffer:** Überschrift „Objekt ergänzen", Link auf die vorhandene Objektansicht.
  Vorbelegt wird mit dem **Bestandswert**, wo einer da ist, sonst mit dem gelesenen Wert.
  Weicht ein gelesener Wert vom Bestandswert ab, steht er als Hinweis unter dem Feld
  („gelesen: 750.000 €"), damit die Person ihn bewusst übernehmen kann. **Nichts wird
  stillschweigend überschrieben.**

Die versteckten Felder tragen `url`, `portal`, `inserats_id` und die Bild-URLs.

### 3.2 `POST /uebernehmen/`

Same-origin, normaler CSRF-Schutz, kein `csrf_exempt`.

**Bei neuem Objekt:** anlegen wie in `objekt_anlegen()`, mit
`quelle=Quelle.URL_EINGEWORFEN`, `portal`, `inserats_id`,
`eingestellt_von=request.user`. Der `IntegrityError`-Fang aus Teil A der vorigen
Spezifikation gilt hier genauso.

**Bei bestehendem Objekt:** die übermittelten Felder setzen, `zuletzt_geaendert_von` und
`zuletzt_geaendert_am` fortschreiben, `zuletzt_gesehen` auf heute.

**Der Kaufpreis läuft ausschließlich über den Preisverlauf** — er ist am Objekt nicht
direkt beschreibbar. Ein Eintrag wird angelegt, wenn ein Preis übermittelt wurde **und**
er vom jüngsten Eintrag abweicht. Quelle des Eintrags: `erneuter Abruf`. Wurde kein
Preis übermittelt oder ist er unverändert, entsteht **kein** Eintrag — ein Verlauf aus
identischen Werten ist kein Verlauf.

**Bilder:** übermittelte Bild-URLs anlegen, die am Objekt noch nicht vorhanden sind.
Vorhandene werden nicht gelöscht und nicht doppelt angelegt.

Danach Umleitung auf die Objektansicht mit `messages.success`. Anders als beim Einwurf,
der auf die Liste zurückführt: hier hat die Person gerade Daten geprüft und will sehen,
was daraus wurde.

### 3.3 Fehlerverhalten

Schlägt etwas fehl, sagt die Seite was und wie es weitergeht. Kein leerer Bildschirm,
keine Umleitung ohne Meldung. Fehlt die URL: „Kein Link übergeben. Öffne das Inserat und
klicke das Lesezeichen erneut."

---

## 4. Zwei Korrekturen aus der Sichtprüfung

### 4.1 Die Objektspalte kappen

Solange kein Titel da ist, gibt `Objekt.__str__` die volle URL aus und sprengt die
Spalte. Zwei Änderungen:

**Am Modell:** `__str__` gibt den Titel zurück, wenn einer da ist. Sonst, wenn Portal und
Inserats-ID gesetzt sind, `f"{get_portal_display()} · {inserats_id}"`. Sonst die URL wie
bisher.

**Im Stylesheet:** die Objektzelle bekommt ab 48rem `max-width: 22rem`,
`overflow: hidden`, `text-overflow: ellipsis`, `white-space: nowrap`. In der
Kartenansicht unter 48rem bleibt der Umbruch erlaubt — dort ist Platz.

### 4.2 Fehlerfarbe von der Preissenkung trennen

`--signal` (`#B4531F`) bleibt der Preissenkung vorbehalten und wird weiterhin nicht
verwendet. Für Fehlermeldungen kommt `--fehler: #8C2F2F` dazu; die Meldungsregel in
`objektradar.css` wird darauf umgestellt.

Begründung: Ein Tippfehler im Formular darf nicht so aussehen wie das wichtigste
Kaufsignal des Werkzeugs.

---

## 5. Zusagen

Nach jeder Runde `make test`. Jede Zusage braucht einen Zeugen, der beim Ausbau umfällt.

1. `GET /uebernehmen/` ohne Anmeldung leitet auf den Login um.
2. `GET /uebernehmen/` legt **kein** Objekt an — auch nicht bei vollständigen Parametern.
   Zu bezeugen über die Objektzahl vor und nach dem Aufruf.
3. `GET /uebernehmen/` ohne `url` meldet den Fehler und leitet um.
4. `GET /uebernehmen/` erkennt ein bestehendes Objekt über Portal und Inserats-ID, auch
   bei abweichender URL-Schreibweise, und zeigt die Ergänzungs-Ansicht.
5. Bei bestehendem Objekt mit abweichendem Bestandswert ist das Formular mit dem
   **Bestandswert** vorbelegt, nicht mit dem gelesenen.
6. `POST /uebernehmen/` legt ein neues Objekt mit Portal, Inserats-ID und Quelle an.
7. Ein übermittelter Preis erzeugt genau einen Preisverlaufseintrag mit Quelle
   `erneuter Abruf`.
8. Ein Preis, der dem jüngsten Eintrag entspricht, erzeugt **keinen** zweiten Eintrag.
9. Kein übermittelter Preis erzeugt keinen Eintrag und löscht keinen bestehenden.
10. Übermittelte Bilder werden angelegt; ein zweiter Aufruf mit denselben Bildern legt
    keine Dubletten an.
11. `POST /uebernehmen/` ohne CSRF-Token wird abgewiesen.
12. `__str__` liefert den Titel, ersatzweise Portal und ID, ersatzweise die URL — drei
    getrennte Zeugen.
13. `GET /lesezeichen/` enthält die absolute Adresse des Übernahme-Endpunkts, aus der
    Anfrage abgeleitet und nicht hartkodiert. Zu bezeugen über zwei Aufrufe unter
    verschiedenem `HTTP_HOST`.

**Gegenprobe nach dem bisherigen Verfahren.** Besonders zu prüfen sind 2, 5, 8 und 13 —
das sind die, bei denen ein Test plausibel aussieht und trotzdem blind sein kann.

---

## 6. Reihenfolge

1. Abschnitt 4 (die beiden Korrekturen) — klein, sofort sichtbar, unabhängig vom Rest.
2. Abschnitt 3 (Vorschau und Übernahme) mit Zusagen 1 bis 12.
3. Abschnitt 2 (Lesezeichen-Seite und Skript) mit Zusage 13.

Abschnitt 3 vor Abschnitt 2, weil der Endpunkt ohne Lesezeichen prüfbar ist — man ruft
die URL von Hand mit Parametern auf. Umgekehrt ginge es nicht.

**Nicht Gegenstand dieser Runde:** Filter, Sortierung, Blättern; die
Aktivitäts-Markierung; die Votum-Übersicht in der Liste; der Mail-Parser; jeder Abruf
einer Inseratsseite durch den Server.

---

## 7. Was diese Runde offen lässt

**Die Trefferquote der Texterkennung ist unbekannt.** Die Muster in 2.3 sind an einer
einzigen Idealista-Seite entworfen und an keiner geprüft. Es ist damit zu rechnen, dass
Preis oder Fläche auf manchen Seiten danebengehen. Das ist eingeplant: die Vorschau
zeigt die Werte vor dem Speichern, und die Muster lassen sich nachschärfen, sobald
mehrere Seiten durchgelaufen sind. **Erst messen, dann verfeinern** — nicht umgekehrt.

**Portalspezifische Auswahlen fehlen bewusst.** Sobald sich zeigt, dass die generische
Erkennung bei einem Portal regelmäßig versagt, kann für dieses Portal eine gezielte
Auswahl ergänzt werden. Vorher wäre es geraten.
