"""Das Feld `plz` anlegen und `stadtteil` als "Ortsteil" beschriften.

Zwei reine Schemaaenderungen, KEINE Datenmigration - ausdruecklich, und aus
demselben Grund wie in 0009: der Bestand besteht aus Testdaten und wird vor
dem Scharfstellen geleert. Kein Nachtrag zerlegt vorhandene Immowelt-Titel
rueckwirkend; hier gehoert keine `RunPython` hin.

Die `AlterField`-Operation aendert nur `verbose_name`. Die Spalte heisst
weiterhin `stadtteil`, und die Datenbank bekommt aus dieser Operation keine
Anweisung.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('objekte', '0010_alter_objekt_zuletzt_gesehen_sichtung'),
    ]

    operations = [
        migrations.AddField(
            model_name='objekt',
            name='plz',
            field=models.CharField(blank=True, default='', max_length=10, verbose_name='PLZ'),
        ),
        migrations.AlterField(
            model_name='objekt',
            name='stadtteil',
            field=models.CharField(blank=True, default='', max_length=150, verbose_name='Ortsteil'),
        ),
    ]
