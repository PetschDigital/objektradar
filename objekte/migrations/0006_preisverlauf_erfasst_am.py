"""`Preisverlauf` bekommt einen Erfassungszeitpunkt neben dem Preisdatum.

Fuer die Markierung "seit deinem letzten Besuch" muss sich eine Bewegung
gegen eine Schwelle mit Uhrzeit pruefen lassen. `datum` ist ein `DateField`
und kann das nicht: Django wirft die Uhrzeit beim Vergleich still weg - aus
der Schwelle 2026-09-04 14:30 wird die nackte 2026-09-04 - und innerhalb
eines Tages gaebe es die Schwelle damit gar nicht.

`datum` bleibt deshalb unangetastet das fachliche Preisdatum. Daneben tritt
`erfasst_am` als Erfassungszeitpunkt. Sortierung, Preissenkungs-Logik und
Anzeige haengen weiter an `datum` und werden hier nicht angefasst.

DER BESTAND WIRD AUS `datum` ABGELEITET, nicht auf den Migrationszeitpunkt
gesetzt. Stuenden alle Alteintraege auf "jetzt", laege ihr Erfassungszeitpunkt
nach jeder bestehenden Besuchsschwelle - und beim naechsten Aufruf leuchtete
die halbe Liste gleichzeitig auf. Genau einmal, und danach traute niemand der
Marke mehr.

Abgeleitet wird auf MITTERNACHT ORTSZEIT des Preisdatums. Das ist bewusst
frueher als der wahre Erfassungszeitpunkt und nicht spaeter: die Marke zeigt
fuer Alteintraege dadurch im Zweifel zu wenig statt zu viel. Zu wenig faellt
niemandem auf, zu viel macht die Marke wertlos.

Drei Schritte in einer Datei, weil sie nur zusammen einen gueltigen Stand
ergeben: ein `auto_now_add`-Feld ist nicht nullbar, und ohne den mittleren
Schritt gaebe es keinen Wert fuer die Bestandszeilen.
"""

from datetime import datetime, time

from django.db import migrations, models
from django.utils import timezone


def aus_datum_ableiten(apps, schema_editor):
    """Mitternacht Ortszeit des Preisdatums, je Bestandszeile.

    Ueber `make_aware` und nicht ueber einen festen Versatz: Sommer- und
    Winterzeit unterscheiden sich um eine Stunde, und ein fester Versatz
    verschoebe die Haelfte des Bestands um genau diese Stunde ueber die
    Tagesgrenze.

    In einem Rutsch mit `bulk_update` statt `save()` je Zeile: der Bestand
    ist klein, aber eine Migration, die je Zeile schreibt, wird bei
    wachsendem Verlauf zur Wartungspause.
    """
    Preisverlauf = apps.get_model("objekte", "Preisverlauf")
    eintraege = list(Preisverlauf.objects.all().only("id", "datum"))
    for eintrag in eintraege:
        eintrag.erfasst_am = timezone.make_aware(
            datetime.combine(eintrag.datum, time.min)
        )
    Preisverlauf.objects.bulk_update(eintraege, ["erfasst_am"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("objekte", "0005_bestand_neue_portale_nachtragen"),
    ]

    operations = [
        # Erst nullbar und OHNE `auto_now_add`: mit `auto_now_add` bekaemen
        # die Bestandszeilen den Migrationszeitpunkt, und genau den sollen sie
        # nicht bekommen.
        migrations.AddField(
            model_name="preisverlauf",
            name="erfasst_am",
            field=models.DateTimeField(null=True, verbose_name="erfasst am"),
        ),
        # Rueckwaerts `noop`: die Spalte faellt im Schritt darueber ohnehin
        # weg, und ein Leeren waere kein Zurueckrollen, sondern ein
        # Datenverlust.
        migrations.RunPython(aus_datum_ableiten, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="preisverlauf",
            name="erfasst_am",
            field=models.DateTimeField(auto_now_add=True, verbose_name="erfasst am"),
        ),
    ]
