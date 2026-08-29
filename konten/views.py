"""Anmeldung.

Es gibt bewusst keinen Registrierungsweg. Konten legt `make superuser` an.
"""

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache


def sperrschluessel(request):
    """Zaehlerschluessel je Absender und Benutzername.

    Hinter einem Reverse Proxy ist `REMOTE_ADDR` die Adresse des Proxys, nicht
    die des Anfragenden. Der Schluessel faellt dann auf "je Benutzername"
    zusammen. Das ist die gewollte Richtung des Fehlers - der Zaehler wird
    dadurch strenger, nicht laxer. Ein `X-Forwarded-For` wird bewusst NICHT
    gelesen: der Kopf ist frei waehlbar, solange kein Proxy davorsteht, der ihn
    ueberschreibt, und ein frei waehlbarer Schluessel hebt das Limit auf.
    """
    absender = request.META.get("REMOTE_ADDR", "")
    benutzer = (request.POST.get("username") or "").strip().lower()
    return f"anmeldeversuche:{absender}:{benutzer}"


class AnmeldeView(LoginView):
    """Anmeldung mit Rate-Limit.

    Das Limit sitzt am Absenden, nicht am Formular: gesperrt wird auch, wer
    beim sechsten Versuch das richtige Passwort trifft. Ein Limit, das nur
    falsche Eingaben abweist, waere keins - genau der Treffer soll nicht mehr
    durchkommen.
    """

    template_name = "konten/login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if cache.get(sperrschluessel(request), 0) >= settings.LOGIN_VERSUCHE:
            return self.render_to_response(
                self.get_context_data(
                    form=self.get_form(),
                    gesperrt=True,
                    # Die Dauer kommt aus den Einstellungen, nicht als Zahl in
                    # den Text. Eine Zahl im Template faellt bei einer Aenderung
                    # STILL aus - die Seite behauptete dann eine Frist, die
                    # nicht mehr gilt.
                    sperre_minuten=settings.LOGIN_SPERRE_SEKUNDEN // 60,
                ),
                status=429,
            )
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        cache.delete(sperrschluessel(self.request))
        return super().form_valid(form)

    def form_invalid(self, form):
        schluessel = sperrschluessel(self.request)
        # `add` setzt nur, wenn der Schluessel fehlt. Damit laeuft die Sperrzeit
        # ab dem ersten Fehlversuch. Wuerde jeder Fehlversuch die Frist neu
        # setzen, hielte ein Dauerbeschuss die Sperre unbegrenzt offen und
        # sperrte den Berechtigten dauerhaft aus.
        cache.add(schluessel, 0, settings.LOGIN_SPERRE_SEKUNDEN)
        try:
            cache.incr(schluessel)
        except ValueError:
            # Zwischen `add` und `incr` abgelaufen. Dann faengt die Zaehlung
            # mit diesem Fehlversuch neu an.
            cache.add(schluessel, 1, settings.LOGIN_SPERRE_SEKUNDEN)
        return super().form_invalid(form)
