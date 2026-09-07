"""Portal und Inserats-ID aus der URL lesen - ohne einen einzigen Seitenabruf.

Eigenes Modul und ausdruecklich keine weitere Funktion in `views.py`: der
Mail-Parser aus Schritt 3 braucht dieselbe Logik, und eine View ist kein Ort,
an dem ein Parser nachsieht.

Die Funktion ist rein - kein Datenbankzugriff, kein Netz, kein Django-Import.
Damit ist sie aus einer Datenmigration und aus dem Mail-Parser gleichermassen
aufrufbar; eine Migration, die Anwendungscode importiert, holt sich sonst die
halbe App in den historischen Zustand.

Erkannt wird nur, was sich an einer echten URL pruefen liess. Weitere
Laenderdomains und weitere Portale kommen erst dazu, wenn eine solche URL
vorliegt: ein falsch erkanntes Paar ist schaedlicher als ein leeres, weil es
zwei verschiedene Objekte am Unique-Index kollidieren laesst.
"""

import re
from urllib.parse import urlsplit

#: Die Portal-Schluessel als nackte Zeichenketten, weil dieses Modul Django
#: nicht importieren darf und `choices.Portal` damit ausser Reichweite ist.
#: Dass die Werte zu `choices.Portal` passen, ist deshalb NICHT strukturell
#: gesichert, sondern nur zurueckgelesen bezeugt - siehe
#: `PortalSchluesselTests`. Ohne diesen Zeugen fiele eine Umbenennung in
#: `choices.py` hier still aus: die View schriebe weiter den alten Schluessel,
#: und niemand meldete sich.
PORTAL_IDEALISTA = "idealista"
PORTAL_IMMOSCOUT24 = "immoscout24"
PORTAL_FOTOCASA = "fotocasa"
PORTAL_MILANUNCIOS = "milanuncios"
PORTAL_PISOS = "pisos"
PORTAL_IMMOWELT = "immowelt"

#: Beide Werte oder keiner. Ein halb gefuelltes Paar ist wertlos - der
#: partielle Unique-Index greift nur, wenn Portal UND ID gesetzt sind.
LEER = ("", "")

#: `idealista.it` und `idealista.pt` sind am 02.09. HERAUSGEFALLEN. Sie standen
#: hier als Domain, ohne dass je ein Pfadmuster fuer sie belegt war: das
#: spanische `inmueble` traf auf ihnen nur, weil niemand eine echte
#: italienische oder portugiesische URL dagegengehalten hat. Damit taeuschten
#: sie Abdeckung vor, die es nicht gab - und die Gruppe sucht ohnehin in
#: Spanien. Eine `.it`-URL faellt jetzt auf "sonstiges", und das ist der
#: richtige Ausgang.
IDEALISTA_DOMAINS = ("idealista.com",)
IMMOSCOUT24_DOMAINS = ("immobilienscout24.de",)
FOTOCASA_DOMAINS = ("fotocasa.es",)
MILANUNCIOS_DOMAINS = ("milanuncios.com",)
PISOS_DOMAINS = ("pisos.com",)

#: NUR `immowelt.de`. `immowelt.at`, `immowelt.ch` und `immonet.de` stehen
#: ausdruecklich NICHT hier: fuer keine dieser Domains liegt eine belegte
#: Inserats-URL vor. Ein Domaineintrag ohne belegtes Pfadmuster taeuscht
#: Abdeckung vor - genau dafuer sind am 02.09. `idealista.it` und `.pt`
#: herausgefallen. Das fuehrende `www.` nimmt `_host()` ab, es gehoert deshalb
#: nicht in die Liste.
IMMOWELT_DOMAINS = ("immowelt.de",)

#: Sprachpraefix optional, abschliessender Schraegstrich optional, danach
#: Schluss. Query und Fragment stehen nicht im Pfad - `urlsplit` hat sie
#: bereits abgetrennt, deshalb stoert ein `?utm_source=...` das `$` nicht.
IDEALISTA_PFAD = re.compile(r"^/(?:[a-z]{2}/)?inmueble/(\d+)/?$")

#: Bewusst NICHT bis zum Ende geprueft: ImmoScout24 haengt an die Expose-URL
#: Fragmente und Unterpfade an.
IMMOSCOUT24_PFAD = re.compile(r"^/expose/(\d+)")

