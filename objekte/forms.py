from decimal import Decimal

from django import forms

from .models import Objekt

#: Felder, die als lokalisierter Text laufen muessen. Siehe `als_dezimaltext`.
DEZIMALFELDER = ("wohnflaeche", "grundstuecksgroesse", "zimmer", "wert_nach_renovierung")


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
