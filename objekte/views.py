from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import CharField, Count, F, Func, Q, Value
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.functional import cached_property
from django.utils.html import format_html
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, TemplateView, UpdateView, View

from .choices import PreisQuelle, Quelle, Status, Wertung
from .forms import ObjektFilterForm, ObjektForm, UebernahmeForm
from .lesezeichen import skript_fuer
from .models import Bild, Notiz, Objekt, Votum
from .portale import portal_und_id

URL_MAXLAENGE = Objekt._meta.get_field("url").max_length

#: Wird vorangestellt, wenn die Eingabe kein Schema mitbringt.
ANGENOMMENES_SCHEMA = "https"


def mit_schema(url):
    """Stellt `https://` voran, wenn die Eingabe kein Schema hat. Sonst unveraendert.

    Am Handy tippt niemand "https://" mit, und ein abgewiesener Einwurf kostet
    genau die Sekunden, um die es bei der Schnellerfassung geht. Alles andere
    an der Eingabe bleibt stehen - Tracking-Parameter, Sprachpraefix und
    abschliessender Schraegstrich werden NICHT angefasst. Ein vorhandenes
    Schema wird auch nicht ausgetauscht: aus `http://` wird kein `https://`,
    sonst zeigte die gespeicherte URL woandershin als die eingeworfene.

    Die Erkennung folgt `forms.URLField.to_python`. Nachgebaut statt benutzt,
    weil das Formularfeld ausser dem Schema noch weitere Normalisierungen
    mitbringt und hier nur diese eine gewollt ist.
    """
    if not url:
        return url
    schema, trenner, _ = url.partition(":")
    hat_schema = (
        trenner
        and schema
        and schema[0].isascii()
        and schema[0].isalpha()
        and "/" not in schema
    )
    if hat_schema:
        return url
    # "//beispiel.de/x" ist protokollrelativ - da fehlt nur das Schema selbst.
    if url.startswith("//"):
        return f"{ANGENOMMENES_SCHEMA}:{url}"
    return f"{ANGENOMMENES_SCHEMA}://{url}"


def gepruefte_url(url):
    """`mit_schema()`, Laengenpruefung, `URLValidator` - in dieser Reihenfolge.

    Gibt `(url, None)` zurueck oder `(None, meldung)`. Herausgezogen, weil der
    Lesezeichen-Zulauf dieselbe Behandlung verlangt wie der Einwurf: zwei
    Kopien derselben drei Pruefungen driften auseinander, und dann haengt an
    einem der beiden Wege eine Regel, die am anderen fehlt.

    Der LEERE Fall steht ausdruecklich NICHT hier drin: beim Einwurf hat
    jemand das Feld nicht ausgefuellt, beim Zulauf hat das Lesezeichen keinen
    Link mitgebracht - das sind zwei verschiedene Sachverhalte und brauchen
    zwei verschiedene Saetze.
    """
    # Vor Laengenpruefung und Validator: was hier herauskommt, ist der Wert,
    # der gespeichert wird - und nur der darf gemessen und geprueft werden.
    url = mit_schema(url)

    if len(url) > URL_MAXLAENGE:
        # Vor dem Validator, aus zwei Gruenden: die Feldlaenge wird sonst
        # nirgends geprueft (ein zu langer Link liefe in einen Datenbankfehler
        # und damit in einen 500er), und ein sehr langer Text gehoert nicht
        # erst durch einen regulaeren Ausdruck geschickt.
        return None, f"Der Link ist länger als {URL_MAXLAENGE} Zeichen."

    try:
        URLValidator()(url)
    except ValidationError:
        return None, "Das ist kein gültiger Link."

    return url, None


def dublette(url):
    """Bestehendes Objekt mit derselben URL, abschliessender Schraegstrich egal.

    Der SCHWACHE Vergleich, Stufe 2. Bewusst ohne Garantie: Sprachpraefix und
    Tracking-Parameter umgehen ihn, und dasselbe Inserat liegt bei mehreren
    Agenturen unter verschiedenen URLs. Seit Schritt 2a laeuft er deshalb erst,
    wenn `dublette_ueber_schluessel()` nichts gefunden hat. Abgeschafft wird er
    darum trotzdem nicht - er traegt drei Faelle, die der starke Vergleich nicht
    sieht: Objekte aus der Zeit vor Schritt 2a, Portale ohne bekanntes
    ID-Muster und URL-Formen, die das Muster nicht trifft.

    `RTRIM` mit Zeichenmenge ist Postgres (und dort auch das, was die Liste
    ohnehin voraussetzt). Beide Seiten werden gleich behandelt, sonst faende
    der Vergleich nur eine der beiden Richtungen.

    Gesucht wird ueber ALLE Objekte, nicht ueber `sichtbar()`: ein verworfenes
    Objekt ein zweites Mal einzuwerfen ist genau der Fall, den "Verworfene
    werden nicht geloescht" verhindern soll.

    Bei mehreren Treffern gewinnt das aelteste - das ist das Original, die
    spaeteren sind Altbestand aus der Zeit vor dieser Pruefung.
    """
    return (
        Objekt.objects.annotate(
            url_ohne_slash=Func(
                F("url"), Value("/"), function="RTRIM", output_field=CharField()
            )
        )
        .filter(url_ohne_slash=url.rstrip("/"))
        .order_by("eingestellt_am", "id")
        .first()
    )


