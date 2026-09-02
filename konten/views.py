"""Anmeldung.

Es gibt bewusst keinen Registrierungsweg. Konten legt `make superuser` an.
"""

import time

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache


# Die Adressen, unter denen der eigene Proxy auf demselben Rechner sitzt. Nur
# von dort darf `X-Forwarded-For` ueberhaupt gelesen werden.
LOKALE_ADRESSEN = frozenset({"127.0.0.1", "::1"})


def absenderadresse(request):
    """Die Adresse, unter der das Rate-Limit zaehlt.

    Ohne Proxy gilt `REMOTE_ADDR`, und `X-Forwarded-For` wird NICHT gelesen:
    der Kopf ist dann frei waehlbar, und ein frei waehlbarer Schluessel hebt
    das Limit auf - je erfundener Adresse ein volles Kontingent.

    Hinter Caddy kehrt sich das um. Dort ist `REMOTE_ADDR` fuer JEDE Anfrage
    127.0.0.1; der Zaehler liefe global statt je Absender, und fuenf
    Fehlversuche von irgendwem sperrten alle fuenf Personen fuer eine
    Viertelstunde. Das ist kein theoretischer Fall.

    Genommen wird der LETZTE Eintrag der Kette, nicht der erste. Caddy haengt
    die tatsaechliche Absender-Adresse RECHTS an das an, was der Aufrufer
    geschickt hat: wer `X-Forwarded-For: 9.9.9.9` sendet, erzeugt
    `9.9.9.9, <echte IP>`. Der linke Teil ist frei erfunden, der rechte stammt
    vom Proxy. Wer den ersten Eintrag naehme, baute genau die Luecke wieder
    ein, gegen die der Kopf sonst gesperrt ist.

    Die Pruefung auf die lokale Adresse ist der zweite Riegel: eine Anfrage,
    die NICHT vom Proxy nebenan kommt, darf den Kopf auch bei eingeschaltetem
    `VERTRAUE_PROXY` nicht setzen duerfen. Sonst genuegte es, Gunicorn direkt
    zu erreichen.

    Bekannte Grenze, bewusst nicht geloest: Wird spaeter der Cloudflare-Proxy
    eingeschaltet, ist die Kette zwei Spruenge lang und der rechte Eintrag ist
    Cloudflares Adresse - dann teilen sich wieder alle denselben Zaehler. Wer
    das aendert, muss die Zahl der vertrauten Spruenge festlegen; den letzten
    Eintrag einfach weiter links zu suchen, waere dieselbe Luecke von vorhin.
    """
    entfernt = request.META.get("REMOTE_ADDR", "")
    if not settings.VERTRAUE_PROXY:
        return entfernt
    if entfernt not in LOKALE_ADRESSEN:
        return entfernt
    # `rsplit` mit 1: nur der rechte Eintrag wird gebraucht, der Rest ist
    # ohnehin nicht vertrauenswuerdig.
    letzter = request.META.get("HTTP_X_FORWARDED_FOR", "").rsplit(",", 1)[-1].strip()
    # Fehlender oder leerer Kopf faellt auf `REMOTE_ADDR` zurueck. Ein leerer
    # Schluesselteil waere fuer alle derselbe - das waere der globale Zaehler,
    # nur unbemerkt.
    return letzter or entfernt


def sperrschluessel(request):
    """Zaehlerschluessel je Absender und Benutzername."""
    benutzer = (request.POST.get("username") or "").strip().lower()
    return f"anmeldeversuche:{absenderadresse(request)}:{benutzer}"


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
        if self._versuche(request) >= settings.LOGIN_VERSUCHE:
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

    @staticmethod
    def _versuche(request):
        stand = cache.get(sperrschluessel(request))
        return stand[0] if stand else 0

    def form_invalid(self, form):
        schluessel = sperrschluessel(self.request)
        jetzt = time.time()
        stand = cache.get(schluessel)
        # Das Ende der Frist steht IM Wert, nicht nur in der Ablaufzeit des
        # Cache-Eintrags. Der frueher hier stehende Weg - `cache.add(..., FRIST)`
        # und danach `cache.incr(...)` - haengt still an `LocMemCache`: dessen
        # `incr` aendert den Wert an Ort und Stelle und laesst die Frist in
        # Ruhe. Der Datenbank-Cache hat kein eigenes `incr`; `BaseCache.incr`
        # schreibt ueber `set()` zurueck, OHNE Frist - also mit der Vorgabe von
        # 300 Sekunden, gerechnet ab jetzt. Damit waere die Sperre erstens
        # kuerzer als `LOGIN_SPERRE_SEKUNDEN` und zweitens bei jedem weiteren
        # Fehlversuch neu gestellt. Genau das darf sie nicht: die Frist laeuft
        # ab dem ERSTEN Fehlversuch, sonst hielte ein Dauerbeschuss die Sperre
        # unbegrenzt offen und sperrte den Berechtigten dauerhaft aus.
        if stand is None:
            zaehler, frist_ende = 0, jetzt + settings.LOGIN_SPERRE_SEKUNDEN
        else:
            zaehler, frist_ende = stand
        # Mindestens eine Sekunde: `set()` mit 0 oder weniger loeschte den
        # Eintrag sofort, und der Fehlversuch waere nicht gezaehlt.
        cache.set(schluessel, (zaehler + 1, frist_ende), max(frist_ende - jetzt, 1))
        return super().form_invalid(form)
