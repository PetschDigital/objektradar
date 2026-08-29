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

#: Beide Werte oder keiner. Ein halb gefuelltes Paar ist wertlos - der
#: partielle Unique-Index greift nur, wenn Portal UND ID gesetzt sind.
LEER = ("", "")

IDEALISTA_DOMAINS = ("idealista.com", "idealista.it", "idealista.pt")
IMMOSCOUT24_DOMAINS = ("immobilienscout24.de",)

#: Sprachpraefix optional, abschliessender Schraegstrich optional, danach
#: Schluss. Query und Fragment stehen nicht im Pfad - `urlsplit` hat sie
#: bereits abgetrennt, deshalb stoert ein `?utm_source=...` das `$` nicht.
IDEALISTA_PFAD = re.compile(r"^/(?:[a-z]{2}/)?inmueble/(\d+)/?$")

#: Bewusst NICHT bis zum Ende geprueft: ImmoScout24 haengt an die Expose-URL
#: Fragmente und Unterpfade an.
IMMOSCOUT24_PFAD = re.compile(r"^/expose/(\d+)")


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

    if _passt(host, IDEALISTA_DOMAINS):
        treffer = IDEALISTA_PFAD.match(teile.path)
        return (PORTAL_IDEALISTA, treffer.group(1)) if treffer else LEER

    if _passt(host, IMMOSCOUT24_DOMAINS):
        treffer = IMMOSCOUT24_PFAD.match(teile.path)
        return (PORTAL_IMMOSCOUT24, treffer.group(1)) if treffer else LEER

    return LEER