def dublette_ueber_schluessel(portal, inserats_id):
    """Bestehendes Objekt mit demselben Portal-und-ID-Paar. Der STARKE Vergleich.

    Gibt `None` zurueck, sobald einer der beiden Werte leer ist - und das ist
    keine Bequemlichkeit, sondern der Riegel: das Paar `("", "")` teilen sich
    alle Objekte, deren Portal nicht erkannt wurde. Eine Suche darueber liefe
    nicht ins Leere, sondern faende irgendeines davon und leitete den Einwurf
    auf ein voellig fremdes Inserat um.

    Gesucht wird ueber ALLE Objekte, nicht ueber `sichtbar()`: ein verworfenes
    Objekt ein zweites Mal einzuwerfen ist genau der Fall, den "Verworfene
    werden nicht geloescht" verhindern soll.

    Bei mehreren Treffern gewinnt das aelteste. Ueber die Oberflaeche kann es
    zwei Objekte mit demselben Paar nicht geben - der Unique-Index verbietet
    es -, aber der Nachtrag an Bestandsdaten laeuft nicht durch diese View.
    """
    if not portal or not inserats_id:
        return None
    return (
        Objekt.objects.filter(portal=portal, inserats_id=inserats_id)
        .order_by("eingestellt_am", "id")
        .first()
    )


def bestehendes_objekt(url, portal, inserats_id):
    """Die zweistufige Dublettensuche. Gibt das Objekt zurueck oder `None`.

    Die Reihenfolge ist nicht beliebig. Stufe 1 traegt genau das, woran Stufe 2
    scheitert: dasselbe Inserat unter anderem Sprachpraefix, mit
    Tracking-Parametern oder ueber eine andere Laenderdomain. Stufe 2 laeuft
    nur, wenn Stufe 1 nichts findet - fuer Altbestand und fuer Portale ohne
    bekanntes Muster.

    Herausgezogen, damit Einwurf und Lesezeichen-Zulauf dieselbe Suche
    benutzen und nicht zwei Nachbauten, die sich in einem Sonderfall
    unterscheiden.
    """
    vorhanden = dublette_ueber_schluessel(portal, inserats_id)
    if vorhanden is None:
        vorhanden = dublette(url)
    return vorhanden


def _liegt_schon_vor(request, vorhanden):
    """Die Antwort auf eine erkannte Dublette - an genau einer Stelle.

    Zweimal gebraucht: einmal nach der Pruefung, einmal nachdem ein Wettlauf
    am Unique-Index aufgeschlagen ist. Fuer die einwerfende Person darf sich
    der zweite Fall vom ersten nicht unterscheiden, und zwei Kopien derselben
    drei Zeilen driften genau darin auseinander.
    """
    messages.info(request, "Das Inserat liegt schon in der Liste.")
    return redirect("objekt", pk=vorhanden.pk)


# =========================================================================
# Die Liste: Sortierung und Blaettern
# =========================================================================

#: Die zulaessigen Sortierschluessel, jeweils auf- und absteigend. EINE
#: Positivliste - der Parameter aus der Adresse wird niemals an `order_by()`
#: durchgereicht.
#:
#: `letzte_aktivitaet` steht hier bewusst NICHT, obwohl die alte
#: Spezifikation ihn nennt: der Schluessel setzt die Annotation aus
#: `mit_aktivitaet()` voraus, und die gehoert zu Punkt 6.
SORTIERSCHLUESSEL = ("eingestellt_am", "aktueller_preis", "qm_preis", "wohnflaeche")

#: Das zuletzt Eingeworfene steht oben - sonst ist es nicht wiederzufinden.
SORTIERUNG_STANDARD = "-eingestellt_am"

#: Objekte je Seite. Als MODULKONSTANTE und nicht als Zahl im Code: der Zeuge
#: fuer die Sortierstabilitaet muss die Seitengroesse heruntersetzen koennen -
#: fuenf Objekte mit gleichem Sortierwert ueber drei Seiten zu verteilen ist
#: der ganze Versuchsaufbau. Eine fest verdrahtete 50 machte ihn unbaubar.
OBJEKTE_JE_SEITE = 50

#: Beschriftung je Schluessel in der Sortierleiste ueber der Liste. Die
#: Leiste wird aus `SORTIERSCHLUESSEL` gebaut, nicht aus diesem Verzeichnis -
#: es liefert nur die Woerter. Ein fehlender Eintrag faellt dem Zeugen
#: `test_jeder_sortierschluessel_hat_eine_beschriftung` auf.
SORTIERBESCHRIFTUNG = {
    "eingestellt_am": "eingeworfen",
    "aktueller_preis": "Kaufpreis",
    "qm_preis": "€/m²",
    "wohnflaeche": "Wohnfläche",
}


def geprueft_sortierung(roh):
    """Der Sortierwert aus der Adresse, gegen die Positivliste geprueft.

    Ein unbekannter oder ungueltiger Wert faellt STILL auf den Standard
    zurueck - keine Fehlermeldung, kein 500er. Ein veralteter Lesezeichenlink
    ist kein Grund, die Liste zu verweigern.

    Zurueckgegeben wird der neu ZUSAMMENGESETZTE Wert, nicht der eingegebene.
    Damit kann nichts aus der Adresse in `order_by()` gelangen: weder
    `?sortierung=passwort` noch `?sortierung=--eingestellt_am`, das ein
    blosses `lstrip("-")` durchgelassen haette.
    """
    absteigend = roh.startswith("-")
    schluessel = roh[1:] if absteigend else roh
    if schluessel not in SORTIERSCHLUESSEL:
        return SORTIERUNG_STANDARD
    return f"-{schluessel}" if absteigend else schluessel


