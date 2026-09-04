"""Middleware der Oberflaeche."""

from django.conf import settings


class BesuchMiddleware:
    """Schreibt die Aktivitaet fort und legt die Besuchsschwelle an `request`.

    Laeuft VOR der Ansicht, nicht danach. `besuch_registrieren()` dreht nach
    einer Pause die Schwelle fuer "seit deinem letzten Besuch" weiter -
    geschaehe das erst nach der Ansicht, zeigte der erste Aufruf eines neuen
    Besuchs noch die Schwelle des vorletzten und alles aus dem letzten Besuch
    erschiene ein zweites Mal als bewegt.

    `request.neu_seit` wird NACH dem Fortschreiben gelesen, und das ist die
    ganze Pointe der Reihenfolge: im ersten Aufruf eines neuen Besuchs steht
    damit bereits die frische Schwelle da. Andersherum gelesen bekaeme die
    Liste die Schwelle des vorletzten Besuchs.

    Gesetzt wird das Attribut nur fuer angemeldete Personen. Ansichten lesen
    es deshalb ueber `getattr(request, "neu_seit", None)` - fuer eine
    anonyme Anfrage soll hier nichts entstehen, was danach so aussieht, als
    haette sie eine Schwelle.

    Die Schreiblast ist eine zusaetzliche UPDATE-Anweisung je Aufruf. Das ist
    dieselbe Groessenordnung wie `SESSION_SAVE_EVERY_REQUEST`, das ohnehin
    gesetzt ist, damit die Jahres-Sitzung sich verlaengert.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # `getattr` statt `request.user`: ohne AuthenticationMiddleware gibt es
        # das Attribut nicht, und eine fehlende Middleware soll sich in der
        # Systempruefung melden, nicht hier als AttributeError.
        person = getattr(request, "user", None)
        if person is not None and person.is_authenticated:
            if not self._ist_statisch(request.path):
                person.besuch_registrieren()
            # NACH dem Fortschreiben. Siehe Klassendocstring.
            request.neu_seit = person.neu_seit
        return self.get_response(request)

    @staticmethod
    def _ist_statisch(pfad):
        """Anfragen auf statische Dateien schreiben nichts fort.

        Jeder Aufruf schreibt in die Datenbank; fuer ein Stylesheet ist das
        ein Schreibzugriff ohne Gegenwert. Schlimmer noch: eine Seite zieht
        mehrere statische Dateien nach, und jede einzelne davon verschoebe
        `letzter_besuch` weiter - die Besuchspause liefe damit nicht ab dem
        letzten Klick, sondern ab dem letzten Bild.

        Im Betrieb liefert Caddy die statischen Dateien selbst aus und die
        Anfragen erreichen Django gar nicht; lokal schon.

        `settings.STATIC_URL` ist von Django auf `/static/` normalisiert und
        traegt den fuehrenden Schraegstrich, passt also unmittelbar auf
        `request.path`. Zeigt sie auf eine fremde Adresse - ein CDN etwa -,
        greift die Pruefung nicht und schadet auch nicht: dann kommen diese
        Anfragen ohnehin nie hier an.
        """
        static_url = settings.STATIC_URL
        return bool(static_url) and pfad.startswith(static_url)
