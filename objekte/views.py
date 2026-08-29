from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import CharField, F, Func, Value
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, UpdateView

from .choices import Quelle, Status, Wertung
from .forms import ObjektForm
from .models import Notiz, Objekt, Votum
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


def _liegt_schon_vor(request, vorhanden):
    """Die Antwort auf eine erkannte Dublette - an genau einer Stelle.

    Zweimal gebraucht: einmal nach der Pruefung, einmal nachdem ein Wettlauf
    am Unique-Index aufgeschlagen ist. Fuer die einwerfende Person darf sich
    der zweite Fall vom ersten nicht unterscheiden, und zwei Kopien derselben
    drei Zeilen driften genau darin auseinander.
    """
    messages.info(request, "Das Inserat liegt schon in der Liste.")
    return redirect("objekt", pk=vorhanden.pk)


class ObjektlisteView(ListView):
    """Die Liste. Das Einwerfen liegt in `objekt_anlegen` auf eigener Adresse."""

    template_name = "objekte/objektliste.html"
    context_object_name = "objekte"

    def get_queryset(self):
        # `sichtbar()` haelt Verworfene und vom Markt Genommene draussen -
        # geloescht wird nichts, sie sind nur nicht im Weg. `mit_qm_preis()`
        # ist der einzige Weg zum €/m²; es gibt bewusst keine Property.
        return Objekt.objects.sichtbar().mit_qm_preis()


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

    # Vor Laengenpruefung und Validator: was hier herauskommt, ist der Wert,
    # der gespeichert wird - und nur der darf gemessen und geprueft werden.
    url = mit_schema(url)

    if len(url) > URL_MAXLAENGE:
        # Vor dem Validator, aus zwei Gruenden: die Feldlaenge wird sonst
        # nirgends geprueft (seit Abschnitt 6 steht kein Formular mehr davor,
        # ein zu langer Link liefe in einen Datenbankfehler und damit in einen
        # 500er), und ein sehr langer Text gehoert nicht erst durch einen
        # regulaeren Ausdruck geschickt.
        messages.error(request, f"Der Link ist länger als {URL_MAXLAENGE} Zeichen.")
        return redirect("objektliste")

    try:
        URLValidator()(url)
    except ValidationError:
        messages.error(request, "Das ist kein gültiger Link.")
        return redirect("objektliste")

    # Beide Werte stehen in der URL selbst und kosten keinen Seitenabruf.
    # Damit wird der Dublettenschutz echt - unabhaengig von Schritt 2 und von
    # jeder Sperre auf Portalseite.
    portal, inserats_id = portal_und_id(url)

    # Zweistufig, und die Reihenfolge ist nicht beliebig. Stufe 1 traegt genau
    # das, woran Stufe 2 scheitert: dasselbe Inserat unter anderem
    # Sprachpraefix, mit Tracking-Parametern oder ueber eine andere
    # Laenderdomain. Stufe 2 laeuft nur, wenn Stufe 1 nichts findet - fuer
    # Altbestand und fuer Portale ohne bekanntes Muster.
    vorhanden = dublette_ueber_schluessel(portal, inserats_id)
    if vorhanden is None:
        vorhanden = dublette(url)
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