def reihenfolge(sortierung):
    """Der `order_by()`-Ausdruck: ein Schluessel mit `nulls_last`, dann `-id`.

    `nulls_last=True` ausnahmslos und in BEIDE Richtungen. `mit_qm_preis()`
    liefert fuer Objekte ohne Wohnflaeche korrekt NULL; absteigend sortiert
    schoebe PostgreSQL diese sonst nach vorn, und dann stehen Grundstuecke
    ohne Flaechenangabe ueber allem. `aktueller_preis` und `wohnflaeche` sind
    ebenfalls nullbar. Ein einheitlicher Codepfad, keine Sonderfaelle je
    Schluessel - auch nicht fuer `eingestellt_am`, das nie NULL ist.

    `-id` als zweites Kriterium ist KEINE Kosmetik. Ohne es ist die
    Reihenfolge bei gleichen Werten unbestimmt; PostgreSQL darf bei zwei
    Abfragen verschieden sortieren, und in Verbindung mit dem Paginator heisst
    das: ein Objekt erscheint auf Seite 1 UND auf Seite 2 - oder auf keiner.
    Der Fall tritt sofort ein, nicht theoretisch: drei Objekte ohne
    Wohnflaeche haben alle `qm_preis = NULL`, und beim Einwerfen mehrerer
    Objekte in einer Minute ist auch `eingestellt_am` gleich.

    `Meta.ordering` traegt `-eingestellt_am, -id` bereits - aber ein
    ausdrueckliches `order_by()` ERSETZT `Meta.ordering` vollstaendig. Der
    Zusatz muss deshalb hier erneut gesetzt werden und darf nicht als "steht
    ja schon im Modell" weggelassen werden.
    """
    absteigend = sortierung.startswith("-")
    feld = F(sortierung[1:] if absteigend else sortierung)
    return [
        feld.desc(nulls_last=True) if absteigend else feld.asc(nulls_last=True),
        F("id").desc(),
    ]


#: Die drei Zaehlungen ueber `vota`: Annotationsname, Wertung, Beschriftung.
#: Die Reihenfolge ist die der Anzeige in der Spalte.
VOTUM_ZAEHLUNGEN = (
    ("votum_dafuer", Wertung.DAFUER, "dafür"),
    ("votum_anschauen", Wertung.ANSCHAUEN, "anschauen"),
    ("votum_raus", Wertung.RAUS, "raus"),
)

#: Was in der Spalte steht, wenn niemand abgestimmt hat. Nicht "5 offen" - die
#: nackte Zahl saehe aus wie ein Zwischenstand - und nicht leer, das saehe aus
#: wie ein Anzeigefehler.
KEIN_VOTUM = "noch kein Votum"


def mit_votumzaehlung(objekte):
    """Drei bedingte `Count`-Annotationen ueber `vota`.

    Das ist zulaessig, WEIL alle drei dieselbe Relation anfassen. Ein zweites
    Aggregat ueber eine ANDERE Relation - etwa `notizen` - erzeugte ein
    Kreuzprodukt und lieferte falsche Zahlen: jede Notiz vervielfachte jedes
    Votum. Kommt hier nicht vor und darf auch nicht dazukommen.
    """
    return objekte.annotate(
        **{
            name: Count("vota", filter=Q(vota__wertung=wertung))
            for name, wertung, _ in VOTUM_ZAEHLUNGEN
        }
    )


def votum_uebersicht(objekt, personen):
    """`3 dafür · 1 raus · 1 offen` als fertiger Text fuer die Spalte.

    Kategorien mit dem Wert 0 werden WEGGELASSEN. Sonst stuende in jeder Zeile
    "0 raus", und die Spalte truege keine Information mehr - genau das, wofuer
    sie da ist, ginge im Rauschen unter.

    `personen` ist die Zahl der aktiven Personen und kommt aus dem Kontext -
    EINMAL je Seite ermittelt, nicht je Zeile. Ein `.count()` an dieser Stelle
    waere ein N+1 und faellt dem Zeugen in `AbfragezahlTests` zur Last.

    Gebaut wird der Text in Python und nicht im Template: die beiden Regeln
    "Null faellt weg" und "gar kein Votum bekommt einen eigenen Satz" liessen
    sich dort nur ueber verschachtelte `{% if %}` ausdruecken, und die Zahl
    "offen" braucht eine Subtraktion, die die Template-Sprache nicht kennt.
    """
    zahlen = [(getattr(objekt, name), wort) for name, _, wort in VOTUM_ZAEHLUNGEN]
    abgestimmt = sum(zahl for zahl, _ in zahlen)
    if not abgestimmt:
        return KEIN_VOTUM
    teile = [f"{zahl} {wort}" for zahl, wort in zahlen if zahl]
    offen = personen - abgestimmt
    # Kann negativ werden: wer nach seinem Votum stillgelegt wurde, zaehlt
    # nicht mehr zu den aktiven Personen, sein Votum steht aber weiter da.
    # "-1 offen" waere Unsinn, also faellt der Teil weg.
    if offen > 0:
        teile.append(f"{offen} offen")
    return " · ".join(teile)


def preisaenderung(objekt):
    """Die Preisaenderung fuer die Preisspalte - oder `None`, wenn keine dasteht.

    Erwartet die Annotationen aus `mit_preisaenderung()`. Rechnet nicht selbst
    nach, was die Datenbank schon geliefert hat, und fragt sie auch nicht noch
    einmal - deshalb kostet der Aufruf je Zeile keine Abfrage.

    Gebaut wird der Wert in Python und nicht im Template: die Template-Sprache
    kennt keine Division, und der Prozentwert ist eine.

    `None` in drei Faellen, und jeder hat seinen eigenen Grund:

    - Kein vorheriger Eintrag. Das ist der Normalfall bei allem, was gerade
      erst eingeworfen wurde: ein einziger Eintrag ist kein Verlauf. Es steht
      dann NICHTS in der Zeile - kein Platzhalter, keine leere Zeile.
    - Kein aktueller Preis. Ohne beide Seiten gibt es keine Veraenderung.
    - Ein vorheriger Preis von 0. Durch ihn laesst sich nicht teilen, und
      Postgres wirft dabei - ein 500er auf der Liste, ausgeloest von einem
      einzelnen Datensatz. Ein Kaufpreis von 0 EUR ist ohnehin keine
      Bezugsgroesse, an der sich eine Senkung messen liesse.

    `senkung` traegt die Richtung und NICHT die Farbe: welche Klasse daraus
    wird, entscheidet das Template. Eine Erhoehung wird ausdruecklich mit
    angezeigt - sie zu verschweigen waere eine Luecke -, aber sie ist kein
    Kaufsignal und bekommt deshalb die gedaempfte Darstellung.
    """
    vorher = objekt.vorheriger_preis
    jetzt = objekt.aktueller_preis
    if vorher is None or jetzt is None or not vorher:
        return None
    return {
        "vorher": vorher,
        "prozent": (jetzt - vorher) / vorher * 100,
        "datum": objekt.preis_geaendert_am,
        "senkung": jetzt < vorher,
    }


