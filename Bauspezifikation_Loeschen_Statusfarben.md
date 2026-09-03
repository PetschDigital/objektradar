# Bauspezifikation — Löschen, Statusfarben, Filterblock, Lesezeichen-Hinweis

> Gilt für Objektradar. Zielzustand, nicht Diff.
> **Stand:** 03.09.2026 · Diese Datei liegt im Repo-Ordner.

---

## 0. Was feststeht und nicht verändert wird

**Nicht anfassen:**

- Feldnamen und `name`-Attribute aller Formulare. Der Schutz sind die 645 bestehenden Tests
- Filterlogik, Sortierung, Blättern, Statuswechsel-Methode, Votum-Logik
- Die Vorbelegung des Statusfilters und die Regel: fehlender Parameter heißt Vorbelegung, leerer heißt null Treffer
- `--signal` bleibt der Preissenkung vorbehalten, `--fehler` den Fehlern
- Die Ergebnisse der drei vorangegangenen Runden werden benutzt, nicht überarbeitet
- Der Kaufpreis bleibt Projektion des Preisverlaufs

**Verboten:** JavaScript, neue Abhängigkeiten, Änderungen an der Auslesbarkeit des Lesezeichens.

**Anhalten und melden, nicht raten**, wenn:

1. Eine `PROTECT`-Beziehung das Löschen eines Objekts verhindert
2. Die Meldungsdarstellung keine Stufe zwischen Hinweis und Fehler kennt
3. Diese Datei dem widerspricht, was du im Repo vorfindest

---

## 1. Objekt löschen

### 1.1 Abgrenzung, die auf die Seite gehört

**Löschen ist nicht „raus".** Der Status „raus" bedeutet: geprüft und verworfen, bleibt sichtbar, damit niemand in drei Monaten dasselbe Objekt erneut prüft. Das ist eine festgelegte Entscheidung und bleibt.

Löschen ist für **Fehleinwürfe und Testmüll** — Dinge, die nie ein Objekt waren. Dieser Unterschied muss auf der Bestätigungsseite stehen, sonst wird gelöscht, was ausgeblendet gehört.

### 1.2 Ablauf

Zwei Stationen, ohne JavaScript:

1. **GET** auf die Löschadresse zeigt eine Bestätigungsseite. Sie legt nichts an und löscht nichts
2. **POST** von dort löscht und leitet auf die Liste um, mit Meldung

Der Einstieg liegt in der **Objektansicht**, nicht in der Liste. In der Liste wäre er ein Fehlklick neben dem Öffnen.

Er ist deutlich schwächer gewichtet als „Bearbeiten" — kein Knopf in derselben Größe, sondern abgesetzt, gedämpft, am Ende der Seite beim Beleg-Block.

### 1.3 Die Bestätigungsseite

Sie nennt **was verloren geht**, mit Zahlen aus dem Bestand: wie viele Vota, Notizen, Preisverlaufseinträge und Statusänderungen am Objekt hängen.

Grund: Fünf Leute arbeiten an derselben Liste. Wer löscht, löscht möglicherweise die Arbeit anderer, ohne es zu wissen. Eine Seite, die nur „wirklich löschen?" fragt, verschweigt das.

Dazu die Bezeichnung des Objekts nach der Rückfall-Regel und ein Weg zurück, der nichts tut.

### 1.4 Wer darf

Jeder. `01_Konzept.md` legt fest: alle gleichberechtigt, kein Rollenkonzept.

### 1.5 Zeugen

- GET auf die Löschadresse löscht nichts und zeigt die Bestätigung
- POST löscht das Objekt
- Vota, Notizen, Preisverlauf, Statusänderungen und Bilder des Objekts verschwinden mit
- Andere Objekte bleiben unberührt
- Nach dem Löschen: Umleitung auf die Liste, Objekt dort nicht mehr auffindbar
- Die Bestätigungsseite nennt die tatsächlichen Zahlen, nicht null

---

## 2. Statusfarben

### 2.1 Was gebraucht wird

Sechs Status, heute alle in derselben Schrift. Sie werden gesucht, nicht gelesen — deshalb Farbe.

