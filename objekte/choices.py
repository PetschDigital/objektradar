"""Alle Auswahllisten an einer Stelle.

Die gespeicherten Werte sind kurze ASCII-Schluessel, die Beschriftung ist
deutsch. Ein Schluessel wird nicht umbenannt - die Beschriftung schon.
"""

from django.db import models


class Portal(models.TextChoices):
    IDEALISTA = "idealista", "idealista"
    IMMOSCOUT24 = "immoscout24", "ImmoScout24"
    SONSTIGES = "sonstiges", "sonstiges"


class Land(models.TextChoices):
    ES = "ES", "Spanien"
    DE = "DE", "Deutschland"
    SONSTIGES = "sonstiges", "sonstiges"


class Objekttyp(models.TextChoices):
    VILLA = "villa", "Villa"
    HAUS = "haus", "Haus"
    REIHENHAUS = "reihenhaus", "Reihenhaus"
    WOHNUNG = "wohnung", "Wohnung"
    FINCA = "finca", "Finca"
    GRUNDSTUECK = "grundstueck", "Grundstück"
    SONSTIGES = "sonstiges", "sonstiges"


class Zustand(models.TextChoices):
    KOSMETISCH = "kosmetisch", "kosmetisch"
    MITTEL = "mittel", "mittel"
    KERNSANIERUNG = "kernsanierung", "Kernsanierung"
    UNKLAR = "unklar", "unklar"


class Status(models.TextChoices):
    NEU = "neu", "neu"
    IN_PRUEFUNG = "in_pruefung", "in Prüfung"
    BESICHTIGUNG = "besichtigung", "Besichtigung"
    HEISSE_SPUR = "heisse_spur", "heiße Spur"
    RAUS = "raus", "raus"
    VOM_MARKT = "vom_markt", "vom Markt"


#: In der Liste standardmaessig ausgeblendet - geloescht wird nichts.
STATUS_AUSGEBLENDET = frozenset({Status.RAUS, Status.VOM_MARKT})


class Quelle(models.TextChoices):
    URL_EINGEWORFEN = "url_eingeworfen", "URL eingeworfen"
    SUCHAGENT = "suchagent", "Suchagent"
    VON_HAND = "von_hand", "von Hand angelegt"


class PreisQuelle(models.TextChoices):
    SUCHAGENTEN_MAIL = "suchagenten_mail", "Suchagenten-Mail"
    ERNEUTER_ABRUF = "erneuter_abruf", "erneuter Abruf"
    VON_HAND = "von_hand", "von Hand"


class Wertung(models.TextChoices):
    DAFUER = "dafuer", "dafür"
    ANSCHAUEN = "anschauen", "anschauen"
    RAUS = "raus", "raus"