class ObjektlisteView(ListView):
    """Die Liste. Das Einwerfen liegt in `objekt_anlegen` auf eigener Adresse."""

    template_name = "objekte/objektliste.html"
    context_object_name = "objekte"
    page_kwarg = "seite"

    def get_paginate_by(self, queryset):
        """Die Seitengroesse, bei JEDEM Aufruf frisch aus der Modulkonstante.

        Nicht als Klassenattribut `paginate_by = OBJEKTE_JE_SEITE`: das waere
        zur Importzeit festgeschrieben, und der Stabilitaetszeuge koennte die
        Seitengroesse nicht mehr heruntersetzen.
        """
        return OBJEKTE_JE_SEITE

    def paginate_queryset(self, queryset, page_size):
        """Eine Seitenzahl ausserhalb des Bereichs faellt still auf die LETZTE Seite.

        Gleiche Haltung wie bei der Sortierung: ein veralteter Blaetterlink
        oder eine von Hand getippte Adresse ist kein Grund, die Liste zu
        verweigern. Die Basisklasse wirft hier einen 404er.

        Die letzte Seite, nicht die erste: wer auf Seite 8 steht und einen
        Filter setzt, der die Liste auf drei Seiten kuerzt, landet auf 3 und
        sieht dort Treffer. Auf Seite 1 geworfen zu werden ist der laengere
        Weg zurueck zu dem, was er gerade angesehen hat.

        Eine gar nicht als Zahl lesbare Angabe (`?seite=abc`) fuehrt weiterhin
        auf Seite 1 - da gibt es keine Stelle im Bereich, die gemeint sein
        koennte. Beides zusammen ist genau `Paginator.get_page()`.
        """
        paginator = self.get_paginator(
            queryset,
            page_size,
            orphans=self.get_paginate_orphans(),
            allow_empty_first_page=self.get_allow_empty(),
        )
        seite = paginator.get_page(self.request.GET.get(self.page_kwarg))
        return paginator, seite, seite.object_list, seite.has_other_pages()

    @cached_property
    def filterform(self):
        """Einmal gebaut, zweimal gebraucht: fuer die Abfrage und fuer das Formular.

        Zwei Exemplare aus derselben Adresse waeren zwar gleich, aber das
        zweite muesste seine Werte ein zweites Mal reinigen - und ab dem
        naechsten Feld mit `clean_…` waere "gleich" eine Annahme statt einer
        Zusage.
        """
        return ObjektFilterForm(self.request.GET)

    def get_queryset(self):
        """Der Statusfilter entscheidet, welche Status erscheinen - er allein.

        `sichtbar()` wird hier NICHT mehr aufgerufen. Zwei Mechanismen fuer
        dieselbe Entscheidung verdecken sich gegenseitig: faellt die
        Vorbelegung des Statusfilters aus, faenge `sichtbar()` den Fall noch
        ab, der Zeuge bliebe gruen und die Zusage waere trotzdem weg.

        `sichtbar()` bleibt im Modell unveraendert stehen. Andere Aufrufer
        bleiben unberuehrt.

        `mit_qm_preis()` ist der einzige Weg zum €/m²; es gibt bewusst keine
        Property.
        """
        # Kein `select_related("eingestellt_von")`: die Liste zeigt den
        # Einwerfer nicht an. Ein Aufruf ohne Leser waere eine Optimierung
        # ohne Nutzen - und saehe in einem halben Jahr wie eine Anforderung
        # aus. Kommt die Spalte, kommt er mit ihr zurueck, samt Zeugen.
        objekte = mit_votumzaehlung(Objekt.objects.mit_qm_preis().mit_preisaenderung())
        return self.filterform.filtern(objekte).order_by(*reihenfolge(self.sortierung))

    @cached_property
    def sortierung(self):
        return geprueft_sortierung(self.request.GET.get("sortierung", ""))

    def get_context_data(self, **kwargs):
        kwargs.setdefault("filterform", self.filterform)
        kwargs.setdefault("ist_gefiltert", self.filterform.ist_gefiltert())
        # Der GEPRUEFTE Wert, nicht der eingegebene: das versteckte Feld im
        # Filterformular traegt ihn weiter, und ein durchgereichter Unfug
        # stuende sonst nach dem naechsten Filtern wieder in der Adresse.
        kwargs.setdefault("sortierung", self.sortierung)
        # Beide Werte je Schluessel im Kontext statt im Template: dort liesse
        # sich das fuehrende Minus nicht an einen Schluessel haengen, und vier
        # Paare von Hand hingeschrieben waeren eine zweite Liste neben
        # `SORTIERSCHLUESSEL`.
        kwargs.setdefault(
            "sortierbar",
            [
                {
                    "beschriftung": SORTIERBESCHRIFTUNG[schluessel],
                    "aufsteigend": schluessel,
                    "absteigend": f"-{schluessel}",
                }
                for schluessel in SORTIERSCHLUESSEL
            ],
        )
        kontext = super().get_context_data(**kwargs)
        # Aus dem Paginator, nicht ueber ein eigenes `count()`: er hat die
        # Zahl bereits gezaehlt, und eine zweite Zaehlabfrage kaeme obendrauf.
        kontext["trefferzahl"] = kontext["paginator"].count
        if kontext["ist_gefiltert"]:
            # Die Gesamtzahl ist die Zahl ALLER Objekte ohne jeden Filter -
            # nicht die der sichtbaren. "12 von 340" beantwortet die Frage
            # "wie viel blende ich gerade aus"; "12 von 15" beantwortete sie
            # nicht. Nur beim gefilterten Stand geholt: ungefiltert steht die
            # Zeile nicht da und die Abfrage waere umsonst.
            kontext["gesamtzahl"] = Objekt.objects.count()

        # EINE Abfrage je Seite, nicht je Zeile: die Personenzahl steht fuer
        # alle Zeilen gleich, und ein `.count()` in der Schleife waere genau
        # das N+1, gegen das `AbfragezahlTests` den Riegel haelt. Die Zahlen
        # selbst stehen als Annotation schon an den Objekten.
        personen = get_user_model().objects.filter(is_active=True).count()
        for objekt in kontext["objekte"]:
            objekt.votum_uebersicht = votum_uebersicht(objekt, personen)
            # Beide Werte liegen als Annotation schon an der Zeile; hier wird
            # nur gerechnet. Keine Abfrage in dieser Schleife - das ist die
            # Zusage, die `test_mehr_preisverlauf_kostet_nicht_mehr_abfragen`
            # haelt.
            objekt.preisaenderung = preisaenderung(objekt)
        return kontext


