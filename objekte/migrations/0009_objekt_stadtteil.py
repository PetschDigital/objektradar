"""Das Feld `stadtteil` anlegen - reine Schemaaenderung, KEINE Datenmigration.

Das Fehlen einer Datenmigration ist ausdruecklich gewollt und keine
Auslassung. Die vorhandenen Objekte sind Testdaten und werden vor dem
Scharfstellen geloescht. Ein Nachtrag, der `stadtteil` rueckwirkend aus
gespeicherten Titeln zerlegt, wendete die Segmentregel auf Daten an, die
niemand geprueft hat - und die Regel ruht auf acht Titeln aus zwei Gemeinden.

DIE KONSTELLATION TAEUSCHT, deshalb steht es hier: Die lokale Datenbank ist
leer, die auf dem Server nicht. Am 07.09. war daraus abgeleitet worden, dass
ein Bestandsnachtrag noetig ist - richtig damals (0008), falsch hier. Der
Unterschied ist, dass der Bestand diesmal ohnehin verworfen wird. Wer diese
Migration spaeter neben 0003, 0005 und 0008 sieht, soll das Muster nicht
fortsetzen: hier gehoert keine `RunPython` hin.

Bezeugt wird das - siehe `StadtteilFeldTests` -, damit ein spaeter
dazugelegter Nachtrag nicht still durchgeht.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('objekte', '0008_bestand_immowelt_nachtragen'),
    ]

    operations = [
        migrations.AddField(
            model_name='objekt',
            name='stadtteil',
            field=models.CharField(blank=True, default='', max_length=150, verbose_name='Stadtteil'),
        ),
    ]
