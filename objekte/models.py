from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import (
    BooleanField,
    Case,
    DecimalField,
    Exists,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Cast
from django.utils import timezone

from .choices import (
    Land,
    Objekttyp,
    Portal,
    PreisQuelle,
    Quelle,
    SichtungQuelle,
    Status,
    Wertung,
    Zustand,
)

PERSON = settings.AUTH_USER_MODEL


class ObjektQuerySet(models.QuerySet):
    def mit_qm_preis(self):
        """Kaufpreis je Quadratmeter Wohnflaeche als Annotation `qm_preis`.

        Kein Datenbankfeld: der Preis wandert, die Wohnflaeche wird
        nachgetragen, und ein gespeicherter Quotient waere sofort falsch.
        Fehlende oder leere Wohnflaeche ergibt NULL - nicht 0. 0 wuerde beim
        Sortieren als "am guenstigsten" ganz nach oben laufen. Betrifft vor
        allem Grundstuecke.

        Es gibt bewusst KEINE gleichnamige Property am Modell. Zwei Formeln
        fuer eine Regel driften auseinander, und Django kann eine Annotation
        nicht auf eine Property ohne Setter schreiben - jede Ansicht, die den
        Wert braucht, holt ihr Queryset ueber diese Methode.
        """
        return self.annotate(
            qm_preis=Case(
                When(
                    Q(aktueller_preis__isnull=True)
                    | Q(wohnflaeche__isnull=True)
                    | Q(wohnflaeche=0),
                    then=Value(None, output_field=DecimalField(max_digits=12, decimal_places=2)),
                ),
                default=Cast(
                    F("aktueller_preis") / F("wohnflaeche"),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )

    def mit_preisaenderung(self):
        """Vorheriger Preis und Datum der letzten Preisaenderung als Annotationen.

        `vorheriger_preis` ist der Preis des VORLETZTEN Verlaufseintrags,
        `preis_geaendert_am` das Datum des JUENGSTEN. Das ist kein Vertippen:
        gefragt ist "von 249.000 auf 219.000, am 14.08." - der alte Preis kommt
        aus dem vorletzten Eintrag, der Zeitpunkt der Aenderung aber aus dem
        juengsten, denn DER hat die Aenderung gebracht. Das Datum des
        vorletzten Eintrags waere der Tag, an dem der ALTE Preis erfasst wurde,
        und damit die Antwort auf eine Frage, die niemand gestellt hat.

        Gibt es nur einen Eintrag, ist `vorheriger_preis` NULL - der Ausschnitt
        `[1:2]` greift dann ins Leere. Genau daran erkennt das Template, dass
        keine Markierung zu setzen ist.

        Zwei korrelierte Subqueries, KEIN zweites Aggregat: `mit_votumzaehlung()`
        zaehlt bereits ueber `vota`, und ein `Count`/`Max` ueber `preise`
        daneben erzeugte ein Kreuzprodukt - jeder Preiseintrag vervielfachte
        jedes Votum und die Votumzahlen waeren still falsch. Eine Subquery im
        SELECT joint nicht und kann das nicht ausloesen.

        Und sie kostet KEINE zusaetzliche Abfrage: beide stehen in derselben
        Anweisung wie die Liste selbst. Ein `prefetch_related` waere eine
        zweite Abfrage, ein Zugriff je Zeile waeren einundfuenfzig - siehe
        `test_mehr_preisverlauf_kostet_nicht_mehr_abfragen`.

        Die Sortierung `-datum, -id` ist dieselbe wie `Preisverlauf.Meta.ordering`
        und steht hier trotzdem ausgeschrieben: ein Ausschnitt auf einem
        Queryset ohne ausdrueckliches `order_by()` haenge sonst daran, dass die
        Meta-Angabe stehen bleibt - und "der vorletzte Eintrag" waere Zufall,
        sobald sie sich bewegt.
        """
        verlauf = Preisverlauf.objects.filter(objekt=OuterRef("pk")).order_by(
            "-datum", "-id"
        )
        return self.annotate(
            vorheriger_preis=Subquery(verlauf.values("preis")[1:2]),
            preis_geaendert_am=Subquery(verlauf.values("datum")[:1]),
        )

    def mit_erstem_bild(self):
        """Adresse des ersten Bildes als Annotation `erstes_bild`.

        Dieselbe Bauform wie `mit_preisaenderung()` und aus demselben Grund:
        die Bild-URLs liegen in einer EIGENEN Tabelle, und die Liste zeigt je
        Zeile genau eines. Ein Zugriff auf `objekt.bilder` im Template waere
        bei fuenfzig Zeilen einundfuenfzig Abfragen - dasselbe N+1-Muster wie
        beim Preisverlauf.

        Eine Subquery im SELECT und KEIN `prefetch_related`: die Subquery
        steht in derselben Anweisung wie die Liste selbst und kostet damit gar
        keine Abfrage, waehrend ein Prefetch eine zweite braechte. Konstant
        waere beides - aber die Liste zieht drei bedingte `Count`-Aggregate
        ueber `vota`, und ein zusaetzlicher JOIN auf `bilder` daneben erzeugte
        ein Kreuzprodukt: jedes Bild vervielfachte jedes Votum und die
        Votumzahlen waeren still falsch. Eine Subquery joint nicht und kann
        das nicht ausloesen. Siehe `test_mehr_bilder_kosten_nicht_mehr_abfragen`.

        Die Sortierung `reihenfolge, id` ist dieselbe wie `Bild.Meta.ordering`
        und steht hier trotzdem ausgeschrieben - aus demselben Grund wie beim
        Preisverlauf: ein Ausschnitt auf einem Queryset ohne ausdrueckliches
        `order_by()` haengt sonst daran, dass die Meta-Angabe stehen bleibt,
        und "das erste Bild" waere Zufall, sobald sie sich bewegt.

        Ohne Bild ist der Wert NULL. Genau daran erkennt das Template, dass
        die ruhige Flaeche zu setzen ist - und nicht ein `<img>` mit leerer
        Adresse.
        """
        bilder = Bild.objects.filter(objekt=OuterRef("pk")).order_by("reihenfolge", "id")
        return self.annotate(erstes_bild=Subquery(bilder.values("url")[:1]))

    def mit_besuchsmarke(self, person, schwelle):
        """`seit_besuch_bewegt` als Annotation: hat sich hier etwas getan?

        Wahr, wenn NACH der Schwelle mindestens eine der Bewegungsarten
        stattfand - und zwar durch JEMAND ANDEREN. Eigenes Tun zaehlt nicht:
        sonst leuchtet der Person ihr eigener letzter Klick entgegen, und die
        Marke ist nach zwei Tagen wertlos.

        Ist die Schwelle `None`, ist NICHTS markiert. Das ist der erste Besuch
        einer Person oder ein Konto von vor der Einfuehrung der Besuchszeiten.
        `Person.neu_seit` entscheidet ausdruecklich nicht, was `None` bedeutet -
        diese Entscheidung gehoert hierher, wo man sie sieht. Die Gegenlesart
        "alles ist neu" liesse beim ersten Login die komplette Liste leuchten,
        und danach schaut niemand mehr hin.

        Eine Bedingung auf der Zeile selbst, alle uebrigen als
        `Exists()`-Unterabfrage - alles im SELECT derselben Anweisung. Das ist
        der eigentliche Bauteil dieses Punktes: je Bewegungsart eine Abfrage
        je Zeile waeren bei fuenfzig Zeilen mehrere hundert je Seitenaufruf.
        So sind es null zusaetzliche - die Seitenabfrage bleibt unabhaengig
        von der Objektzahl konstant. Hier steht bewusst KEINE Zahl: sie
        stimmte bei jeder neuen Bewegungsart einen Tag lang nicht mehr.

        `Exists` und KEIN Aggregat: die Liste zieht bereits drei bedingte
        `Count` ueber `vota`, und ein zweiter JOIN auf `notizen` oder `preise`
        daneben erzeugte ein Kreuzprodukt - jede Notiz vervielfachte jedes
        Votum und die Votumzahlen waeren still falsch. Eine `Exists`-Subquery
        joint nicht und kann das nicht ausloesen. Dieselbe Ueberlegung wie bei
        `mit_preisaenderung()` und `mit_erstem_bild()`.

        `__gt` und nicht `__gte`: die Schwelle IST die letzte Aktivitaet der
        Person aus dem vorherigen Besuch. Was genau in dieser Mikrosekunde
        geschah, hat sie gesehen - mit `__gte` markierte ihr eigener letzter
        Aufruf das Objekt, das sie gerade angesehen hat.

        Der Preisverlauf prueft gegen `erfasst_am` und NIE gegen `datum`.
        `datum` ist das fachliche Preisdatum, darf in der Vergangenheit liegen
        und ist ein `DateField` - eine Schwelle mit Uhrzeit laesst sich
        dagegen nicht pruefen. Siehe das Feld im Modell.

        Vier der fuenf Bewegungsarten laufen ueber denselben Helfer
        `durch_andere()`, die Sichtung seit dem 15.09. eingeschlossen. Die
        NULL-Behandlung steht dort EINMAL und gilt damit fuer alle vier.

        Am Preisverlauf haengt als EINZIGEM keine Person: `02` fuehrt nur
        Objekt, Datum, Preis und Quelle. Eine von Hand eingetragene
        Preisaenderung ist deshalb nicht zuzuordnen und markiert auch fuer die
        eintragende Person. Das ist bewusst so hingenommen und kein Fehler -
        ab Schritt 3 kommen Preisaenderungen ohnehin ueberwiegend aus den
        Suchagenten-Mails, wo es gar keine Person gibt.
        """
        if schwelle is None:
            return self.annotate(
                seit_besuch_bewegt=Value(False, output_field=BooleanField())
            )

        def durch_andere(modell, zeitfeld):
            """Gibt es an diesem Objekt einen Eintrag NACH der Schwelle, der
            nicht von dieser Person stammt?

            `Sichtung.person` und `Statusaenderung.person` sind NULLBAR, und
            die NULL-Person steht deshalb AUSGESCHRIEBEN da. Das ist seit dem
            15.09. eine STILREGEL UND KEIN RIEGEL - ausdruecklich, nicht aus
            Nachlaessigkeit. Dieselbe Hausform wie bei `eingestellt_von`
            unten.

            SIE HAELT KEINE ZEILE, DIE NICHT AUCH SO BLIEBE. Nachgemessen:
            Django schreibt in jede negierte Gleichheit auf einer nullbaren
            Spalte selbst einen NULL-Riegel, gleichlautend in
            `.exclude(person=person)` wie in `filter(~Q(person=person))`:

                NOT (person_id = %s AND person_id IS NOT NULL)

            Fuer die NULL-Zeile ist das `NOT (unbekannt AND falsch)` und
            damit WAHR - die Zeile bleibt, in allen drei Fassungen. Was in
            rohem SQL ein Fund waere, ist im ORM keiner.

            Folge, die man wissen muss: WER DIESEN ZWEIG ENTFERNT, WIRD VON
            KEINEM ZEUGEN AUFGEHALTEN. Es gibt hier nichts zu bewachen, weil
            nichts kaputtgeht. Der Zweig traegt erst, wenn diese Abfrage
            einmal an Django vorbei gebaut wird - in rohem SQL, in einer
            `RawSQL`-Annotation oder unter einem ORM, das die Umschreibung
            nicht macht. Bis dahin steht er als Regel, die man liest. Siehe
            `SichtungBesuchsmarkeTests` und den Bericht vom 15.09.

            Bei `Votum` und `Notiz` ist die Spalte NOT NULL - der NULL-Zweig
            laeuft dort ins Leere. Dass er trotzdem mitlaeuft, ist der Sinn
            der Sache: EINE Form fuer alle vier statt vier Fassungen, von
            denen jede einzeln verfallen kann. Die ausgeschriebene
            Sonderfassung nur fuer die Sichtung stand hier bis zum 15.09.
            und ist zurueckgebaut.
            """
            return Exists(
                modell.objects.filter(
                    Q(person__isnull=True) | ~Q(person=person),
                    objekt=OuterRef("pk"),
                    **{f"{zeitfeld}__gt": schwelle},
                )
            )

        bewegt = (
            # Das Objekt selbst - keine Unterabfrage, die Spalten stehen an
            # der Zeile. `eingestellt_von` ist NULLBAR, und die NULL steht
            # ausgeschrieben da: ab Schritt 3 legt der Mail-Parser Objekte
            # ohne Person an, und die soll die Marke tragen.
            #
            # Bis zum 15.09. stand hier, `~Q(...)` allein liesse die NULL in
            # SQL liegen. Nachgemessen stimmt das im ORM nicht - Django haengt
            # den NULL-Riegel selbst an, siehe `durch_andere()` oben. Der
            # Zweig bleibt trotzdem stehen, aus demselben Grund wie dort: als
            # Regel, die man liest.
            (
                Q(eingestellt_am__gt=schwelle)
                & (Q(eingestellt_von__isnull=True) | ~Q(eingestellt_von=person))
            )
            | Q(durch_andere(Votum, "geaendert_am"))
            | Q(durch_andere(Notiz, "erstellt_am"))
            | Q(durch_andere(Statusaenderung, "datum"))
            # Dass eine Sichtung als Bewegung zaehlt, ist der Punkt: so ist
            # erkennbar, dass jemand nachgesehen hat, und drei Leute pruefen
            # nicht dasselbe Inserat am selben Tag. Die NULL-Person steht im
            # Helfer, nicht hier.
            | Q(durch_andere(Sichtung, "zeitpunkt"))
            # Ohne Personenfilter - siehe Docstring.
            | Q(
                Exists(
                    Preisverlauf.objects.filter(
                        objekt=OuterRef("pk"), erfasst_am__gt=schwelle
                    )
                )
            )
        )
        return self.annotate(
            seit_besuch_bewegt=ExpressionWrapper(bewegt, output_field=BooleanField())
        )

    def mit_eigenem_votum(self, person):
        """`hat_eigenes_votum` als Annotation: hat DIESE Person an DIESEM Objekt gestimmt?

        Die Freischaltung des verdeckten Votums. Wer an einem Objekt noch nicht
        abgestimmt hat, sieht dort die Vota der anderen nicht - weder Zaehlstand
        noch Wertung noch Begruendung. Das kippt die Zusage aus `01` und `02`
        ("Alle sehen alle Vota") und ist mit dem Ankereffekt begruendet: wer
        "3 dafuer" liest, stimmt eher zu, und dann misst das Votum eine Meinung
        plus vier Bestaetigungen.

        JE OBJEKT und je Person, nicht global. Deshalb `OuterRef("pk")` in der
        Unterabfrage: ein Votum an Objekt A schaltet Objekt B nicht frei. Eine
        Fassung ohne diesen Bezug - "hat diese Person irgendwo gestimmt" - waere
        ein globaler Schalter und traefe die Zusage nicht.

        JEDES Votum schaltet frei, auch "anschauen". Deshalb steht hier kein
        Filter auf `wertung`: eine Sonderbehandlung einzelner Wertungen waere
        eine zweite Regel, die niemand angefordert hat.

        `Exists` und KEIN Aggregat: die Liste zieht bereits drei bedingte
        `Count` ueber `vota`, und ein zweites Aggregat daneben - auch ueber
        dieselbe Relation - braeuchte einen eigenen Filter am selben JOIN und
        waere von den drei Zaehlungen nicht mehr zu trennen. Eine
        `Exists`-Subquery steht im SELECT, joint nicht und kann die Zahlen
        nicht anfassen. Dieselbe Ueberlegung wie bei `mit_besuchsmarke()`.

        Und sie kostet KEINE zusaetzliche Abfrage: sie steht in derselben
        Anweisung wie die Liste selbst. Ein Zugriff je Zeile - `objekt.vota`
        im Template oder eine Schleife in der Ansicht - waere bei fuenfzig
        Zeilen einundfuenfzig Abfragen. Siehe
        `test_mehr_objekte_kosten_nicht_mehr_abfragen` in `VerdecktesVotumTests`.

        Die Frage laesst sich aus KEINER Zeile selbst beantworten: an `Objekt`
        haengt keine Spalte, die "diese Person hat hier gestimmt" wuesste. Die
        Unterabfrage ist damit nicht wegzukuerzen - anders als bei
        `mit_besuchsmarke()`, wo `eingestellt_am` an der Zeile steht und eine
        naive Schleife darauf kurzschliessen konnte.
        """
        return self.annotate(
            hat_eigenes_votum=Exists(
                Votum.objects.filter(objekt=OuterRef("pk"), person=person)
            )
        )

    def sichtbar(self):
        """Ohne verworfene und vom Markt genommene Objekte. Geloescht wird nichts."""
        from .choices import STATUS_AUSGEBLENDET

        return self.exclude(status__in=list(STATUS_AUSGEBLENDET))


class Objekt(models.Model):
    """Ein Inserat. Einziges Pflichtfeld ist die URL.

    Ein Objekt, von dem nur der Link bekannt ist, muss speicherbar sein - sonst
    dauert das Erfassen laenger als ein paar Sekunden und die Liste stirbt.
    """

    url = models.URLField("Link zum Inserat", max_length=500)

    portal = models.CharField("Portal", max_length=20, choices=Portal, blank=True, default="")
    inserats_id = models.CharField(
        "Inserats-ID",
        max_length=100,
        blank=True,
        default="",
        help_text="Zusammen mit dem Portal der Dublettenschlüssel. Ab Schritt 2 vom Parser gefüllt.",
    )

    titel = models.CharField("Titel des Inserats", max_length=300, blank=True, default="")
    # Ein ZEICHENFELD und ausdruecklich keine Zahl: `01067` verloere als Zahl
    # die fuehrende Null. Mit einer PLZ rechnet niemand; sie ist eine
    # Kennung, die aus Ziffern besteht.
    #
    # Die Laenge von zehn Zeichen ist vorgegeben (15.09.). Gefuellt wird das
    # Feld derzeit nur aus dem Immowelt-Titel, und dort liefert das Muster in
    # `portale.py` genau fuenf Ziffern - die Leseregel kann die Feldlaenge
    # nicht ueberschreiten und braucht deshalb keinen Laengenriegel.
    plz = models.CharField("PLZ", max_length=10, blank=True, default="")
    # Feldtyp, Laenge und `blank`-Verhalten sind ABSICHTLICH Zeichen fuer
    # Zeichen die von `ort`: beide tragen ein Segment desselben Titels, und
    # eine abweichende Laenge liesse den Riegel in `ort_und_stadtteil()` und
    # `angaben_aus_titel()` fuer das eine Feld anders greifen als fuer das
    # andere.
    #
    # Die drei Ortsebenen sind getrennte Felder und keine Abstufung eines
    # einzigen: `stadtteil` ist die Lage INNERHALB der Gemeinde, `ort` die
    # Gemeinde, `region` die uebergeordnete Ebene. Ein einzelnes Ortsfeld warf
    # genau den Unterschied weg, der den Preis erklaert - `Puerto de Santiago`
    # am Meer und `Tamaimo-Arguayo` im Landesinneren liegen beide in der
    # Gemeinde `Santiago del Teide`.
    #
    # Die BESCHRIFTUNG heisst seit dem 15.09. "Ortsteil", der Spaltenname
    # bleibt `stadtteil` - keine Umbenennung, keine Datenmigration, kein
    # Umbau von Filter oder Freitextsuche. "Lage innerhalb der Gemeinde"
    # trifft auf einen Hamburger Stadtteil ebenso zu wie auf ein Dorf in einer
    # Amtsgemeinde; "Stadtteil" traf nur den ersten Fall. Ein Feld, das bei
    # Doerfern falsch beschriftet ist, bleibt leer - und genau diese Angabe
    # erklaert den Preisunterschied.
    stadtteil = models.CharField("Ortsteil", max_length=150, blank=True, default="")
    ort = models.CharField("Ort", max_length=150, blank=True, default="")
    land = models.CharField("Land", max_length=20, choices=Land, blank=True, default="")
    region = models.CharField(
        "Region",
        max_length=150,
        blank=True,
        default="",
        help_text="Freitext. Der Suchraum ist offen und wird nicht vorab festgelegt.",
    )
    objekttyp = models.CharField(
        "Objekttyp", max_length=20, choices=Objekttyp, blank=True, default=""
    )

    wohnflaeche = models.DecimalField(
        "Wohnfläche (m²)",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    grundstuecksgroesse = models.DecimalField(
        "Grundstücksgröße (m²)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Bei Wohnungen leer.",
    )
    zimmer = models.DecimalField(
        "Zimmer",
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    baujahr = models.PositiveSmallIntegerField(
        "Baujahr", null=True, blank=True, validators=[MinValueValidator(1000)]
    )
    beschreibung = models.TextField("Beschreibung", blank=True, default="")

    aktueller_preis = models.DecimalField(
        "Kaufpreis (€)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        # `editable=False` haelt das Feld strukturell aus jedem Formular
        # heraus. `save()` verwirft eine Zuweisung ohnehin still - in einem
        # ModelForm waere das eine Falle: jemand tippt einen Preis ein, nichts
        # passiert, nichts meldet sich. Geaendert wird ueber `preis_setzen()`.
        editable=False,
        help_text="Redundant zum Preisverlauf - der Verlauf führt, dieses Feld hält Filter und Sortierung schnell.",
    )
    zustand = models.CharField(
        "Zustand", max_length=20, choices=Zustand, default=Zustand.UNKLAR
    )
    wert_nach_renovierung = models.DecimalField(
        "Wert nach Renovierung (€)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Grobe Schätzung. Ohne sie ist der Kaufpreis für sich aussagelos.",
    )

    status = models.CharField("Status", max_length=20, choices=Status, default=Status.NEU)
    quelle = models.CharField(
        "Quelle", max_length=20, choices=Quelle, default=Quelle.VON_HAND
    )

    eingestellt_von = models.ForeignKey(
        PERSON,
        verbose_name="eingestellt von",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="eingestellte_objekte",
    )
    eingestellt_am = models.DateTimeField("eingestellt am", auto_now_add=True)
    zuletzt_geaendert_von = models.ForeignKey(
        PERSON,
        verbose_name="zuletzt geändert von",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="geaenderte_objekte",
    )
    zuletzt_geaendert_am = models.DateTimeField("zuletzt geändert am", auto_now=True)
    # PROJEKTION der Tabelle `Sichtung`, kein Eingabefeld. Dasselbe Muster wie
    # `aktueller_preis` gegenueber dem Preisverlauf (Entscheidung 28.08.): die
    # Tabelle fuehrt, die Spalte folgt.
    #
    # Der Hilfetext sagte bis zum 14.09. "wird ab Schritt 2 beim erneuten
    # Abruf gefuellt" und beschrieb damit etwas, das der Code nie getan hat -
    # gefuellt wurde das Feld an genau einer Stelle, beim Ergaenzen eines
    # Bestandsobjekts ueber die Uebernahme. Ein Hilfetext, der eine Zusage
    # macht, die der Code nicht haelt, ist schlimmer als keiner.
    #
    # Bekannte Grenze, unveraendert: `editable=False` ist reine
    # Django-Schicht. Ein `.update()` umgeht sie lautlos. Das gilt beim
    # Kaufpreis genauso und wird hingenommen.
    zuletzt_gesehen = models.DateTimeField(
        "zuletzt gesehen",
        null=True,
        blank=True,
        editable=False,
        help_text="Zeitpunkt der jüngsten Sichtung. Wird ausschließlich von "
        "`sichtung_eintragen()` geschrieben, nie von Hand.",
    )

    objects = ObjektQuerySet.as_manager()

    class Meta:
        verbose_name = "Objekt"
        verbose_name_plural = "Objekte"
        # Zweites Kriterium wie beim Preisverlauf: zwei Objekte aus derselben
        # Mikrosekunde staenden sonst unbestimmt zueinander, und "das zuletzt
        # Eingeworfene steht oben" waere Zufall. Ab Schritt 3 ist das der
        # Normalfall - der Mail-Parser legt mehrere Objekte in einer Schleife an.
        ordering = ["-eingestellt_am", "-id"]
        constraints = [
            # Greift nur, wenn beide Teile gesetzt sind. Die Roh-URL taugt nicht
            # als Schluessel: Sprachpraefix, Tracking-Parameter und abschliessender
            # Schraegstrich variieren beim selben Inserat.
            models.UniqueConstraint(
                fields=["portal", "inserats_id"],
                condition=~Q(portal="") & ~Q(inserats_id=""),
                name="objekt_eindeutig_je_portal_und_inserats_id",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["land", "region"]),
            models.Index(fields=["aktueller_preis"]),
        ]

    def __str__(self):
        """Titel, ersatzweise Portal und Inserats-ID, ersatzweise die URL.

        Ohne Titel stand hier die volle URL und sprengte die Objektspalte der
        Liste. `Portal · ID` ist kurz, eindeutig und liegt bei jedem
        eingeworfenen Inserat eines erkannten Portals schon vor - niemand muss
        dafuer etwas nachtragen.

        `ort` ist aus der Kette gefallen: die Liste fuehrt dafuer eine eigene
        Spalte, und derselbe Wert zweimal nebeneinander sagt nichts Zweites.
        """
        if self.titel:
            return self.titel
        if self.portal and self.inserats_id:
            return f"{self.get_portal_display()} · {self.inserats_id}"
        return self.url

    def save(self, *args, **kwargs):
        """Legt beim Anlegen den ersten Verlaufseintrag an - und nur dann.

        `aktueller_preis` ist eine Kopie des Preisverlaufs, kein Eingabefeld.
        Zwei getrennte Gruende:

        1. Ein Verlaufseintrag entsteht nur beim Anlegen (`_state.adding`).
           Sonst haengt eine veraltete Instanz ihren alten In-Memory-Preis als
           vermeintlich neuen Eintrag an. Ab Schritt 3 ist das der Normalfall,
           weil der Mail-Parser nebenher Eintraege schreibt.
        2. Bei jedem spaeteren Speichern wird der Preis aus der Datenbank
           nachgelesen, statt ihn zu schreiben. Ohne das schriebe dieselbe
           veraltete Instanz die Spalte auf den alten Wert zurueck und die
           letzte Senkung waere lautlos verschwunden - der Eintrag im Verlauf
           bliebe stehen, die Liste zeigte den alten Preis.

        Geaendert wird der Preis ausschliesslich ueber `preis_setzen()`.
        """
        neu = self._state.adding
        aktualisiert = kwargs.get("update_fields")
        schreibt_preis = aktualisiert is None or "aktueller_preis" in aktualisiert

        if not neu and schreibt_preis:
            self.aktueller_preis = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("aktueller_preis", flat=True)
                .first()
            )

        with transaction.atomic():
            super().save(*args, **kwargs)
            if neu and self.aktueller_preis is not None:
                Preisverlauf.objects.create(
                    objekt=self,
                    preis=self.aktueller_preis,
                    quelle=PreisQuelle.VON_HAND,
                )

    def preis_setzen(self, person, preis, quelle=PreisQuelle.VON_HAND):
        """Preis aendern und den Verlauf fortschreiben. Gibt den Eintrag zurueck.

        Symmetrisch zu `status_setzen()`: der einzige Weg, an dem sich der
        Preis aendert. Der Verlaufseintrag fuehrt, das Objektfeld folgt.
        """
        with transaction.atomic():
            eintrag = Preisverlauf.objects.create(objekt=self, preis=preis, quelle=quelle)
            # `Preisverlauf.save()` hat die Spalte gesetzt, sofern dieser Eintrag
            # der juengste ist. Den massgeblichen Wert zurueckholen, statt den
            # veralteten In-Memory-Wert weiterzureichen.
            self.aktueller_preis = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("aktueller_preis", flat=True)
                .first()
            )
            self.zuletzt_geaendert_von = person
            self.save(update_fields=["zuletzt_geaendert_von", "zuletzt_geaendert_am"])
        return eintrag

    def sichtung_eintragen(self, person=None, quelle=SichtungQuelle.VON_HAND):
        """Bestaetigen, dass das Inserat noch existiert. Gibt den Eintrag zurueck
        - oder `None`, wenn heute schon einer derselben Person vorlag.

        DER EINZIGE WEG, auf dem eine Sichtung entsteht und auf dem
        `zuletzt_gesehen` geschrieben wird. Symmetrisch zu `preis_setzen()`
        und `status_setzen()`: der Verlaufseintrag fuehrt, das Objektfeld
        folgt. Ein `Sichtung.objects.create()` im Anwendungscode umginge die
        Daempfung und liesse Feld und Tabelle auseinanderlaufen.

        GEDAEMPFT auf einen Eintrag je Person und Tag, und zwar
        QUELLENUEBERGREIFEND: wer morgens per Lesezeichen einwirft und
        nachmittags auf "geprueft" klickt, erzeugt einen Eintrag. Die zweite
        Bestaetigung desselben Tages durch dieselbe Person ist keine neue
        Information - sie blaehte nur die Loeschbestaetigung auf und sagte
        nichts Zweites.

        Folge, die so GEWOLLT ist: wird ein Eintrag unterdrueckt, wandert auch
        `zuletzt_gesehen` nicht. Feld und Tabelle laufen nie auseinander, denn
        das Feld wird nur dort gesetzt, wo auch ein Eintrag entsteht.

        "HEUTE" ist `timezone.localdate()` und niemals `date.today()`.
        `USE_TZ` steht auf `True` und `TIME_ZONE` auf `Europe/Berlin`;
        `date.today()` liest dagegen die Zeitzone des BETRIEBSSYSTEMS. Auf dem
        Rechner, auf dem gebaut wird, sind beide gleich - auf einem Server in
        UTC sind sie es zwischen Mitternacht und zwei Uhr nachts nicht, und
        dann daempfte die Methode gegen einen anderen Tag als den, den der
        Vergleich `zeitpunkt__date` in der Datenbank meint: `__date` rechnet
        nach `settings.TIME_ZONE` um. Siehe `SichtungZeitzonenTests`.

        DIE NULL-PERSON IST EIN EIGENER FALL und wird AUSGESCHRIEBEN
        gefuehrt. Ab Schritt 3 legt der Mail-Parser Sichtungen ohne Person an;
        eine Agenten-Mail von heute darf nicht dadurch verschluckt werden,
        dass irgendjemand heute schon geklickt hat - und umgekehrt.
        """
        with transaction.atomic():
            heute = timezone.localdate()
            # AUSGESCHRIEBEN und nicht als `Q(person=person)` mit `None`
            # darin. Die beiden Fassungen sind in Django 5.2 zwar
            # gleichwertig - ein `exact`-Lookup auf `None` wird intern zu
            # `isnull` -, aber die Regel steht hier als Regel und nicht als
            # Nebenwirkung einer ORM-Umschreibung, die sich mit einer
            # Django-Version aendern kann. Dieselbe Hausform wie bei
            # `eingestellt_von` in `mit_besuchsmarke()`.
            wie_diese_person = (
                Q(person__isnull=True) if person is None else Q(person=person)
            )
            if self.sichtungen.filter(wie_diese_person, zeitpunkt__date=heute).exists():
                return None

            eintrag = Sichtung.objects.create(objekt=self, person=person, quelle=quelle)
            # Den juengsten Eintrag AUS DER TABELLE holen, statt
            # `eintrag.zeitpunkt` weiterzureichen. Beides liefert hier
            # denselben Wert - aber "das Feld ist die Projektion der Tabelle"
            # steht damit auch im Code und nicht nur im Kommentar.
            self.zuletzt_gesehen = self.sichtungen.first().zeitpunkt
            # `update_fields` haelt `Objekt.save()` von seinem Preiszweig fern:
            # ohne `aktueller_preis` in der Liste liest er den Preis nicht nach
            # und legt keinen Verlaufseintrag an.
            self.save(update_fields=["zuletzt_gesehen"])
        return eintrag

    def status_setzen(self, person, neuer_status):
        """Status aendern und die Aenderung protokollieren.

        Der Status wird immer manuell gesetzt, nie aus den Vota abgeleitet.
        Bei fuenf Leuten muss nachvollziehbar sein, wer ein Objekt weggeklickt hat.
        """
        if neuer_status == self.status:
            return None
        with transaction.atomic():
            aenderung = Statusaenderung.objects.create(
                objekt=self,
                person=person,
                alter_status=self.status,
                neuer_status=neuer_status,
            )
            self.status = neuer_status
            self.zuletzt_geaendert_von = person
            self.save(update_fields=["status", "zuletzt_geaendert_von", "zuletzt_geaendert_am"])
        return aenderung


class Bild(models.Model):
    """Nur die URL, keine Datei.

    Fremde Inseratsfotos werden nicht kopiert: spart Speicher und Backup auf
    dem kleinen VPS und vermeidet die Frage nach der Nutzungsberechtigung.
    """

    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.CASCADE, related_name="bilder"
    )
    url = models.URLField("Bild-URL", max_length=500)
    reihenfolge = models.PositiveSmallIntegerField("Reihenfolge", default=0)

    class Meta:
        verbose_name = "Bild"
        verbose_name_plural = "Bilder"
        ordering = ["reihenfolge", "id"]

    def __str__(self):
        return self.url


class Preisverlauf(models.Model):
    """Ein Eintrag je erfasstem Preis. Der Verlauf fuehrt, nicht das Objektfeld."""

    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.CASCADE, related_name="preise"
    )
    datum = models.DateField("Datum", default=timezone.localdate)
    # Zwei Zeitbegriffe an einem Modell, und das ist Absicht.
    #
    # `datum` ist das FACHLICHE Preisdatum: der Tag, an dem der Preis im
    # Inserat stand. Es wird von Hand gesetzt, darf in der Vergangenheit
    # liegen und bleibt tagesgenau - der Preisverlauf, die Sortierung und die
    # Preissenkungsmarkierung haengen daran und werden davon nicht beruehrt.
    #
    # `erfasst_am` ist der ERFASSUNGSZEITPUNKT: wann dieser Eintrag in die
    # Datenbank kam. Nur daran laesst sich "seit deinem letzten Besuch"
    # messen. Eine Schwelle von 14:30 Uhr gegen ein reines Datum zu pruefen
    # geht nicht - Django wirft die Uhrzeit beim Vergleich still weg und macht
    # aus 2026-09-04 14:30 die nackte 2026-09-04. Die Marke saehe damit
    # innerhalb eines Tages gar nichts, und wer die Liste zweimal am Tag
    # aufmacht, bekaeme jede Preisaenderung des Tages nie zu sehen.
    #
    # Die Besuchsmarkierung prueft ausschliesslich gegen `erfasst_am`.
    erfasst_am = models.DateTimeField("erfasst am", auto_now_add=True)
    preis = models.DecimalField(
        "Preis (€)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    quelle = models.CharField(
        "Quelle", max_length=20, choices=PreisQuelle, default=PreisQuelle.VON_HAND
    )

    class Meta:
        verbose_name = "Preiseintrag"
        verbose_name_plural = "Preisverlauf"
        # Zweites Kriterium, weil zwei Eintraege am selben Tag sonst
        # unbestimmt sortiert sind und "der juengste" dann Zufall waere.
        ordering = ["-datum", "-id"]

    def __str__(self):
        return f"{self.datum}: {self.preis} €"

    def save(self, *args, **kwargs):
        """Schreibt den juengsten Preis redundant ans Objekt zurueck.

        Bewusst per queryset-`update()`: `objekt.save()` wuerde den Eintrag,
        der gerade entsteht, ein zweites Mal anlegen.
        """
        with transaction.atomic():
            super().save(*args, **kwargs)
            juengster = self.objekt.preise.first()
            if juengster is not None and juengster.pk == self.pk:
                Objekt.objects.filter(pk=self.objekt_id).update(aktueller_preis=self.preis)


class Statusaenderung(models.Model):
    """Wer hat wann welchen Status gesetzt."""

    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.CASCADE, related_name="statusaenderungen"
    )
    person = models.ForeignKey(
        PERSON,
        verbose_name="Person",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="statusaenderungen",
    )
    alter_status = models.CharField("alter Status", max_length=20, choices=Status)
    neuer_status = models.CharField("neuer Status", max_length=20, choices=Status)
    datum = models.DateTimeField("Datum", auto_now_add=True)

    class Meta:
        verbose_name = "Statusänderung"
        verbose_name_plural = "Statusänderungen"
        ordering = ["-datum", "-id"]

    def __str__(self):
        return f"{self.get_alter_status_display()} → {self.get_neuer_status_display()}"