@require_POST
def objekt_anlegen(request):
    """Schnellerfassung: ein URL-Feld, ein Knopf.

    Antwortet immer mit einer Umleitung, nie mit gerendertem HTML - ein
    Neuladen wuerde sonst dasselbe Objekt ein zweites Mal anlegen. Fehler
    laufen deshalb ueber `messages`, nicht ueber Formularfehler.
    """
    url = request.POST.get("url", "").strip()

    if not url:
        messages.error(request, "Bitte einen Link eintragen.")
        return redirect("objektliste")

    url, fehler = gepruefte_url(url)
    if fehler:
        messages.error(request, fehler)
        return redirect("objektliste")

    # Beide Werte stehen in der URL selbst und kosten keinen Seitenabruf.
    # Damit wird der Dublettenschutz echt - unabhaengig von Schritt 2 und von
    # jeder Sperre auf Portalseite.
    portal, inserats_id = portal_und_id(url)

    vorhanden = bestehendes_objekt(url, portal, inserats_id)
    if vorhanden is not None:
        return _liegt_schon_vor(request, vorhanden)

    try:
        # Eng um den einen Aufruf: ein gefangener `IntegrityError` macht in
        # einer noch offenen Transaktion jede folgende Abfrage unbrauchbar -
        # und genau danach fragt der `except`-Zweig die Datenbank noch einmal.
        with transaction.atomic():
            objekt = Objekt.objects.create(
                # Bis auf ein fehlendes Schema unveraendert - siehe `mit_schema()`.
                url=url,
                portal=portal,
                inserats_id=inserats_id,
                quelle=Quelle.URL_EINGEWORFEN,
                eingestellt_von=request.user,
                zuletzt_geaendert_von=request.user,
            )
    except IntegrityError:
        # Wettlauf: zwischen Pruefung und Insert hat ein zweiter Einwurf
        # dasselbe Paar angelegt. Das ist kein Serverfehler, sondern eine
        # Dublette, die nur eine Wimpernschlaglaenge zu spaet auffiel.
        vorhanden = dublette_ueber_schluessel(portal, inserats_id)
        if vorhanden is not None:
            return _liegt_schon_vor(request, vorhanden)
        # Kein stilles Verschlucken: hier ist nichts gespeichert worden, und
        # eine Erfolgsmeldung waere eine Luege.
        messages.error(request, "Das Inserat konnte nicht angelegt werden.")
        return redirect("objektliste")
    messages.success(
        request,
        format_html(
            'Objekt angelegt. <a href="{}">ergänzen</a>',
            reverse("objekt", args=[objekt.pk]),
        ),
    )
    return redirect("objektliste")


class ObjektView(DetailView):
    """Ein Template, vier voneinander getrennte Aktionen. Kein Inline-Edit."""

    model = Objekt
    template_name = "objekte/objekt.html"
    context_object_name = "objekt"

    def get_queryset(self):
        # Nicht ueber `sichtbar()`: ein verworfenes Objekt muss aufrufbar
        # bleiben, sonst laesst es sich nie zurueckholen.
        return Objekt.objects.mit_qm_preis()

    def get_context_data(self, **kwargs):
        # Einmal holen, dann in Python teilen. Zwei Abfragen "meins" und
        # "die anderen" lieferten dasselbe und kosteten eine mehr.
        vota = list(self.object.vota.select_related("person"))
        kwargs.setdefault("vota", vota)
        kwargs.setdefault(
            "eigenes_votum",
            next((v for v in vota if v.person_id == self.request.user.pk), None),
        )
        kwargs.setdefault(
            "andere_vota", [v for v in vota if v.person_id != self.request.user.pk]
        )
        kwargs.setdefault("wertungen", Wertung.choices)
        kwargs.setdefault("statusauswahl", Status.choices)
        kwargs.setdefault("notizen", self.object.notizen.select_related("person"))
        kwargs.setdefault(
            "statusaenderungen", self.object.statusaenderungen.select_related("person")
        )
        return super().get_context_data(**kwargs)


