"""Middleware der Oberflaeche."""


class BesuchMiddleware:
    """Schreibt bei jedem Aufruf die Aktivitaet der angemeldeten Person fort.

    Laeuft VOR der Ansicht, nicht danach. `besuch_registrieren()` dreht nach
    einer Pause die Schwelle fuer "neu seit deinem letzten Besuch" weiter -
    geschaehe das erst nach der Ansicht, zeigte der erste Aufruf eines neuen
    Besuchs noch die Schwelle des vorletzten und alles aus dem letzten Besuch
    erschiene ein zweites Mal als neu.

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
            person.besuch_registrieren()
        return self.get_response(request)