class Votum(models.Model):
    """Ein Votum je Person und Objekt, jederzeit aenderbar.

    UMGEDREHT am 04.09. Bis dahin galt hier und in `01`/`02`: alle sehen alle
    Vota, kein verdecktes Abstimmen. Jetzt sieht die Vota an einem Objekt nur,
    wer an DIESEM Objekt selbst gestimmt hat - der Ankereffekt hat die
    Uebersicht teurer gemacht, als sie wert war: wer "3 dafuer" liest, stimmt
    eher zu, und dann misst das Votum eine Meinung plus vier Bestaetigungen.

    Am Modell aendert das NICHTS. Die Freischaltung ist eine Frage der
    Darstellung und wird dort entschieden, wo dargestellt wird: in der Abfrage
    (`ObjektQuerySet.mit_eigenem_votum()`) und in den beiden Vorlagen. Ein
    Feld oder ein Manager, der "sichtbare Vota" lieferte, waere eine zweite
    Stelle mit derselben Regel - und die driftet.
    """

    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.CASCADE, related_name="vota"
    )
    person = models.ForeignKey(
        PERSON, verbose_name="Person", on_delete=models.PROTECT, related_name="vota"
    )
    wertung = models.CharField("Wertung", max_length=20, choices=Wertung)
    begruendung = models.TextField("Begründung", blank=True, default="")
    geaendert_am = models.DateTimeField("geändert am", auto_now=True)

    class Meta:
        verbose_name = "Votum"
        verbose_name_plural = "Vota"
        # Nach der Spalte, nicht ueber den JOIN auf den Benutzernamen. Die
        # Reihenfolge ist damit die der Kontoanlage, nicht alphabetisch.
        ordering = ["person_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["objekt", "person"], name="ein_votum_je_person_und_objekt"
            ),
        ]

    def __str__(self):
        return f"{self.person}: {self.get_wertung_display()}"