class ObjektBearbeitenView(UpdateView):
    model = Objekt
    form_class = ObjektForm
    template_name = "objekte/objekt_bearbeiten.html"
    context_object_name = "objekt"

    def form_valid(self, form):
        """Die Reihenfolge der vier Schritte ist nicht beliebig.

        `objekt.save()` (3) liest `aktueller_preis` aus der Datenbank nach und
        ueberschreibt damit den Wert, den die Instanz aus dem Formular
        mitbringt. Erst danach traegt `objekt.aktueller_preis` den
        massgeblichen Preis - und nur gegen den darf der Vergleich in (4)
        laufen. Vorher verglichen, waere die Bezugsgroesse der Stand vom
        Oeffnen des Formulars; hat inzwischen jemand anders den Preis
        geaendert, entstuende ein Verlaufseintrag, der eine Aenderung
        behauptet, die niemand vorgenommen hat.

        (2) muss vor (3) liegen, sonst wird `zuletzt_geaendert_von` nicht
        mitgeschrieben.
        """
        objekt = form.save(commit=False)
        objekt.zuletzt_geaendert_von = self.request.user
        objekt.save()

        kaufpreis = form.cleaned_data.get("kaufpreis")
        # Ein LEERES Feld heisst "nicht aendern", nicht "Preis loeschen":
        # `Preisverlauf.preis` ist nicht nullbar, und ein Verlauf, aus dem
        # Eintraege verschwinden, waere keiner.
        if kaufpreis is not None and kaufpreis != objekt.aktueller_preis:
            objekt.preis_setzen(self.request.user, kaufpreis)

        self.object = objekt
        messages.success(self.request, "Gespeichert.")
        return redirect("objekt", pk=objekt.pk)


@require_POST
def votum_setzen(request, pk):
    """Ein Votum je Person und Objekt, jederzeit aenderbar.

    `update_or_create`, nicht `create`: das zweite Votum derselben Person
    ersetzt das erste. Ein `create` liefe in den Unique-Constraint und damit
    in einen 500er.
    """
    objekt = get_object_or_404(Objekt, pk=pk)
    wertung = request.POST.get("wertung", "")
    if wertung not in Wertung.values:
        messages.error(request, "Unbekannte Wertung.")
        return redirect("objekt", pk=pk)
    Votum.objects.update_or_create(
        objekt=objekt,
        person=request.user,
        defaults={
            "wertung": wertung,
            "begruendung": request.POST.get("begruendung", "").strip(),
        },
    )
    return redirect("objekt", pk=pk)


@require_POST
def status_setzen(request, pk):
    """Der Status wird immer manuell gesetzt, nie aus den Vota abgeleitet."""
    objekt = get_object_or_404(Objekt, pk=pk)
    neuer_status = request.POST.get("status", "")
    if neuer_status not in Status.values:
        messages.error(request, "Unbekannter Status.")
        return redirect("objekt", pk=pk)
    # `status_setzen()` gibt None zurueck, wenn sich nichts geaendert hat.
    # Dann auch keine Erfolgsmeldung - eine Bestaetigung fuer eine nicht
    # stattgefundene Aenderung ist eine Falschmeldung.
    if objekt.status_setzen(request.user, neuer_status) is not None:
        messages.success(request, f"Status steht auf „{objekt.get_status_display()}“.")
    return redirect("objekt", pk=pk)


@require_POST
def notiz_anlegen(request, pk):
    objekt = get_object_or_404(Objekt, pk=pk)
    text = request.POST.get("text", "").strip()
    if not text:
        messages.error(request, "Eine leere Notiz wird nicht gespeichert.")
        return redirect("objekt", pk=pk)
    Notiz.objects.create(objekt=objekt, person=request.user, text=text)
    return redirect("objekt", pk=pk)


# =========================================================================
# Der Lesezeichen-Zulauf: Vorschau (GET) und Uebernahme (POST)
# =========================================================================

#: Was das Lesezeichen uebergibt. Schluessel ist der Query-Parameter, Wert der
#: Feldname im Formular. Bewusst eine kurze, geschlossene Liste: was hier nicht
#: steht, wird nicht uebernommen - auch nicht, wenn es jemand an die URL haengt.
#: `preis` heisst im Formular `kaufpreis`, weil der Preis dort ueber ein
#: Zusatzfeld laeuft und nicht ueber die Modellspalte.
GELESENE_FELDER = {
    "titel": "titel",
    "beschreibung": "beschreibung",
    "preis": "kaufpreis",
    "wohnflaeche": "wohnflaeche",
    "zimmer": "zimmer",
}

#: Einheit hinter dem gelesenen Wert im Hinweis unter dem Feld.
EINHEITEN = {"kaufpreis": "€", "wohnflaeche": "m²"}

KEIN_LINK = "Kein Link übergeben. Öffne das Inserat und klicke das Lesezeichen erneut."

#: Bild-URLs laufen nicht durch das Formular - sie kommen als wiederholter
#: Parameter und werden einzeln geprueft.
BILD_MAXLAENGE = Bild._meta.get_field("url").max_length


