from decimal import Decimal

from django import forms
from django.db.models import Q

from .choices import STATUS_AUSGEBLENDET, Land, Objekttyp, Portal, Status, Zustand
from .models import Objekt

#: Felder, die als lokalisierter Text laufen muessen. Siehe `als_dezimaltext`.
DEZIMALFELDER = ("wohnflaeche", "grundstuecksgroesse", "zimmer", "wert_nach_renovierung")

#: Was das Lesezeichen mitbringt und niemand im Vorschauformular tippt. Steht
#: auf Modulebene, weil eine geschachtelte `Meta` waehrend ihres Klassenkoerpers
#: nicht auf ein Attribut der umgebenden Klasse zugreifen kann.
VERSTECKTE_FELDER = ("url", "portal", "inserats_id")


def als_dezimaltext(feld):
    """Lokalisiertes Dezimalfeld in einem `TextInput` statt in `NumberInput`.

    Mit `USE_THOUSAND_SEPARATOR` rendert ein lokalisierter Wert als
    "285.000,00". Ein `<input type="number">` haelt das nicht fuer eine Zahl
    und raeumt das Feld beim Anzeigen KOMMENTARLOS leer - wer dann speichert,
    loescht den Wert, ohne je etwas getippt zu haben. `type="text"` mit
    `inputmode="decimal"` zeigt am Handy dieselbe Zifferntastatur und laesst
    den Wert stehen.

    `localize` muss an beiden Stellen gesetzt werden: am Feld fuer das LESEN
    (`sanitize_separators` beim Absenden) und am Widget fuer das SCHREIBEN.
    `Field.__init__` verbindet beide, danach zugewiesen bleibt das Widget
    unlokalisiert - und dann rendert es "285000.00" und liest "285.000,00"
    als 28500000.
    """
    feld.localize = True
    feld.widget = forms.TextInput(attrs={"inputmode": "decimal"})
    feld.widget.is_localized = True