#: Die letzte Zahl im Pfad; ein einzelner Buchstabe als letztes Segment (`/d`
#: an der Expose-URL) wird uebersprungen.
#:
#: Der Anfang des Pfades wird ABSICHTLICH nicht geprueft. Dasselbe Inserat
#: heisst auf der deutschen Fassung `/de/kaufen/wohnimmobilie/...` und auf der
#: spanischen `/es/comprar/vivienda/...` - haengt das Muster am Sprachpraefix
#: oder an den Woertern dahinter, legen zwei Personen dasselbe Objekt doppelt
#: an. Der Ausstattungspfad davor ist zudem beliebig lang.
#:
#: `(?:.*/)?` ist gierig und greift damit die LETZTE Zahl: die Neubau-URL
#: traegt zwei (`.../20561853/189207445`) und muss die zweite liefern.
FOTOCASA_PFAD = re.compile(r"^/(?:.*/)?(\d+)(?:/[a-zA-Z])?/?$")

#: Die Zahl nach dem letzten Bindestrich vor `.htm`.
#:
#: Was das Ergebnis festnagelt, ist NICHT die Gier von `.*-`, sondern der
#: Anker `\.htm$`: die Ziffern muessen unmittelbar davor stehen. Am 02.09. in
#: der Sabotage-Gegenprobe nachgemessen - ein nicht-gieriges `.*?-` liefert
#: an derselben URL denselben Wert. Der Hinweis steht hier, weil eine erste
#: Fassung dieses Kommentars die Gier fuer tragend hielt und ein Zeuge, der
#: sie bewachen sollte, deshalb nichts gemessen haette.
#:
#: DUENNE STELLE, ausdruecklich so gebaut: dieses Muster haengt an einem
#: einzigen Beleg. Kleinanzeigenportale fuehren oft mehrere Anzeigentypen mit
#: abweichenden Pfaden. Passt eine URL nicht, faellt sie auf "sonstiges" -
#: das ist der richtige Ausgang, kein Fehler.
MILANUNCIOS_PFAD = re.compile(r"^/.*-(\d+)\.htm$")

#: Der VOLLSTAENDIGE Block aus zwei durch Unterstrich getrennten Zahlen am
#: Pfadende, nicht eine der beiden.
#:
#: Die zweite Zahl ist in beiden Belegen sechsstellig und beginnt mit `10` -
#: vermutlich eine Makler- oder Agenturkennung. Naehme man nur sie, truegen
#: alle Objekte desselben Maklers denselben Schluessel und der Dublettenschutz
#: waere still tot. Naehme man nur die erste, drohen Kollisionen. Der ganze
#: Block ist die sichere Wahl: ist er zu breit gefasst, erscheint spaeter eine
#: Dublette, die keine ist - sichtbar und reparierbar. Der umgekehrte Fehler
#: waere unsichtbar.
PISOS_PFAD = re.compile(r"^/.*[-/](\d+_\d+)/?$")

#: Das ERSTE Pfadsegment hinter `/expose/`, was auch immer darin steht.
#:
#: Kein `$` am Ende und deshalb auch keine Sonderbehandlung fuer den
#: abschliessenden Schraegstrich oder fuer weitere Segmente dahinter: `[^/]+`
#: hoert am naechsten Schraegstrich von selbst auf. Query und Fragment hat
#: `urlsplit` schon abgetrennt. Ist das Segment leer (`/expose/`), verlangt
#: `+` mindestens ein Zeichen und das Muster trifft nicht - die Antwort ist
#: dann LEER, also Portal `sonstiges` ohne Schluessel.
#:
#: AUSDRUECKLICH NICHT auf UUID-Format geprueft. Beide Belege tragen eine
#: UUID, aber fuehrt Immowelt aeltere Inserate mit anderem Kennungsformat,
#: fielen die bei einem strengen Muster auf `sonstiges` durch - das erzeugt
#: eine sichtbare Dublette. Ein zu breites Muster erzeugte umgekehrt eine
#: STILLE Kollision, und die ist hier ausgeschlossen: das Segment hinter
#: `/expose/` ist per Definition die Inseratskennung und nicht die eines
#: Anbieters. Von den beiden moeglichen Fehlern ist der sichtbare gewaehlt.
IMMOWELT_PFAD = re.compile(r"^/expose/([^/]+)")


