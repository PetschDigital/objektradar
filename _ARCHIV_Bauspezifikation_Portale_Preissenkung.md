# Bauspezifikation — Portalmuster, Preissenkung, Nachrunde Tests

> Gilt für Objektradar. Zielzustand, nicht Diff.
> **Stand:** 02.09.2026 · Diese Datei liegt im Repo-Ordner.

---

## 0. Was feststeht und nicht verändert wird

**Nicht anfassen:**

- Der Dublettenschlüssel ist Portal + Inserats-ID, als partieller Unique-Index nur bei gesetzten Werten. Die zweistufige Prüfung — Schlüssel zuerst, URL-Vergleich als Rückfall — bleibt, wie sie ist
- Portal und Inserats-ID werden **aus der URL** abgeleitet, nie aus der Seite gelesen. Kein Seitenabruf, unter keinen Umständen
- `Preisverlauf` führt den Preis. Der Kaufpreis am Objekt ist eine Projektion und bleibt `editable=False`
- Ein Verlaufseintrag entsteht nur, wenn ein Preis übermittelt wird **und** vom jüngsten abweicht
- `--signal` ist der Preissenkung vorbehalten, `--fehler` den Fehlern
- Die Layout-Runde von heute ist abgeschlossen. Abstandsskala, Feldeinheit und Raster werden benutzt, nicht überarbeitet
- Redirect-Ziele, Feldnamen, Query-String-Erzeugung: unverändert

**Verboten:** neue Abhängigkeiten, JavaScript, Änderungen an der Bookmarklet-Logik.

**Anhalten und melden, nicht raten**, wenn:

1. Ein Bestandsobjekt beim Nachtragen des Schlüssels mit einem anderen kollidiert
2. Die Abfragezahl der Liste sich nicht konstant halten lässt (Abschnitt 2)
3. Diese Datei dem widerspricht, was du im Repo vorfindest

---

## 1. Portalmuster

### 1.1 Was rausfliegt

`idealista.it` und `idealista.pt` werden **entfernt**. Sie sind als Domain vorgesehen, haben kein Pfadmuster, laufen ins Leere und täuschen Abdeckung vor. Die Gruppe sucht in Spanien.

Prüfe vorher, ob Bestandsobjekte mit einer `.it`- oder `.pt`-URL existieren. Falls ja: melden, nicht stillschweigend umschreiben.

### 1.2 Was dazukommt

Drei Portale, jedes an einer echten URL belegt. Die Muster sind **sprachunabhängig** — dasselbe Objekt hat auf der deutschen und der spanischen Fassung einen anderen Pfad, aber dieselbe ID. Hängt das Muster am Sprachpräfix, legen zwei Personen dasselbe Objekt doppelt an.

**fotocasa** — `fotocasa.es`

```
https://www.fotocasa.es/de/kaufen/wohnimmobilie/marbella/aire-acondicionado-heizung-.../190346632/d
https://www.fotocasa.es/de/kaufen/wohnimmobilie/neubau/marbella/20561853/189207445
```

Die Inserats-ID ist die **letzte Zahl im Pfad**, wobei ein einzelner Buchstabe als letztes Segment ignoriert wird. Im ersten Beispiel `190346632`. Der Ausstattungspfad davor ist beliebig lang und darf nicht Teil des Musters werden.

Das zweite Beispiel ist ein Neubauprojekt und trägt zwei Zahlen. Es ist als Beleg für die Struktur aufgeführt, nicht als Zielfall — dort greift dieselbe Regel und liefert `189207445`.

**milanuncios** — `milanuncios.com`

```
https://www.milanuncios.com/venta-de-apartamentos-en-san-pedro-de-alcantara-malaga/marbella-607639645.htm
```

Die ID ist die Zahl nach dem **letzten Bindestrich** vor `.htm`, hier `607639645`.

