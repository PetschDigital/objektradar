"""Den Bestandsnachtrag ein zweites Mal laufen lassen - jetzt mit den drei
neuen Portalen.

Am 02.09. sind `fotocasa`, `milanuncios` und `pisos` dazugekommen und
`idealista.it`/`idealista.pt` herausgefallen. Bestandsobjekte, die vor diesem
Tag eingeworfen wurden, haben deshalb keinen Dublettenschluessel, obwohl ihre
URL jetzt zu einem bekannten Muster passt. Diese Migration holt das nach.

Sie fuehrt AUSDRUECKLICH die Funktion aus 0003 aus und baut sie nicht nach.
Zwei Gruende, und beide zaehlen:

1. Die Regel bei Kollision - das AELTERE Objekt bekommt den Schluessel - ist
   dieselbe wie am 29.08. Zwei Kopien dieser Regel driften auseinander, und
   dann haengt an einem der beiden Laeufe eine Entscheidung, die am anderen
   fehlt. Genau diese Regel ist es, an der Vota und Notizen haengen.
2. 0003 liest die Muster ueber `portal_und_id` aus dem Anwendungscode. Die
   Funktion ist rein und deterministisch; mit den erweiterten Mustern liefert
   derselbe Code fuer dieselben Bestandsdaten jetzt mehr Treffer. Genau das
   ist hier gewollt.

Der Lauf ist idempotent: schon vergebene Paare bleiben unangetastet, und
Objekte mit gesetztem Schluessel werden gar nicht erst geholt. Ein erneuter
Lauf auf einer Datenbank, auf der 0003 bereits durchlief, aendert deshalb nur
das, was die neuen Muster hinzugewinnen.

Was diese Migration NICHT tut: sie schreibt keinen Schluessel zurueck, den
0003 bereits vergeben hat. `idealista.it` und `idealista.pt` sind
herausgefallen - haette ein Bestandsobjekt darueber einen Schluessel bekommen,
bliebe der stehen. Vor dem Bau geprueft: eine `.it`- oder `.pt`-URL liegt im
Bestand nicht vor. Waere eine da gewesen, waere sie gemeldet und nicht
stillschweigend umgeschrieben worden.
"""

from importlib import import_module

from django.db import migrations

#: Der Modulname faengt mit einer Ziffer an - ein `import` schreibt sich dafuer
#: nicht hin.
nachtragen = import_module(
    "objekte.migrations.0003_portal_und_inserats_id_nachtragen"
).nachtragen


class Migration(migrations.Migration):
    dependencies = [
        ("objekte", "0004_alter_objekt_portal"),
    ]

    operations = [
        # Rueckwaerts `noop`, aus demselben Grund wie in 0003: die Felder
        # wieder zu leeren waere kein Zurueckrollen, sondern ein Datenverlust -
        # beim Einwerfen geschriebene Paare stuenden mit denen dieser Migration
        # in derselben Spalte und liessen sich nicht auseinanderhalten.
        migrations.RunPython(nachtragen, migrations.RunPython.noop),
    ]