class Notiz(models.Model):
    """Freitext am Objekt, unabhaengig vom Votum. Beliebig viele, chronologisch."""

    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.CASCADE, related_name="notizen"
    )
    person = models.ForeignKey(
        PERSON, verbose_name="Person", on_delete=models.PROTECT, related_name="notizen"
    )
    text = models.TextField("Text")
    erstellt_am = models.DateTimeField("erstellt am", auto_now_add=True)

    class Meta:
        verbose_name = "Notiz"
        verbose_name_plural = "Notizen"
        ordering = ["-erstellt_am", "-id"]

    def __str__(self):
        return self.text[:60]


class Sichtung(models.Model):
    """Ein Eintrag je bestaetigter Existenz des Inserats. Mit Person und Quelle.

    Beantwortet die Zusage aus `01`: "Ist es noch am Markt oder schon weg."
    `Objekt.zuletzt_gesehen` ist die Projektion dieser Tabelle, nicht
    umgekehrt.

    NUR POSITIVBELEG. Die Tabelle traegt ausschliesslich "Inserat existiert".
    Wer nachsieht und nichts findet, setzt den Status auf `vom Markt` - dieser
    Wechsel wird seit dem 28.08. in `Statusaenderung` mit Person und Datum
    protokolliert und braucht hier keine zweite Spur.

    Angelegt wird ausschliesslich ueber `Objekt.sichtung_eintragen()`. Ein
    direktes `Sichtung.objects.create()` im Anwendungscode umginge die
    Tagesdaempfung.
    """

    objekt = models.ForeignKey(
        Objekt, verbose_name="Objekt", on_delete=models.CASCADE, related_name="sichtungen"
    )
    # NULLBAR, und das ist ab Schritt 3 der REGELFALL: die staerkste Sichtung
    # ist die ohne Menschen. Meldet eine Agenten-Mail ein bekanntes Objekt
    # erneut, existiert das Inserat nachweislich - da hat niemand geklickt.
    # Dieselbe Lage wie beim Preisverlauf (Entscheidung 05.09.).
    #
    # `PROTECT` wie bei `Votum`, `Notiz` und `Statusaenderung`: ein geloeschtes
    # Konto darf die Belege nicht mitnehmen. Die Kombination aus `PROTECT` und
    # `null=True` hat im Bestand genau ein Vorbild - `Statusaenderung.person`.
    person = models.ForeignKey(
        PERSON,
        verbose_name="Person",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sichtungen",
    )
    # GENAU EIN Zeitfeld, anders als beim Preisverlauf.
    #
    # `Preisverlauf` traegt zwei (`datum` und `erfasst_am`), weil dort zwei
    # Signale gegen zwei verschiedene Zeitbegriffe laufen: die
    # Besuchsmarkierung gegen den Erfassungszeitpunkt, die Preisanzeige gegen
    # das fachliche Preisdatum - die Mail nennt gestern, der Abruf laeuft
    # heute frueh. An der Sichtung haengt kein solches Signal. Ob eine
    # Agenten-Mail von gestern als "gestern" oder "heute frueh" gesehen
    # zaehlt, aendert nichts.
    #
    # `auto_now_add` verhindert zugleich, dass der Wert von aussen gesetzt
    # wird - eine Sichtung, die sich vordatieren laesst, waere kein Beleg.
    zeitpunkt = models.DateTimeField("Zeitpunkt", auto_now_add=True)
    quelle = models.CharField(
        "Quelle", max_length=20, choices=SichtungQuelle, default=SichtungQuelle.VON_HAND
    )

    class Meta:
        verbose_name = "Sichtung"
        verbose_name_plural = "Sichtungen"
        # Zweites Kriterium ZWINGEND, wie beim Preisverlauf und am Objekt
        # selbst: zwei Eintraege aus derselben Mikrosekunde staenden sonst
        # unbestimmt zueinander, und "die juengste Sichtung" - die Anzeige der
        # Objektansicht UND die Quelle fuer `zuletzt_gesehen` - waere Zufall.
        # Ab Schritt 3 ist das der Normalfall: der Mail-Parser traegt mehrere
        # Sichtungen in einer Schleife ein.
        ordering = ["-zeitpunkt", "-id"]

    def __str__(self):
        wer = self.person.anzeigename if self.person_id else self.get_quelle_display()
        return f"{self.zeitpunkt:%d.%m.%Y %H:%M} — {wer}"
