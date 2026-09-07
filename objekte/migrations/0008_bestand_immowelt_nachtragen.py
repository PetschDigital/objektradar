"""Den Bestandsnachtrag ein drittes Mal laufen lassen - jetzt mit Immowelt.

Am 07.09. ist `immowelt` dazugekommen, an zwei echten Bestandsinseraten
belegt. Bestandsobjekte, die vor diesem Tag eingeworfen wurden, tragen deshalb
`sonstiges` ohne Dublettenschluessel, obwohl ihre URL jetzt zu einem bekannten
Muster passt. Diese Migration holt Portal und Inserats-ID fuer sie nach.

Sie fuehrt - wie 0005 - AUSDRUECKLICH die Funktion aus 0003 aus und baut sie
nicht nach. Zwei Gruende, und beide zaehlen unveraendert:

1. Die Regel bei Kollision - das AELTERE Objekt bekommt den Schluessel - ist
   dieselbe wie am 29.08. und am 02.09. Drei Kopien dieser Regel driften
   auseinander, und dann haengt an einem der Laeufe eine Entscheidung, die an
   den anderen fehlt. Genau diese Regel ist es, an der Vota und Notizen
   haengen.
2. 0003 liest die Muster ueber `portal_und_id` aus dem Anwendungscode. Die
   Funktion ist rein und deterministisch; mit dem Immowelt-Muster liefert
   derselbe Code fuer dieselben Bestandsdaten jetzt mehr Treffer. Genau das
   ist hier gewollt.

Dass die Kennung dabei auf Kleinschreibung normalisiert wird, kommt aus
derselben Quelle: `portal_und_id()` liefert sie bereits klein. Die Migration
kennt die Regel nicht und muss sie nicht kennen - eine zweite Fassung
daneben waere genau die Kopie, die 1. ausschliesst.

Der Lauf ist idempotent: schon vergebene Paare bleiben unangetastet, und
Objekte mit gesetztem Schluessel werden gar nicht erst geholt. Ein erneuter
Lauf auf einer Datenbank, auf der 0003 und 0005 bereits durchliefen, aendert
deshalb nur das, was das Immowelt-Muster hinzugewinnt.

Was diese Migration NICHT tut: sie schreibt keinen Schluessel zurueck, den
0003 oder 0005 bereits vergeben haben. Sie ruehrt insbesondere kein Objekt an,
das schon ein anderes Portal traegt - der Filter in 0003 holt nur Objekte mit
leerem Portal ODER leerer Inserats-ID, und die Menge `vergeben` haelt den Rest
ab.

VOR DEM BAU GEPRUEFT: in der Entwicklungsdatenbank liegt derzeit KEIN Objekt -
sie ist vollstaendig leer, nicht nur frei von Immowelt-URLs. Der Lauf ist dort
also ein Nichts. Die Migration steht trotzdem in der Kette, weil sie auf jeder
Datenbank laeuft, auf der Bestand liegt; das Immowelt-Testobjekt aus der Runde
vom 02.09. ist genau der Fall, fuer den sie gebaut ist. Gemeldet statt
stillschweigend uebergangen.
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
        # Die Reihenfolge ist nicht beliebig: liefe der Nachtrag VOR 0007,
        # schriebe er `immowelt` in eine Spalte, deren Auswahlliste den Wert
        # noch nicht kennt.
        ("objekte", "0007_alter_objekt_portal"),
    ]

    operations = [
        # Rueckwaerts `noop`, aus demselben Grund wie in 0003 und 0005: die
        # Felder wieder zu leeren waere kein Zurueckrollen, sondern ein
        # Datenverlust - beim Einwerfen geschriebene Paare stuenden mit denen
        # dieser Migration in derselben Spalte und liessen sich nicht
        # auseinanderhalten.
        migrations.RunPython(nachtragen, migrations.RunPython.noop),
    ]