**Dünne Stelle, ausdrücklich so berichtet:** Dieses Muster hängt an einem einzigen Beleg. Kleinanzeigenportale führen oft mehrere Anzeigentypen mit abweichenden Pfaden. Passt eine URL nicht, fällt sie auf „sonstiges" — das ist der richtige Ausgang, kein Fehler.

**pisos** — `pisos.com`

```
https://www.pisos.com/comprar/atico-cabopino_reserva_de_marbella-65035296319_108900/
https://www.pisos.com/promocion-los_pacos-6109286238_109700/
```

Die ID ist der **vollständige Block aus zwei durch Unterstrich getrennten Zahlen** am Pfadende, hier `65035296319_108900`.

Begründung, weil sie zählt: Die zweite Zahl ist in beiden Belegen sechsstellig und beginnt mit `10` — vermutlich eine Makler- oder Agenturkennung. Nähme man nur sie, trügen alle Objekte desselben Maklers denselben Schlüssel, und der Dublettenschutz wäre still tot. Nähme man nur die erste, drohen Kollisionen. Der ganze Block ist die sichere Wahl: Ist er zu breit gefasst, erscheint später eine Dublette, die keine ist — sichtbar und reparierbar. Der umgekehrte Fehler wäre unsichtbar.

### 1.3 Was bleibt

`idealista.com` mit `/inmueble/<id>/` und `immoscout24` bleiben unverändert. ImmoScout ist deutsch, aber das Muster funktioniert und täuscht daher keine Abdeckung vor.

### 1.4 Datenmodell und Bestand

Die Portal-Auswahl erweitert sich um `fotocasa`, `milanuncios`, `pisos`. Das ist eine Migration.

Dazu eine **Datenmigration für den Bestand**: Objekte ohne Dublettenschlüssel, deren URL zu einem der neuen Muster passt, bekommen Portal und ID nachgetragen.

**Regel bei Kollision, aus der Migration vom 29.08. übernommen:** Stehen zwei Objekte auf demselben Inserat, bekommt das **ältere** den Schlüssel. Ohne festgelegte Reihenfolge greift `Meta.ordering` absteigend, und dann liefe jeder künftige Einwurf auf das jüngere, während Vota und Notizen am älteren hängen.

Das Immowelt-Testobjekt bleibt in der Liste und bleibt „sonstiges". Das ist gewollt.

### 1.5 Zeugen

- Je Portal: eine echte URL → erwartetes Paar aus Portal und ID. Die vier oben stehenden URLs wörtlich als Testdaten
- Je Portal eine **Sprachvariante** derselben URL (`/de/` gegen `/es/` oder ohne Präfix) → gleiche ID
- `idealista.it` und `idealista.pt` → **nicht** als idealista erkannt, sondern „sonstiges"
- Eine unbekannte Domain → „sonstiges", kein Schlüssel, kein Fehler
- Die fotocasa-Neubau-URL → `189207445`, nicht `20561853`

---

## 2. Preissenkung in der Liste

### 2.1 Was gezeigt wird

In der Preisspalte steht wie bisher der aktuelle Kaufpreis. Existiert ein **vorheriger** Verlaufseintrag, kommt darunter kleiner:

- der vorherige Preis, durchgestrichen
- die Veränderung in Prozent
- das Datum der Änderung

**Senkung** steht in `--signal`. **Erhöhung** steht in `--gedaempft`, neutral. Eine Erhöhung zu verschweigen wäre eine Lücke — sie ist Information, nur nicht das Kaufsignal.

Gibt es nur einen Verlaufseintrag, steht dort nichts. Kein Platzhalter, keine leere Zeile.

Das gilt für **beide Fassungen** — Tabelle ab 48rem und Karten darunter.

### 2.2 Der wichtigste technische Punkt

Die Liste zeigt bis zu einer Seitengröße an Objekten. Für jedes den vorletzten Preisverlaufseintrag einzeln zu holen ist ein N+1-Problem — bei 50 Objekten 51 Abfragen.