class UebernehmenView(View):
    """Zwei Stationen auf einer Adresse, streng getrennt.

    GET zeigt, was gelesen wurde, und legt NICHTS an - ein Aufruf der
    Vorschau-URL, auch versehentlich, auch zweimal, veraendert nichts. Erst
    POST schreibt.

    Der Umweg ueber GET ist kein Formalismus, sondern loest zwei Dinge auf
    einmal: das Sitzungs-Cookie steht auf `SameSite=Lax` und kaeme bei einem
    POST von der Inseratsseite nicht mit - die Uebernahme liefe unangemeldet
    auf. Und die gelesenen Werte stammen aus einer Heuristik ueber fremdes
    Markup; sie gehoeren vor dem Speichern vor Augen.

    Kein `csrf_exempt`: der POST kommt von dieser Seite, nicht von aussen.
    """

    template_name = "objekte/uebernehmen.html"

    # --- GET: die Vorschau ------------------------------------------------

    def get(self, request):
        gepruefte = self._eingang(request, request.GET)
        if gepruefte is None:
            return redirect("objektliste")
        url, portal, inserats_id, vorhanden = gepruefte

        gelesen = self._gelesene_werte(request.GET)
        form = UebernahmeForm(instance=vorhanden)

        # Reihenfolge: erst die Hinweise, dann das Vorbelegen. `_vorbelegen`
        # schreibt in dieselbe `initial`, aus der `_hinweise` den Bestandswert
        # liest - umgekehrt herum haette jedes leere Feld einen Hinweis auf
        # sich selbst.
        hinweise = self._hinweise(form, gelesen) if vorhanden is not None else {}
        self._vorbelegen(form, gelesen)

        return self._zeigen(
            request,
            form=form,
            hinweise=hinweise,
            vorhanden=vorhanden,
            url=url,
            portal=portal,
            inserats_id=inserats_id,
            bilder=self._gelesene_bilder(request.GET),
        )

    # --- POST: die Uebernahme --------------------------------------------

    def post(self, request):
        gepruefte = self._eingang(request, request.POST)
        if gepruefte is None:
            return redirect("objektliste")
        url, portal, inserats_id, vorhanden = gepruefte

        bilder = self._gelesene_bilder(request.POST)
        form = UebernahmeForm(request.POST, instance=vorhanden)

        if not form.is_valid():
            # Kein leerer Bildschirm und keine Umleitung ohne Meldung: das
            # Formular kommt mit seinen Fehlern zurueck. Hinweise auf gelesene
            # Werte stehen dann nicht mehr darunter - die gelesenen Werte
            # stehen inzwischen in den Feldern, und der Vergleich, aus dem der
            # Hinweis entsteht, hat keine zweite Seite mehr.
            messages.error(request, "Bitte die markierten Felder prüfen.")
            return self._zeigen(
                request,
                form=form,
                hinweise={},
                vorhanden=vorhanden,
                url=url,
                portal=portal,
                inserats_id=inserats_id,
                bilder=bilder,
            )

        objekt = form.save(commit=False)
        neu = objekt.pk is None

        if neu:
            objekt.url = url
            # Aus der URL neu abgeleitet, nicht aus dem versteckten Feld
            # uebernommen: `portal_und_id()` ist rein und liefert zu derselben
            # URL denselben Wert - ein mitgeschicktes Paar koennte dagegen ein
            # fremdes sein und zwei Inserate am Unique-Index kollidieren
            # lassen.
            objekt.portal = portal
            objekt.inserats_id = inserats_id
            objekt.quelle = Quelle.URL_EINGEWORFEN
            objekt.eingestellt_von = request.user
        else:
            # `portal`, `inserats_id` und `url` des Bestands bleiben, wie sie
            # sind. Sie sind der Dublettenschluessel; ihn nebenbei aus einer
            # Heuristik zu ueberschreiben waere genau das stillschweigende
            # Ueberschreiben, das dieser Weg vermeiden soll.
            objekt.zuletzt_gesehen = timezone.now()

        objekt.zuletzt_geaendert_von = request.user

        try:
            # Eng um den einen Aufruf: ein gefangener `IntegrityError` macht in
            # einer noch offenen Transaktion jede folgende Abfrage unbrauchbar -
            # und genau danach fragt der `except`-Zweig die Datenbank noch einmal.
            with transaction.atomic():
                objekt.save()
        except IntegrityError:
            # Wettlauf: zwischen Vorschau und Uebernahme hat jemand dasselbe
            # Paar angelegt.
            vorhanden = dublette_ueber_schluessel(portal, inserats_id)
            if vorhanden is not None:
                return _liegt_schon_vor(request, vorhanden)
            messages.error(request, "Das Inserat konnte nicht angelegt werden.")
            return redirect("objektliste")

        self._preis_fortschreiben(request, objekt, form.cleaned_data.get("kaufpreis"))
        self._bilder_ergaenzen(objekt, bilder)

        messages.success(
            request, "Objekt übernommen." if neu else "Objekt ergänzt."
        )
        # Anders als der Einwurf, der auf die Liste zurueckfuehrt: hier hat die
        # Person gerade Daten geprueft und will sehen, was daraus wurde.
        return redirect("objekt", pk=objekt.pk)

    # --- gemeinsam --------------------------------------------------------

    def _eingang(self, request, daten):
        """URL pruefen und das bestehende Objekt suchen. `None` heisst: Abbruch.

        Dieselbe Behandlung wie beim Einwurf, durch Aufruf derselben
        Funktionen. Die Meldung ist dann schon gesetzt; der Aufrufer leitet um.
        """
        roh = daten.get("url", "").strip()
        if not roh:
            messages.error(request, KEIN_LINK)
            return None

        url, fehler = gepruefte_url(roh)
        if fehler:
            messages.error(request, fehler)
            return None

        portal, inserats_id = portal_und_id(url)
        return url, portal, inserats_id, bestehendes_objekt(url, portal, inserats_id)

    def _zeigen(self, request, *, form, hinweise, vorhanden, url, portal, inserats_id, bilder):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                # Feld und Hinweis paarweise: eine Vorlage kann ein Verzeichnis
                # nicht ueber einen veraenderlichen Schluessel nachschlagen.
                "felder": [(feld, hinweise.get(feld.name)) for feld in form],
                "objekt": vorhanden,
                "url": vorhanden.url if vorhanden is not None else url,
                "portal": vorhanden.portal if vorhanden is not None else portal,
                "inserats_id": (
                    vorhanden.inserats_id if vorhanden is not None else inserats_id
                ),
                "bilder": bilder,
            },
        )

    def _gelesene_werte(self, daten):
        """Die gelesenen Felder als Rohtext, leere weggelassen."""
        werte = {}
        for parameter, feldname in GELESENE_FELDER.items():
            wert = daten.get(parameter, "").strip()
            if wert:
                werte[feldname] = wert
        return werte

    def _gelesene_bilder(self, daten):
        """Die Bild-URLs, in der uebergebenen Reihenfolge, ohne Dubletten.

        Was zu lang ist oder den Validator nicht besteht, faellt heraus statt
        in einen Datenbankfehler zu laufen. Eine unbrauchbare Bildadresse ist
        kein Grund, die ganze Uebernahme abzubrechen.
        """
        gesehen = set()
        bilder = []
        for roh in daten.getlist("bilder"):
            wert = roh.strip()
            if not wert or wert in gesehen or len(wert) > BILD_MAXLAENGE:
                continue
            try:
                URLValidator()(wert)
            except ValidationError:
                continue
            gesehen.add(wert)
            bilder.append(wert)
        return bilder

    def _hinweise(self, form, gelesen):
        """Gelesene Werte, die vom Bestandswert abweichen - als fertiger Text.

        Nur wo ein Bestandswert steht: sonst ist der gelesene Wert bereits die
        Vorbelegung, und ein Hinweis darunter wiederholte ihn nur.

        Verglichen wird im Typ des Feldes, nicht als Zeichenkette. "120" und
        `Decimal("120.00")` sind derselbe Wert; als Text verglichen waeren sie
        verschieden, und unter jedem Zahlenfeld staende ein Hinweis, der nichts
        meldet. Gereinigt wird nur der gelesene Wert - der Bestandswert hat
        seinen Typ schon, und ihn durch `clean()` zu schicken hiesse, eine
        unlokalisierte `str()`-Ausgabe wieder lokalisiert zu lesen.
        """
        hinweise = {}
        for name, roh in gelesen.items():
            bestand = form.initial.get(name)
            if bestand is None or bestand == "":
                continue
            try:
                wert = form.fields[name].clean(roh)
            except ValidationError:
                # Unlesbar - dann steht der Rohwert da. Verschweigen waere
                # schlechter: der Bestandswert bliebe unwidersprochen stehen,
                # obwohl die Seite etwas anderes ausgewiesen hat.
                hinweise[name] = roh
                continue
            if wert != bestand:
                hinweise[name] = self._als_text(name, wert)
        return hinweise

    def _als_text(self, name, wert):
        if name in EINHEITEN:
            return (
                f"{formats.number_format(wert, decimal_pos=0, force_grouping=True)} "
                f"{EINHEITEN[name]}"
            )
        return formats.localize(wert)

    def _vorbelegen(self, form, gelesen):
        """Bestandswert schlaegt gelesenen Wert. Nichts wird ueberschrieben."""
        for name, roh in gelesen.items():
            if form.initial.get(name) in (None, ""):
                form.initial[name] = roh

    def _preis_fortschreiben(self, request, objekt, kaufpreis):
        """Ein Eintrag nur bei einem Preis, der vom juengsten abweicht.

        Der Vergleich laeuft gegen `objekt.aktueller_preis` NACH `save()`:
        `Objekt.save()` liest die Spalte aus der Datenbank nach, und erst
        danach traegt sie den massgeblichen Wert. Vorher verglichen, waere die
        Bezugsgroesse der Stand vom Oeffnen der Vorschau - hat inzwischen
        jemand anders den Preis geaendert, entstuende ein Eintrag, der eine
        Aenderung behauptet, die niemand vorgenommen hat.

        Kein uebermittelter Preis heisst "nicht aendern", nicht "Preis
        loeschen": ein Verlauf, aus dem Eintraege verschwinden, waere keiner.
        Und ein Verlauf aus identischen Werten ist auch keiner.
        """
        if kaufpreis is None or kaufpreis == objekt.aktueller_preis:
            return
        objekt.preis_setzen(request.user, kaufpreis, PreisQuelle.ERNEUTER_ABRUF)

    def _bilder_ergaenzen(self, objekt, bilder):
        """Anlegen, was noch nicht da ist. Vorhandenes bleibt unangetastet.

        Die Reihenfolge zaehlt weiter, wo der Bestand aufgehoert hat - sonst
        stuenden beim zweiten Aufruf mehrere Bilder auf derselben Zahl und die
        Sortierung fiele auf die ID zurueck.
        """
        vorhanden = set(objekt.bilder.values_list("url", flat=True))
        naechste = objekt.bilder.count()
        for url in bilder:
            if url in vorhanden:
                continue
            Bild.objects.create(objekt=objekt, url=url, reihenfolge=naechste)
            vorhanden.add(url)
            naechste += 1


class LesezeichenView(TemplateView):
    """Die Anleitung samt fertigem Lesezeichen zum Hineinziehen.

    Die Zieladresse wird GERENDERT und nicht hartkodiert:
    `request.build_absolute_uri()` liefert lokal `http://localhost:8347/…` und
    auf dem VPS die dortige Adresse. Ein hartkodierter Wert waere an genau
    einer der beiden Stellen falsch, und der Fehler faellt erst auf, wenn ein
    Klick auf das Lesezeichen ins Leere laeuft.
    """

    template_name = "objekte/lesezeichen.html"

    def get_context_data(self, **kwargs):
        ziel = self.request.build_absolute_uri(reverse("uebernehmen"))
        kwargs.setdefault("ziel", ziel)
        kwargs.setdefault("skript", skript_fuer(ziel))
        return super().get_context_data(**kwargs)