class ObjektForm(forms.ModelForm):
    """Bearbeiten-Formular der Objektansicht.

    `status` fehlt bewusst: er laeuft ueber `status_setzen()`, das die
    Aenderung protokolliert. Ein Formularfeld erzeugte keinen Eintrag, und
    dann gaebe es zwei Wege, von denen nur einer nachvollziehbar ist.
    """

    kaufpreis = forms.DecimalField(
        label="Kaufpreis (€)",
        required=False,
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=2,
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
        help_text=(
            "Leer heißt: nicht ändern. Ein einmal erfasster Preis lässt sich hier "
            "nicht zurücknehmen — der Preisverlauf führt, und ein Verlauf, aus dem "
            "Einträge verschwinden, wäre keiner."
        ),
    )

    class Meta:
        model = Objekt
        # `aktueller_preis` fehlt nicht aus Versehen: es ist `editable=False`
        # und faellt aus jedem ModelForm heraus. Erfasst wird der Preis ueber
        # das Zusatzfeld `kaufpreis` oben.
        fields = [
            "url",
            "portal",
            "inserats_id",
            "titel",
            "ort",
            "land",
            "region",
            "objekttyp",
            "wohnflaeche",
            "grundstuecksgroesse",
            "zimmer",
            "baujahr",
            "beschreibung",
            "zustand",
            "wert_nach_renovierung",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["kaufpreis"] = self.instance.aktueller_preis
        for name in DEZIMALFELDER:
            als_dezimaltext(self.fields[name])


class UebernahmeForm(ObjektForm):
    """Das Vorschauformular des Lesezeichen-Zulaufs.

    Dieselben Felder wie beim Bearbeiten, ohne die drei, die das Lesezeichen
    mitbringt: `url`, `portal` und `inserats_id` stehen als versteckte Felder
    im Formular und werden nicht von Hand gepflegt. Die Feldliste wird aus
    `ObjektForm.Meta` ABGELEITET und nicht abgeschrieben - eine zweite Liste
    driftet von der ersten weg, und das faellt erst auf, wenn ein neues Feld
    an einer der beiden Stellen fehlt.
    """

    class Meta(ObjektForm.Meta):
        fields = [f for f in ObjektForm.Meta.fields if f not in VERSTECKTE_FELDER]


# =========================================================================
# Der Listenfilter
# =========================================================================

#: Vorbelegung des Statusfilters: alles ausser den ausgeblendeten Status.
#: ABGELEITET statt abgeschrieben. "Verworfene Objekte werden ausgeblendet,
#: nicht geloescht" ist EINE Zusage, und sie steht bereits in
#: `STATUS_AUSGEBLENDET`. Eine zweite, von Hand gefuehrte Liste driftet davon
#: weg, und dann blendet die Liste etwas anderes aus als `sichtbar()`.
STATUS_VORBELEGUNG = tuple(s for s in Status if s not in STATUS_AUSGEBLENDET)

#: Erster Eintrag jeder Einfachauswahl. Der leere Wert heisst "nicht filtern"
#: und darf nicht in die Abfrage laufen - siehe `filtern()`.
ALLE = ("", "alle")

#: Formularfeld -> Lookup. Alles, was ohne Sonderbehandlung auskommt.
#: `suche` und `status` stehen nicht hier: die eine ist ODER-verknuepft ueber
#: vier Spalten, der andere hat eine Vorbelegung.
EINFACHE_FILTER = {
    "land": "land",
    "portal": "portal",
    "objekttyp": "objekttyp",
    "zustand": "zustand",
    "preis_von": "aktueller_preis__gte",
    "preis_bis": "aktueller_preis__lte",
    "flaeche_von": "wohnflaeche__gte",
    "flaeche_bis": "wohnflaeche__lte",
    "region": "region__icontains",
}

#: Spalten, die der Freitext absucht - ODER-verknuepft, `icontains`.
SUCHSPALTEN = ("titel", "ort", "region", "beschreibung")

#: Parameter, die keine Filterung sind. Sie loesen die Trefferanzeige nicht
#: aus - wer nur blaettert oder sortiert, filtert nicht.
KEINE_FILTER = frozenset({"sortierung", "seite"})


class StatusAuswahlfeld(forms.MultipleChoiceField):
    """Wie `MultipleChoiceField`, aber leere Eintraege fallen heraus.

    `?status=` traegt einen leeren Wert. Ohne dieses Aussortieren liefe er in
    "Treffen Sie eine gueltige Auswahl" - das Feld waere ungueltig, faellt aus
    `cleaned_data` heraus und der Filter fiele auf die Vorbelegung zurueck.
    Genau das soll `?status=` NICHT tun: es heisst "keiner der Werte" und
    liefert null Treffer. Siehe `ObjektFilterForm.statusauswahl()`.
    """

    def to_python(self, value):
        return [wert for wert in super().to_python(value) if wert != ""]


class ObjektFilterForm(forms.Form):
    """Der Listenfilter. Gelesen aus GET, damit ein gefilterter Stand teilbar ist.

    Ein normales `forms.Form`: ein `ModelForm` traegt Pflichtfelder und
    Validierung, die hier beide falsch waeren, und `django-filter` waere eine
    neue Abhaengigkeit fuer elf Zeilen `filter()`.

    Alle Felder `required=False`. Ein leeres Formular ist der Normalfall, kein
    Fehler.
    """

    suche = forms.CharField(label="Suche", required=False)
    status = StatusAuswahlfeld(
        label="Status",
        required=False,
        choices=Status.choices,
        widget=forms.CheckboxSelectMultiple,
    )
    land = forms.ChoiceField(label="Land", required=False, choices=[ALLE, *Land.choices])
    portal = forms.ChoiceField(
        label="Portal", required=False, choices=[ALLE, *Portal.choices]
    )
    objekttyp = forms.ChoiceField(
        label="Objekttyp", required=False, choices=[ALLE, *Objekttyp.choices]
    )
    zustand = forms.ChoiceField(
        label="Zustand", required=False, choices=[ALLE, *Zustand.choices]
    )
    preis_von = forms.DecimalField(label="Preis ab (€)", required=False, max_digits=12,
                                   decimal_places=2)
    preis_bis = forms.DecimalField(label="Preis bis (€)", required=False, max_digits=12,
                                   decimal_places=2)
    flaeche_von = forms.DecimalField(label="Fläche ab (m²)", required=False, max_digits=8,
                                     decimal_places=2)
    flaeche_bis = forms.DecimalField(label="Fläche bis (m²)", required=False, max_digits=8,
                                     decimal_places=2)
    region = forms.CharField(label="Region", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dieselbe Behandlung wie im Bearbeiten-Formular, aus demselben Grund:
        # ein `<input type="number">` raeumt einen lokalisiert gerenderten Wert
        # ("285.000,00") beim Anzeigen kommentarlos leer.
        for name in ("preis_von", "preis_bis", "flaeche_von", "flaeche_bis"):
            als_dezimaltext(self.fields[name])

    # --- die Auswertung ---------------------------------------------------

    def statusauswahl(self):
        """Die Statuswerte, nach denen gefiltert wird. Drei Faelle, streng getrennt.

        Der Parameter FEHLT ganz - dann gilt die Vorbelegung: Verworfene und
        vom Markt Genommene bleiben draussen.

        Der Parameter steht drin und ist LEER - dann sind es null Werte und
        damit null Treffer. Es wird ausdruecklich NICHT auf die Vorbelegung
        zurueckgefallen: sonst liesse sich eine leere Auswahl gar nicht
        ausdruecken, und das Kontrollkaestchen "keiner" waere ohne Wirkung.

        Der Parameter steht drin und ist UNGUELTIG - dann ist er abgewiesen,
        das Feld faellt aus `cleaned_data` heraus, und es gilt die Vorbelegung.
        Gleiche Haltung wie bei Sortierung und Seitenzahl: ein unbrauchbarer
        Parameter faellt still auf den Standard, statt einen 500er zu werfen.
        """
        if "status" not in self.data:
            return list(STATUS_VORBELEGUNG)
        self.is_valid()  # fuellt `cleaned_data`; wiederholte Aufrufe kosten nichts
        gewaehlt = self.cleaned_data.get("status")
        if gewaehlt is None:
            return list(STATUS_VORBELEGUNG)
        return gewaehlt

    def filtern(self, objekte):
        """Wendet den Filter an und gibt das eingeschraenkte Queryset zurueck.

        Ein NICHT gesetzter Filter fasst die Abfrage nicht an - auch nicht mit
        einem `Q()`, das "alles" bedeutet. `land`, `portal` und `objekttyp`
        sind `blank=True, default=""`; ein Filter, der den leeren Wert
        mitpruefte, verbaerge den kompletten Bestand.

        Gewollte Nebenwirkung: ein GESETZTER Filter auf `land=ES` verbirgt
        Objekte OHNE Land. Das ist richtig so - wer nach Spanien filtert, will
        keine Objekte sehen, von denen niemand weiss, wo sie stehen.
        """
        self.is_valid()  # fuellt `cleaned_data`; ungueltige Felder fallen heraus
        objekte = objekte.filter(status__in=self.statusauswahl())

        suche = self.cleaned_data.get("suche")
        if suche:
            bedingung = Q()
            for spalte in SUCHSPALTEN:
                bedingung |= Q(**{f"{spalte}__icontains": suche})
            objekte = objekte.filter(bedingung)

        for name, lookup in EINFACHE_FILTER.items():
            wert = self.cleaned_data.get(name)
            # Nicht `if not wert`: eine 0 als Untergrenze ist ein gesetzter
            # Filter, und ein leerer Text ist keiner.
            if wert is None or wert == "":
                continue
            objekte = objekte.filter(**{lookup: wert})

        return objekte

    def ist_gefiltert(self):
        """Ob ueberhaupt gefiltert wurde - fuer die Trefferanzeige.

        Gemessen an der ANWESENHEIT des Parameters, nicht an seinem Wert:
        `?status=` filtert auf null Treffer und muss die Anzeige ausloesen,
        sonst stuende die leere Liste ohne Erklaerung da.
        """
        return any(name not in KEINE_FILTER for name in self.data)