def _unveraendert(kennung: str) -> str:
    """Die Kennung so, wie sie im Pfad steht."""
    return kennung


#: Portal, Domains, Pfadmuster und Normalisierung der Kennung in EINER Tabelle
#: statt in sechs Zweigen. Ein weiteres Portal ist damit eine Zeile und keine
#: weitere Kopie derselben vier Zeilen - und die Zeilen koennen nicht
#: auseinanderdriften.
#:
#: Die vierte Spalte ist am 07.09. dazugekommen. Sie steht ABSICHTLICH je
#: Portal und nicht als ein globales `.lower()` in `portal_und_id()`: fuer die
#: fuenf aelteren Portale ist die Kennung eine reine Ziffernfolge, an der
#: Kleinschreibung nichts belegt und nichts geprueft waere. Eine Regel, die
#: fuer vier Portale nie an einer echten URL nachgemessen wurde, dort
#: mitlaufen zu lassen, ist dieselbe vorgetaeuschte Abdeckung wie eine Domain
#: ohne Pfadmuster.
#:
#: Die Domainmengen ueberschneiden sich nicht; die Schleife nimmt den ersten
#: Treffer. Passt die Domain, aber nicht der Pfad, ist die Antwort LEER - es
#: wird NICHT beim naechsten Eintrag weitergesucht: eine idealista-URL mit
#: unbekanntem Pfad ist kein fotocasa-Inserat.
PORTALE = (
    (PORTAL_IDEALISTA, IDEALISTA_DOMAINS, IDEALISTA_PFAD, _unveraendert),
    (PORTAL_IMMOSCOUT24, IMMOSCOUT24_DOMAINS, IMMOSCOUT24_PFAD, _unveraendert),
    (PORTAL_FOTOCASA, FOTOCASA_DOMAINS, FOTOCASA_PFAD, _unveraendert),
    (PORTAL_MILANUNCIOS, MILANUNCIOS_DOMAINS, MILANUNCIOS_PFAD, _unveraendert),
    (PORTAL_PISOS, PISOS_DOMAINS, PISOS_PFAD, _unveraendert),
    # `str.lower`, weil dieselbe Kennung in abweichender Gross-/Kleinschreibung
    # sonst zwei verschiedene Schluessel fuer dasselbe Inserat ergaebe. Der
    # Dublettenschutz fiele dabei LAUTLOS aus - dieselbe Fehlerart, gegen die
    # bei `pisos.com` der ganze Zahlenblock genommen wurde.
    (PORTAL_IMMOWELT, IMMOWELT_DOMAINS, IMMOWELT_PFAD, str.lower),
)


def _host(teile):
    """Reiner Hostname, klein geschrieben, ohne fuehrendes `www.`.

    `urlsplit(...).hostname` erledigt genau die drei Schritte, die die
    Spezifikation an `netloc` verlangt - Zugangsdaten entfernen, Port
    entfernen, in Kleinbuchstaben wandeln. Von Hand nachgebaut waere es
    dieselbe Regel ein zweites Mal, und zwei Formeln fuer eine Regel driften.
    """
    host = teile.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _passt(host, domains):
    """Genau die Domain oder eine Subdomain davon - kein blosses `endswith`.

    `endswith("idealista.com")` allein traefe auch `nichtidealista.com`. Der
    Punkt in der zweiten Bedingung ist der ganze Unterschied.
    """
    return any(host == d or host.endswith("." + d) for d in domains)


def portal_und_id(url: str) -> tuple[str, str]:
    """`(portal, inserats_id)` aus der URL. Beide gesetzt oder beide leer.

    Wird das Portal nicht sicher erkannt oder keine ID gefunden, ist die
    Rueckgabe `("", "")` - niemals nur eines von beiden.

    `sonstiges` ist ausdruecklich eingeschlossen: fuer dieses Portal gibt es
    kein bekanntes ID-Muster, und ein geratener Wert liesse zwei verschiedene
    Objekte kollidieren.
    """
    try:
        teile = urlsplit(url or "")
    except ValueError:
        # `urlsplit` wirft bei kaputten IPv6-Klammern. Ein unlesbarer Host ist
        # kein erkanntes Portal - und ganz sicher kein Grund fuer einen 500er
        # in einer Datenmigration.
        return LEER

    host = _host(teile)
    if not host:
        return LEER

    for portal, domains, muster, normalisieren in PORTALE:
        if _passt(host, domains):
            treffer = muster.match(teile.path)
            return (portal, normalisieren(treffer.group(1))) if treffer else LEER

    return LEER


