"""Portal und Inserats-ID an Bestandsobjekten nachtragen.

Bis Schritt 2a blieben beide Felder leer, weil erst der Parser aus Schritt 2
sie fuellen sollte. Damit griff der partielle Unique-Index nie. Diese Migration
holt fuer den Bestand nach, was `objekt_anlegen()` ab jetzt beim Einwerfen tut.

Sie importiert `portal_und_id` aus dem Anwendungscode. Das koppelt eine
Migration an Code, der sich aendern kann - hier vertretbar, weil die Funktion
rein und deterministisch ist und ein spaeter besseres Muster auch fuer
Bestandsdaten das gewuenschte Ergebnis waere.
"""

from django.db import migrations, models

from objekte.portale import portal_und_id


def nachtragen(apps, schema_editor):
    """Idempotent, und laeuft auch auf einer Datenbank mit Altdubletten durch.

    Der Riegel ist die Menge `vergeben`: ohne sie schlaegt der zweite Schreiber
    desselben Paares am Unique-Index auf, die Migration bricht ab und
    hinterlaesst eine halb migrierte Datenbank - der Zustand, aus dem heraus
    ein erneuter Lauf nicht mehr sauber ist. Ein bereits vergebenes Paar laesst
    das Objekt deshalb unangetastet, statt es zu ueberschreiben. Welches der
    beiden Objekte den Schluessel bekommt, entscheidet die Reihenfolge des
    Laufs; das ist hinnehmbar, weil beide auf dasselbe Inserat zeigen und der
    URL-Vergleich sie weiterhin traegt.

    Paare, die im selben Lauf entstehen, zaehlen mit - sonst saehe die Menge
    nur den Stand vom Beginn und zwei Bestandsobjekte auf dasselbe Inserat
    liefen doch in den Index.
    """
    Objekt = apps.get_model("objekte", "Objekt")

    vergeben = set(
        Objekt.objects.exclude(portal="")
        .exclude(inserats_id="")
        .values_list("portal", "inserats_id")
    )

    # `order_by` ist hier keine Kosmetik. Zeigen zwei Bestandsobjekte auf
    # dasselbe Inserat, bekommt das ERSTE der Reihenfolge den Schluessel und
    # das zweite bleibt leer. Ohne feste Reihenfolge entschiede das die
    # Datenbank - und faellt sie auf das juengere, liefe jeder kuenftige
    # Einwurf ueber `dublette_ueber_schluessel()` genau dorthin, waehrend Vota
    # und Notizen am aelteren haengen. Dieselbe Regel wie dort: das aelteste
    # gewinnt.
    for objekt in Objekt.objects.filter(
        models.Q(portal="") | models.Q(inserats_id="")
    ).order_by("eingestellt_am", "id").iterator():
        portal, inserats_id = portal_und_id(objekt.url)
        if not portal or not inserats_id:
            continue
        if (portal, inserats_id) in vergeben:
            continue

        objekt.portal = portal
        objekt.inserats_id = inserats_id
        # `update_fields` haelt `zuletzt_geaendert_am` (auto_now) still: ein
        # Nachtrag ist keine Aenderung, die jemand vorgenommen hat, und die
        # Spalte ist genau die Angabe, an der man das ablesen wuerde.
        objekt.save(update_fields=["portal", "inserats_id"])
        vergeben.add((portal, inserats_id))


class Migration(migrations.Migration):
    dependencies = [
        ("objekte", "0002_alter_objekt_options"),
    ]

    operations = [
        # Rueckwaerts `noop`: die Felder wieder zu leeren waere kein
        # Zurueckrollen, sondern ein Datenverlust - beim Einwerfen geschriebene
        # Paare stuenden mit denen dieser Migration in derselben Spalte und
        # liessen sich nicht auseinanderhalten.
        migrations.RunPython(nachtragen, migrations.RunPython.noop),
    ]