**Das wird über die Abfrage gelöst**, nicht in der Schleife: Annotation oder `prefetch_related` mit begrenztem Queryset. Welcher Weg, entscheidest du — aber die Abfragezahl muss **konstant** bleiben.

Der bestehende `assertNumQueries`-Zeuge vergleicht 5 gegen 50 Objekte. Er wird auf den neuen Fall erweitert: Objekte **mit** Preisverlauf, nicht nur ohne. Ein Zeuge, der nur preisverlaufsfreie Objekte misst, ginge genau am Problem vorbei.

Bekommst du die Abfragezahl nicht konstant: anhalten und melden. Nicht mit einer Schleife bauen und hoffen.

### 2.3 Was nicht gebaut wird

**Kein Filter „nur mit Preissenkung".** Er wäre bis Schritt 3 leer, weil Senkungen derzeit nur entstehen, wenn jemand dasselbe Inserat zweimal über das Lesezeichen übernimmt.

**Keine Sortierung nach Preisänderung.** Gleicher Grund.

### 2.4 Zeugen

- Objekt mit zwei Verlaufseinträgen, zweiter niedriger → Markierung erscheint, Prozentwert stimmt, Klasse trägt `--signal`
- Objekt mit zwei Einträgen, zweiter höher → Markierung erscheint, **nicht** in `--signal`
- Objekt mit genau einem Eintrag → keine Markierung
- Objekt ohne Preis → keine Markierung, kein Fehler
- Abfragezahl konstant bei 5 gegen 50 Objekten **mit** Preisverlauf

---

## 3. Nachrunde Tests

### 3.1 Kommentar-Zeuge vervollständigen

Der Zeuge aus der Layout-Runde misst an **einem** von vier Kommentartexten, und zwar auf Seite 1. Der vierte Kommentar sitzt im Blätter-Zweig und erscheint erst ab Seite 2 — dort schaut der Test nie hin.

**Zielzustand:** Alle vier Textstellen werden geprüft, und der Test ruft auch eine **zweite Seite** ab. Weiter am Kommentartext messen, nicht an `{#`.

### 3.2 Die vier Stylesheet-Tests bewerten

Aus der Stylesheet-Runde vom 29.08. stammen vier Tests, die CSS-Struktur messen. Einer davon, `StylesheetKorrekturenTests._block_ab_48rem`, hat in der Layout-Runde eine Bauentscheidung diktiert: Der Media-Block musste ein einziger bleiben, damit der Test nicht ins Leere greift.

Das ist die falsche Reihenfolge. Ein Test bewacht eine Zusage; er schreibt nicht vor, wie das Stylesheet gegliedert ist.

**Aufgabe:** Berichte für jeden der vier, was er tatsächlich behauptet. Dann das Kriterium anwenden:

- Misst er eine **Zusage an den Nutzer**, die brechen könnte → bleibt
- Misst er **Struktur oder Schreibweise** des Stylesheets → raus, mit Begründung

Grenzfälle werden **gemeldet, nicht entfernt**. Im Zweifel bleibt der Test stehen und du schreibst dazu, warum du unsicher bist. Ein zu Unrecht entfernter Zeuge fällt niemandem auf.

---

## 4. Abschluss der Runde

Berichte:

1. Geänderte Dateien, Migrationen
2. Testzahl vorher und nachher, alle grün
3. Ob einer der drei Konfliktfälle aus Abschnitt 0 auftrat
4. Ergebnis der Bestands-Datenmigration: wie viele Objekte bekamen einen Schlüssel, gab es Kollisionen
5. Welchen Weg du für Abschnitt 2.2 gewählt hast und wie hoch die Abfragezahl ist
6. Bewertung der vier Stylesheet-Tests einzeln
7. Was du gebaut hast, das hier nicht steht, und warum
8. Welche Zusagen unbewacht bleiben

Sabotage-Gegenprobe je Zeuge. Diese Runde ist im Gegensatz zur Layout-Runde gut bewachbar — hier erwarte ich echte Zeugen, keine Fehlanzeige.

Kein `git push`.