def ist_bekannte_domain(url: str) -> bool:
    """Ob die Domain zu einem der bekannten Portale gehoert.

    NICHT dasselbe wie `portal_und_id(url) != LEER`. Eine idealista-URL mit
    unbekanntem Pfad - eine Suchseite etwa - liefert dort `("", "")` und ist
    hier trotzdem eine BEKANNTE Domain. Genau diese Trennung braucht die
    Vorschau: sie warnt vor einer fremden Seite, nicht vor einem Pfadmuster,
    das auf einem Portal noch nicht belegt ist. Ueber `portal_und_id()`
    gemessen, warnte sie bei jeder idealista-Suchseite mit - und eine Warnung,
    die bei bekannten Portalen mitspringt, liest nach der dritten niemand mehr.

    Gelesen wird aus derselben `PORTALE`-Tabelle wie `portal_und_id()`, ueber
    dieselben beiden Hilfsfunktionen. Eine zweite Domainliste daneben driftete
    von der ersten weg, und dann warnte die Vorschau vor einem Portal, das der
    Einwurf laengst erkennt.

    `sonstiges` steht bewusst NICHT in der Tabelle: es ist der Auffangwert der
    Auswahlliste und keine Domain. Es gibt keine Adresse, die dazu gehoerte.
    """
    try:
        teile = urlsplit(url or "")
    except ValueError:
        # Dieselbe Behandlung wie in `portal_und_id()`: ein unlesbarer Host
        # ist kein bekanntes Portal - und kein Grund fuer einen 500er.
        return False

    host = _host(teile)
    if not host:
        return False

    return any(_passt(host, domains) for _, domains, _, _ in PORTALE)


# =========================================================================
# Die Preisquelle je Portal
# =========================================================================
#
# Der Preis wurde bisher ausschliesslich aus dem Fliesstext der Seite
# gelesen - vom Lesezeichen, ueber das groesste Zahl-mit-Euro-Muster. Bei
# Immowelt liefert dieser Weg belegt den Kaufpreis INKLUSIVE
# Kaufnebenkosten: an vier Objekten aus dem Bestand zwischen 10,57 % und
# 11,57 % zu hoch, und die Spreizung ist die der Grunderwerbsteuer.
#
# Der Fehler ist STILL. Elf Prozent liegen mitten im plausiblen Bereich; die
# Plausibilitaetswarnung kann sie nicht fangen, und jedes Immowelt-Objekt
# sortiert sich ueber den EUR/m2-Vergleich lautlos zu teuer ein. Genau
# deshalb steht die Regel hier und nicht als weitere Heuristik im
# Lesezeichen.
#
# Gelesen wird stattdessen der og:Titel, den das Lesezeichen ohnehin schon
# uebergibt. Das ist eine BEWUSSTE Abweichung von `03_Technik.md` ("keine
# portalspezifischen Auswahlen"): der Satz dort zielt auf geratene
# CSS-Auswahlen in undokumentiertem Markup. Ein Meta-Feld, aus dem Titel,
# Beschreibung und Bilder ohnehin kommen und dessen Format an fuenf echten
# Inseraten belegt ist, ist etwas anderes.

#: Eine Zahl unmittelbar vor dem Euro-Zeichen.
#:
#: Belegt ist die Schreibweise OHNE Tausendertrenner (`139000 €`). Der erste
#: Zweig - `139.000 €` - steht hier trotzdem, und zwar nicht aus Vorsorge,
#: sondern als RIEGEL: ohne ihn traefe `\d+` an `139.000 €` die Ziffern `000`
#: und ergaebe einen Preis von null. Ein Muster, das an einer geaenderten
#: Schreibweise still einen falschen Wert liefert, ist genau der Fehler,
#: gegen den diese Runde gebaut ist.
#:
#: Der Blick zurueck `(?<![\d.,])` verhindert dasselbe von der anderen
#: Seite: eine Zahl wird nur als GANZE gelesen, nie als ihr eigenes Ende.
#: Ohne ihn zaehlte `139.000 €` als zwei Treffer, und der Riegel unten
#: schluege an einem voellig normalen Titel an.
#:
#: Ein Komma trennt hier NICHT: `139000,00 €` trifft bewusst nicht und ergibt
#: damit KEINEN Preis. Ein nicht belegtes Format faellt auf "kein Preis" -
#: das ist der gewollte Ausgang, nicht eine Luecke.
TITEL_EURO = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.\u00a0\u202f ]\d{3})+|\d+)\s*€")


