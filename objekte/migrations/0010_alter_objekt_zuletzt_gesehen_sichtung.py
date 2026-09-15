"""Die Tabelle `Sichtung` - und der Bestand von `zuletzt_gesehen` auf NULL.

Der Datenschritt ist KEIN Nachtrag im Sinne von 0003, 0005 und 0008: die
traegen etwas nach, diese hier RAEUMT etwas weg.

Der Grund: `zuletzt_gesehen` ist ab jetzt die Projektion der Tabelle
`Sichtung`. Die Tabelle entsteht in dieser Migration leer. Bliebe an einem
Altobjekt ein Datum stehen, truege es eine Auskunft, zu der kein Eintrag
existiert - Feld und Tabelle widerspraechen sich ab Tag eins, und die
Objektansicht zeigte je nach Herkunft der Angabe zwei verschiedene Antworten
auf dieselbe Frage.

KEIN INFORMATIONSVERLUST. Das Feld wurde bisher an genau einer Stelle
gesetzt - beim Ergaenzen eines Bestandsobjekts ueber die Uebernahme - und die
Objekte, die es tragen, sind Testdaten, die vor dem Scharfstellen ohnehin
geleert werden (Entscheidung 14.09.).

Der Rueckweg loescht nichts und stellt nichts wieder her: die Werte sind weg,
und ein `RunPython.noop` sagt das ehrlich. Eine Umkehrfunktion, die
`zuletzt_gesehen` aus den Sichtungen neu berechnet, waere hier falsch - beim
Rueckwaertsmigrieren faellt die Tabelle unmittelbar danach weg.

Die lokale Datenbank ist leer, die auf dem Server nicht. Genau dieser
Unterschied hat am 14.09. schon einmal in die Irre gefuehrt (siehe 0009);
hier faellt er zugunsten des Datenschritts aus, weil dieser den Bestand
nicht INTERPRETIERT, sondern nur eine Spalte raeumt, deren Bedeutung sich
aendert.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def zuletzt_gesehen_leeren(apps, schema_editor):
    """Alle Bestandswerte auf NULL.

    Ueber das historische Modell und `update()`, nicht ueber `save()`: die
    Migration darf die Methoden des heutigen Modells nicht kennen - und
    `Objekt.save()` von heute liest den Preis nach und legte beim Anlegen
    Verlaufseintraege an.

    Laeuft VOR der `AlterField`: danach traegt das Feld `editable=False`,
    und das ist zwar nur die Django-Schicht und stuende einem `update()`
    nicht im Weg - aber die Reihenfolge "erst raeumen, dann verriegeln" ist
    die, die man liest, ohne sie nachschlagen zu muessen.
    """
    Objekt = apps.get_model("objekte", "Objekt")
    Objekt.objects.exclude(zuletzt_gesehen=None).update(zuletzt_gesehen=None)


class Migration(migrations.Migration):

    dependencies = [
        ('objekte', '0009_objekt_stadtteil'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            zuletzt_gesehen_leeren, migrations.RunPython.noop, elidable=False
        ),
        migrations.AlterField(
            model_name='objekt',
            name='zuletzt_gesehen',
            field=models.DateTimeField(blank=True, editable=False, help_text='Zeitpunkt der jüngsten Sichtung. Wird ausschließlich von `sichtung_eintragen()` geschrieben, nie von Hand.', null=True, verbose_name='zuletzt gesehen'),
        ),
        migrations.CreateModel(
            name='Sichtung',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('zeitpunkt', models.DateTimeField(auto_now_add=True, verbose_name='Zeitpunkt')),
                ('quelle', models.CharField(choices=[('lesezeichen', 'Lesezeichen'), ('von_hand', 'von Hand'), ('suchagent', 'Suchagent')], default='von_hand', max_length=20, verbose_name='Quelle')),
                ('objekt', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sichtungen', to='objekte.objekt', verbose_name='Objekt')),
                ('person', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='sichtungen', to=settings.AUTH_USER_MODEL, verbose_name='Person')),
            ],
            options={
                'verbose_name': 'Sichtung',
                'verbose_name_plural': 'Sichtungen',
                'ordering': ['-zeitpunkt', '-id'],
            },
        ),
    ]
