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

#: Portal, Domains und Pfadmuster in EINER Tabelle statt in fuenf Zweigen.
#: Ein weiteres Portal ist damit eine Zeile und keine vierte Kopie derselben
#: drei Zeilen - und die Zeilen koennen nicht auseinanderdriften.
#:
#: Die Domainmengen ueberschneiden sich nicht; die Schleife nimmt den ersten
#: Treffer. Passt die Domain, aber nicht der Pfad, ist die Antwort LEER - es
#: wird NICHT beim naechsten Eintrag weitergesucht: eine idealista-URL mit
#: unbekanntem Pfad ist kein fotocasa-Inserat.
PORTALE = (
    (PORTAL_IDEALISTA, IDEALISTA_DOMAINS, IDEALISTA_PFAD),
    (PORTAL_IMMOSCOUT24, IMMOSCOUT24_DOMAINS, IMMOSCOUT24_PFAD),
    (PORTAL_FOTOCASA, FOTOCASA_DOMAINS, FOTOCASA_PFAD),
    (PORTAL_MILANUNCIOS, MILANUNCIOS_DOMAINS, MILANUNCIOS_PFAD),
    (PORTAL_PISOS, PISOS_DOMAINS, PISOS_PFAD),
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

    for portal, domains, muster in PORTALE:
        if _passt(host, domains):
            treffer = muster.match(teile.path)
            return (portal, treffer.group(1)) if treffer else LEER

    return LEER