def _immowelt_preis_aus_titel(titel: str) -> int | None:
    """Der Kaufpreis aus dem og:Titel - oder `None`.

    Belegtes Format, fuenf Faelle aus zwei Tagen:

        Wohnung 68 m² 139000 € zum Kauf Bernbach,Bad Herrenalb (76332)
        Haus 115 m² 290000 € zum Kauf Neu Lüdershagen,Wendorf (18442)

    Die Zahl vor `m²` ist die Wohnflaeche. Sie wird hier AUSDRUECKLICH NICHT
    gelesen - fuer die Wohnflaeche bleibt das bisherige Verfahren
    unveraendert. Diese Funktion liefert einen Preis und sonst nichts.

    DER RIEGEL: gelesen wird nur bei GENAU EINER Zahl mit Euro-Zeichen. Keine
    Zahl oder mehrere heisst `None` - kein Rueckfall auf das Fliesstextmuster,
    kein "die erste nehmen", kein Raten.

    Die Begruendung ist die Fehlerart: ein leeres Feld sieht man, einen um elf
    Prozent falschen nicht. Aendert Immowelt das Titelformat, muss der Weg auf
    "kein Preis" fallen und nicht auf "irgendein Preis". Ein Inserat auf
    Anfrage, ganz ohne Preisangabe, ist derselbe Fall.

    `None` und nicht 0 als Fehlanzeige: eine 0 im Titel ist ein GELESENER
    Preis und muss von "nichts gelesen" unterscheidbar bleiben. Der Aufrufer
    prueft deshalb gegen `is not None` und nicht gegen den Wahrheitswert.
    """
    treffer = TITEL_EURO.findall(titel or "")
    if len(treffer) != 1:
        return None
    return int(re.sub(r"\D", "", treffer[0]))


#: Welche Portale den Preis aus dem Titel lesen statt aus dem Fliesstext.
#:
#: Eine TABELLE und keine `if`-Abfrage in der Uebernahme: dass die Regel
#: ausschliesslich fuer Immowelt gilt, ist damit strukturell und nicht in
#: einer View nachgehalten. Ein weiteres Portal waere eine Zeile.
#:
#: Beide Funktionen unten lesen aus dieser einen Tabelle. Eine zweite Liste
#: daneben - etwa eine Menge "Portale mit Titelpreis" - driftete von ihr weg,
#: und dann verwuerfe die Uebernahme den Fliesstextpreis fuer ein Portal, fuer
#: das gar kein Titelmuster hinterlegt ist. Das Feld bliebe dauerhaft leer.
PREIS_AUS_TITEL = {
    PORTAL_IMMOWELT: _immowelt_preis_aus_titel,
}


def preis_kommt_aus_dem_titel(portal: str) -> bool:
    """Ob fuer dieses Portal der Fliesstextpreis NICHT mehr gilt.

    Getrennt von `preis_aus_titel()` abzufragen, weil es zwei verschiedene
    Fragen sind: ob das Fliesstextmuster verworfen wird, und was der Titel
    hergibt. Ueber ein `None` allein waeren sie nicht auseinanderzuhalten -
    "dieses Portal liest normal" und "der Riegel hat zugeschlagen" saehen
    gleich aus, und der Riegel fiele auf den Fliesstext zurueck. Genau das
    darf er nicht.
    """
    return portal in PREIS_AUS_TITEL


def preis_aus_titel(portal: str, titel: str) -> int | None:
    """Der Preis aus dem Titel fuer dieses Portal - oder `None`.

    `None` fuer jedes Portal ohne eigene Titelregel. Der Aufrufer fragt vorher
    `preis_kommt_aus_dem_titel()`; ohne diese Frage saehe er nicht, ob `None`
    "kein Titelpreis gelesen" oder "gilt hier gar nicht" heisst.
    """
    leser = PREIS_AUS_TITEL.get(portal)
    if leser is None:
        return None
    return leser(titel)