Jeder Status bekommt eine eigene, **gedeckte** Hintergrundfläche mit lesbarer Schrift darauf. Keine grellen Töne; die Liste ist ein Arbeitswerkzeug, kein Warnsystem.

**Kein Rot für „raus".** Rot ist in diesem Werkzeug zweifach belegt — `--signal` für die Preissenkung, `--fehler` für Fehler. Ein rotes „raus" konkurriert mit dem wichtigsten Kaufsignal.

Die Zuordnung, dem Sinn nach:

| Status | Anmutung |
|---|---|
| neu | neutral, unaufdringlich — noch keine Aussage |
| in Prüfung | leicht aktiv |
| Besichtigung | deutlicher aktiv |
| heiße Spur | die wärmste der sechs, aber **nicht** `--signal` |
| raus | zurückgenommen, ausgegraut |
| vom Markt | noch schwächer als „raus", fast neutral |

Die konkreten Werte wählst du. Sie kommen als neue Variablen ins Stylesheet, `--signal` und `--fehler` bleiben unverändert.

### 2.2 Wo

Überall, wo ein Status erscheint: Liste in beiden Fassungen, Objektansicht, und die Anzeige des aktuellen Status am Statusformular.

Der Kontrast muss reichen. Ein blasser Text auf blasser Fläche ist schlechter als gar keine Farbe.

### 2.3 Zeugen

- Jeder der sechs Status trägt eine eigene, unterscheidbare Auszeichnung
- Keine davon ist `var(--signal)` oder `var(--fehler)`
- Der bestehende Zeuge, der `var(--signal)` an genau einer Stelle festhält, bleibt grün

---

## 3. Filterblock nachziehen

Drei Punkte aus der Sichtprüfung:

**Die Doppelpunkte fallen weg.** „Suche:", „Land:", „Preis ab (€):" — die stammen aus der Zeit, als die Beschriftung neben dem Feld stand. Über dem Feld sind sie falsch.

**Das Suchfeld wird begrenzt.** Es zieht sich über die halbe Fensterbreite, während die Felder daneben viel schmaler sind. Es bekommt dieselbe Rasterbreite wie die anderen oder höchstens zwei Spalten.

**Der Statusblock wird sichtbar abgesetzt.** Er steht als volle Zeile zwischen Suche und Land und sieht aus wie alles andere. Er braucht eine erkennbare Abgrenzung — Linie oder eigener Flächenton —, sonst wirkt der Block als eine Kette gleicher Dinge.

Feldnamen, Reihenfolge und Formklasse bleiben unverändert.

---

## 4. Lesezeichen-Hinweis bei unbekannter Domain

Das Lesezeichen löst auf jeder Seite aus. Im Bestand liegt deshalb ein Objekt, das die Objektradar-Seite selbst erfasst hat.

**Es wird nicht gesperrt.** Ein Inserat von einem unbekannten Portal muss weiter erfassbar bleiben — das ist ein Vorteil dieses Weges und wird nicht aufgegeben.

**Stattdessen warnt die Vorschau.** Gehört die Domain zu keinem bekannten Portal, steht auf der Übernahme-Vorschau ein deutlicher Hinweis, dass die Seite vermutlich kein Inserat ist. Speichern bleibt möglich.

Der Hinweis benutzt die bestehende Meldungsdarstellung. **Nicht in `--fehler`** — es ist kein Fehler. Kennt die Darstellung keine Stufe zwischen Hinweis und Fehler: anhalten und melden, keine neue Farbe erfinden.

**Zeugen:**

- Bekannte Portaldomain → kein Hinweis
- Unbekannte Domain → Hinweis erscheint
- Unbekannte Domain → Speichern funktioniert trotzdem

---

## 5. Abschluss

Berichte:

1. Geänderte Dateien, Migrationen
2. Testzahl vorher und nachher, alle grün
3. Ob einer der drei Konfliktfälle aus Abschnitt 0 auftrat
4. Welche Beziehungen beim Löschen mitgehen und ob eine davon `PROTECT` trägt
5. Welche Farbwerte du für die sechs Status gewählt hast
6. Was du gebaut hast, das hier nicht steht, und warum
7. Welche Zusagen unbewacht bleiben

Sabotage-Gegenprobe je Zeuge. Kein `git push`.
