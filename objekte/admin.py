from django.contrib import admin

from .choices import Status
from .models import Bild, Notiz, Objekt, Preisverlauf, Statusaenderung, Votum


def _statuswechsel(ziel):
    """Baut eine Admin-Action, die den Status ueber `status_setzen()` aendert.

    Nicht ueber das Formular: `status` ist readonly, weil ein Formularfeld
    keine Statusaenderung protokollieren wuerde. Die Action geht denselben Weg
    wie spaeter die Oberflaeche und traegt `request.user` als Person ein.
    """

    def aktion(modeladmin, request, queryset):
        geaendert = 0
        for objekt in queryset:
            if objekt.status_setzen(request.user, ziel) is not None:
                geaendert += 1
        unveraendert = len(queryset) - geaendert
        text = f"{geaendert} Objekt(e) auf „{ziel.label}“ gesetzt."
        if unveraendert:
            text += f" {unveraendert} standen bereits darauf."
        modeladmin.message_user(request, text)

    aktion.__name__ = f"status_auf_{ziel.value}"
    aktion.short_description = f"Status auf „{ziel.label}“ setzen"
    return aktion


STATUS_ACTIONS = [_statuswechsel(s) for s in Status]


class BildInline(admin.TabularInline):
    model = Bild
    extra = 0


class PreisverlaufInline(admin.TabularInline):
    model = Preisverlauf
    extra = 0
    ordering = ["-datum", "-id"]


class VotumInline(admin.TabularInline):
    model = Votum
    extra = 0


class NotizInline(admin.TabularInline):
    model = Notiz
    extra = 0
    readonly_fields = ["erstellt_am"]


class StatusaenderungInline(admin.TabularInline):
    model = Statusaenderung
    extra = 0
    can_delete = False
    readonly_fields = ["person", "alter_status", "neuer_status", "datum"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Objekt)
class ObjektAdmin(admin.ModelAdmin):
    """Notfallkorrekturen und Kontrolle. Ersetzt nicht die eigentliche Oberfläche."""

    list_display = [
        "__str__",
        "ort",
        "region",
        "land",
        "wohnflaeche",
        "aktueller_preis",
        "qm_preis_anzeige",
        "zustand",
        "status",
    ]
    list_filter = ["status", "zustand", "land", "objekttyp", "portal", "quelle"]
    search_fields = ["titel", "ort", "region", "url", "inserats_id"]
    # `aktueller_preis` und `status` haben eine Historientabelle und werden
    # deshalb hier nicht editiert. Ein Statuswechsel im Formular erzeugte
    # keine Statusaenderung, eine Preisaenderung keinen Verlaufseintrag - der
    # Weg fuehrt ueber `preis_setzen()` bzw. `status_setzen()`. Der Preis
    # laesst sich im Admin ueber den Preisverlauf-Inline setzen.
    readonly_fields = [
        "aktueller_preis",
        "status",
        "eingestellt_von",
        "eingestellt_am",
        "zuletzt_geaendert_von",
        "zuletzt_geaendert_am",
    ]
    inlines = [BildInline, PreisverlaufInline, VotumInline, NotizInline, StatusaenderungInline]
    actions = STATUS_ACTIONS

    def get_queryset(self, request):
        return super().get_queryset(request).mit_qm_preis()

    @admin.display(description="€/m²", ordering="qm_preis")
    def qm_preis_anzeige(self, obj):
        return "—" if obj.qm_preis is None else f"{obj.qm_preis:.0f}"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.eingestellt_von = request.user
        obj.zuletzt_geaendert_von = request.user
        super().save_model(request, obj, form, change)


@admin.register(Statusaenderung)
class StatusaenderungAdmin(admin.ModelAdmin):
    list_display = ["objekt", "person", "alter_status", "neuer_status", "datum"]
    list_filter = ["neuer_status"]
    readonly_fields = ["objekt", "person", "alter_status", "neuer_status", "datum"]

    def has_add_permission(self, request):
        return False
