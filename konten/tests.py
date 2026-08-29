"""Zusagen am Konto. Der Rauchtest lag urspruenglich mit im Objekt-Teil -
die Besuchszeiten gehoeren aber zu `konten`, `make test` laeuft ohnehin ueber beide.
"""

import re
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .middleware import BesuchMiddleware
from .models import BESUCHSPAUSE, Person


class BesuchTests(TestCase):
    """"Neu seit deinem letzten Besuch" braucht eine Schwelle, die stillsteht,
    solange jemand arbeitet - sonst leert sich die Liste unter der Hand.

    `letzter_besuch` ist die letzte Aktivitaet, nicht der Beginn des Besuchs.
    Gegen den Beginn gemessen rotiert die Schwelle mitten in einer langen
    Sitzung, weil der Abstand irgendwann von allein die Pause ueberschreitet.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen")
        self.t0 = timezone.now()

    # --- erster Besuch ---------------------------------------------------

    def test_beim_ersten_besuch_ist_die_schwelle_leer(self):
        self.person.besuch_registrieren(self.t0)
        self.assertIsNone(self.person.neu_seit)

    def test_der_erste_aufruf_setzt_die_letzte_aktivitaet(self):
        self.person.besuch_registrieren(self.t0)
        self.assertEqual(self.person.letzter_besuch, self.t0)

    def test_der_erste_aufruf_meldet_keinen_neuen_besuch(self):
        self.assertFalse(self.person.besuch_registrieren(self.t0))

    def test_die_zeiten_landen_in_der_datenbank(self):
        self.person.besuch_registrieren(self.t0)
        self.person.refresh_from_db()
        self.assertEqual(self.person.letzter_besuch, self.t0)

    # --- laufende Sitzung ------------------------------------------------

    def test_jeder_aufruf_schreibt_die_letzte_aktivitaet_fort(self):
        self.person.besuch_registrieren(self.t0)
        spaeter = self.t0 + timedelta(minutes=5)
        self.person.besuch_registrieren(spaeter)
        self.assertEqual(self.person.letzter_besuch, spaeter)

    def test_weiterklicken_verschiebt_die_schwelle_nicht(self):
        self.person.besuch_registrieren(self.t0)
        self.person.besuch_registrieren(self.t0 + timedelta(minutes=5))
        self.assertIsNone(self.person.neu_seit)

    def test_weiterklicken_meldet_keinen_neuen_besuch(self):
        self.person.besuch_registrieren(self.t0)
        self.assertFalse(self.person.besuch_registrieren(self.t0 + timedelta(minutes=5)))

    def test_eine_stunde_am_stueck_bewegt_die_schwelle_nie(self):
        """Sieben Aufrufe im Abstand von zehn Minuten.

        Der Regressionstest fuer den eigentlichen Fehler: gegen den
        Besuchsbeginn gemessen rotierte die Schwelle hier zweimal
        (None -> t0 -> t0+30), obwohl niemand pausiert hatte. Zwei Aufrufe
        koennen das nicht zeigen - die Akkumulation braucht die volle Stunde.
        """
        for schritt in range(7):
            zeitpunkt = self.t0 + timedelta(minutes=10 * schritt)
            self.person.besuch_registrieren(zeitpunkt)
            self.assertIsNone(
                self.person.neu_seit,
                f"Schwelle nach {10 * schritt} Minuten gewandert",
            )

    def test_nach_einer_stunde_am_stueck_stimmt_die_letzte_aktivitaet(self):
        for schritt in range(7):
            self.person.besuch_registrieren(self.t0 + timedelta(minutes=10 * schritt))
        self.assertEqual(self.person.letzter_besuch, self.t0 + timedelta(minutes=60))

    # --- neuer Besuch ----------------------------------------------------

    def test_nach_der_pause_wird_die_letzte_aktivitaet_zur_schwelle(self):
        self.person.besuch_registrieren(self.t0)
        self.person.besuch_registrieren(self.t0 + BESUCHSPAUSE)
        self.assertEqual(self.person.neu_seit, self.t0)

    def test_die_schwelle_ist_die_letzte_aktivitaet_nicht_der_besuchsbeginn(self):
        """Der Test, der die beiden Bauarten unterscheidet.

        Aufrufe bei 0, 10, 20, dann Pause bis 60. Schwelle muss 20 sein - die
        letzte Aktivitaet. Gegen den Besuchsbeginn gemessen waere sie 0, und
        alles zwischen 0 und 20 erschiene ein zweites Mal als "neu".
        """
        for minute in (0, 10, 20):
            self.person.besuch_registrieren(self.t0 + timedelta(minutes=minute))
        self.person.besuch_registrieren(self.t0 + timedelta(minutes=60))
        self.assertEqual(self.person.neu_seit, self.t0 + timedelta(minutes=20))

    def test_nach_der_pause_laeuft_die_aktivitaet_weiter(self):
        self.person.besuch_registrieren(self.t0)
        spaeter = self.t0 + BESUCHSPAUSE
        self.person.besuch_registrieren(spaeter)
        self.assertEqual(self.person.letzter_besuch, spaeter)

    def test_nach_der_pause_wird_ein_neuer_besuch_gemeldet(self):
        self.person.besuch_registrieren(self.t0)
        self.assertTrue(self.person.besuch_registrieren(self.t0 + BESUCHSPAUSE))

    def test_knapp_vor_der_pause_wird_noch_nicht_rotiert(self):
        self.person.besuch_registrieren(self.t0)
        self.person.besuch_registrieren(
            self.t0 + BESUCHSPAUSE - timedelta(seconds=1)
        )
        self.assertIsNone(self.person.neu_seit)

    def test_ein_zweiter_besuch_ueberschreibt_die_alte_schwelle(self):
        self.person.besuch_registrieren(self.t0)
        zweiter = self.t0 + BESUCHSPAUSE
        self.person.besuch_registrieren(zweiter)
        self.person.besuch_registrieren(zweiter + BESUCHSPAUSE)
        self.assertEqual(self.person.neu_seit, zweiter)


class AnzeigenameTests(TestCase):
    """`{{ person }}` und `{{ person.anzeigename }}` muessen dasselbe liefern -
    sonst ist ein nacktes `{{ person }}` im Template eine stille Abweichung.
    """

    def test_ohne_namen_steht_der_benutzername(self):
        p = Person.objects.create_user("steffen")
        self.assertEqual(p.anzeigename, "steffen")

    def test_mit_namen_steht_der_volle_name(self):
        p = Person.objects.create_user("steffen", first_name="Steffen", last_name="P.")
        self.assertEqual(p.anzeigename, "Steffen P.")

    def test_str_und_anzeigename_sind_dasselbe(self):
        p = Person.objects.create_user("steffen", first_name="Steffen", last_name="P.")
        self.assertEqual(str(p), p.anzeigename)


class OeffentlicheAnsichtenTests(TestCase):
    """Was ist ohne Anmeldung erreichbar?

    Der Riegel gegen einen Registrierungsweg ist nicht "wir haben keinen
    gebaut", sondern diese Aufzaehlung: `LoginRequiredMiddleware` laesst genau
    die Ansichten durch, die `login_not_required` tragen. Wer eine neue offene
    Ansicht anlegt - eine Registrierung, ein Passwort-Zuruecksetzen, eine
    oeffentliche Liste - macht diesen Test rot und muss die Zeile bewusst
    hinzufuegen.
    """

    ERWARTET = {"anmelden/", "admin/login/"}

    def _offene_pfade(self, muster, praefix=""):
        for eintrag in muster:
            pfad = praefix + str(eintrag.pattern)
            unterliste = getattr(eintrag, "url_patterns", None)
            if unterliste is not None:
                yield from self._offene_pfade(unterliste, pfad)
            elif not getattr(eintrag.callback, "login_required", True):
                yield pfad

    def test_nur_die_anmeldung_ist_ohne_konto_erreichbar(self):
        from config.urls import urlpatterns

        self.assertEqual(set(self._offene_pfade(urlpatterns)), self.ERWARTET)


class ZugangTests(TestCase):
    """Ohne Anmeldung ist nichts zu sehen, mit Anmeldung alles."""

    def setUp(self):
        # Der Zaehler des Rate-Limits liegt im Cache und ueberlebt die
        # Testmethode. Mehrere Fehlversuchs-Tests in dieser Klasse summierten
        # sich sonst auf und machten den naechsten Test aus dem falschen Grund
        # rot - abhaengig von der Reihenfolge.
        cache.clear()
        self.person = Person.objects.create_user(
            "steffen", password="ein-langes-passwort", first_name="Steffen", last_name="P."
        )

    # --- ohne Anmeldung --------------------------------------------------

    def test_die_liste_verlangt_eine_anmeldung(self):
        antwort = self.client.get("/")
        self.assertEqual(antwort.status_code, 302)

    def test_die_umleitung_fuehrt_auf_die_anmeldeseite(self):
        antwort = self.client.get("/")
        self.assertEqual(antwort["Location"], "/anmelden/?next=/")

    def test_der_admin_verlangt_ebenfalls_eine_anmeldung(self):
        antwort = self.client.get("/admin/")
        self.assertEqual(antwort.status_code, 302)

    def test_die_anmeldeseite_selbst_ist_offen(self):
        self.assertEqual(self.client.get("/anmelden/").status_code, 200)

    # --- anmelden --------------------------------------------------------

    def test_richtige_zugangsdaten_melden_an(self):
        self.client.post(
            "/anmelden/", {"username": "steffen", "password": "ein-langes-passwort"}
        )
        self.assertIn("_auth_user_id", self.client.session)

    def test_nach_dem_anmelden_fuehrt_der_weg_auf_die_liste(self):
        antwort = self.client.post(
            "/anmelden/", {"username": "steffen", "password": "ein-langes-passwort"}
        )
        self.assertEqual(antwort["Location"], "/")

    def test_die_gemerkte_zieladresse_wird_angesteuert(self):
        antwort = self.client.post(
            "/anmelden/?next=/admin/",
            {"username": "steffen", "password": "ein-langes-passwort"},
        )
        self.assertEqual(antwort["Location"], "/admin/")

    def test_falsche_zugangsdaten_melden_nicht_an(self):
        self.client.post("/anmelden/", {"username": "steffen", "password": "falsch"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_falsche_zugangsdaten_bleiben_auf_der_anmeldeseite(self):
        antwort = self.client.post(
            "/anmelden/", {"username": "steffen", "password": "falsch"}
        )
        self.assertEqual(antwort.status_code, 200)

    def _ohne_wechselnde_werte(self, antwort):
        """Seiteninhalt ohne CSRF-Token und ohne den zurueckgespielten Eingabewert.

        Beide wechseln je Aufruf und haben mit der Frage nichts zu tun. Ein
        Vergleich der rohen Seiten misst sie mit und ist immer rot.
        """
        return re.sub(r'value="[^"]*"', 'value=""', antwort.content.decode())

    def test_die_fehlermeldung_verraet_nicht_ob_es_das_konto_gibt(self):
        # Sonst laesst sich ueber die Anmeldeseite herausfinden, wer dabei ist.
        mit_konto = self.client.post(
            "/anmelden/", {"username": "steffen", "password": "falsch"}
        )
        ohne_konto = self.client.post(
            "/anmelden/", {"username": "gibtesnicht", "password": "falsch"}
        )
        self.assertEqual(
            self._ohne_wechselnde_werte(mit_konto), self._ohne_wechselnde_werte(ohne_konto)
        )

    # --- angemeldet ------------------------------------------------------

    def test_mit_anmeldung_ist_die_liste_zu_sehen(self):
        self.client.force_login(self.person)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_die_seite_nennt_die_angemeldete_person(self):
        self.client.force_login(self.person)
        self.assertContains(self.client.get("/"), "Steffen P.")

    def test_die_seite_bietet_das_abmelden_an(self):
        self.client.force_login(self.person)
        self.assertContains(self.client.get("/"), 'action="/abmelden/"')

    def test_wer_angemeldet_ist_wird_von_der_anmeldeseite_weggeleitet(self):
        self.client.force_login(self.person)
        self.assertEqual(self.client.get("/anmelden/")["Location"], "/")

    # --- abmelden --------------------------------------------------------

    def test_abmelden_beendet_die_sitzung(self):
        self.client.force_login(self.person)
        self.client.post("/abmelden/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_nach_dem_abmelden_fuehrt_der_weg_auf_die_anmeldeseite(self):
        self.client.force_login(self.person)
        self.assertEqual(self.client.post("/abmelden/")["Location"], "/anmelden/")

    def test_abmelden_per_get_beendet_die_sitzung_nicht(self):
        # Ein GET liesse sich von einer fremden Seite aus ausloesen.
        self.client.force_login(self.person)
        self.client.get("/abmelden/")
        self.assertIn("_auth_user_id", self.client.session)

    # --- Sitzungsdauer ---------------------------------------------------

    def test_das_sitzungscookie_haelt_ein_jahr(self):
        self.client.force_login(self.person)
        antwort = self.client.get("/")
        self.assertEqual(antwort.cookies["sessionid"]["max-age"], 60 * 60 * 24 * 365)

    def test_jeder_aufruf_erneuert_das_sitzungscookie(self):
        # `SESSION_SAVE_EVERY_REQUEST`: sonst laeuft das Jahr ab Anmeldung und
        # nicht ab dem letzten Aufruf, und irgendwann steht jeder wieder davor.
        self.client.force_login(self.person)
        self.client.get("/")
        zweite = self.client.get("/")
        self.assertIn("sessionid", zweite.cookies)


# Die Frist ist ABSICHTLICH eine andere als die aus den Einstellungen (900).
# Bei gleicher Zahl rendert eine fest eingetragene "15 Minuten" im Template
# dieselbe Zeichenkette wie der Wert aus den Einstellungen, und der Zeuge
# dafuer ist blind. Am 28.08.2026 an genau dieser Stelle gemessen.
@override_settings(LOGIN_VERSUCHE=3, LOGIN_SPERRE_SEKUNDEN=600)
class AnmeldeRateLimitTests(TestCase):
    """Fuenf Konten, ein offenes Anmeldeformular - ohne Limit ist Durchprobieren
    die naheliegende Angriffsform.
    """

    def setUp(self):
        cache.clear()
        self.person = Person.objects.create_user("steffen", password="ein-langes-passwort")

    def _fehlversuch(self, benutzer="steffen"):
        return self.client.post("/anmelden/", {"username": benutzer, "password": "falsch"})

    def _richtig(self, benutzer="steffen"):
        return self.client.post(
            "/anmelden/", {"username": benutzer, "password": "ein-langes-passwort"}
        )

    def test_unterhalb_der_grenze_bleibt_die_anmeldung_offen(self):
        for _ in range(2):
            self._fehlversuch()
        self.assertEqual(self._fehlversuch().status_code, 200)

    def test_unterhalb_der_grenze_kommt_das_richtige_passwort_durch(self):
        for _ in range(2):
            self._fehlversuch()
        self._richtig()
        self.assertIn("_auth_user_id", self.client.session)

    def test_ueber_der_grenze_wird_abgewiesen(self):
        for _ in range(3):
            self._fehlversuch()
        self.assertEqual(self._fehlversuch().status_code, 429)

    def test_die_sperre_gilt_auch_fuer_das_richtige_passwort(self):
        # Der eigentliche Punkt. Ein Limit, das nur falsche Eingaben abweist,
        # haelt genau den Versuch nicht auf, auf den es ankommt.
        for _ in range(3):
            self._fehlversuch()
        self._richtig()
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_die_gesperrte_seite_nennt_die_dauer_aus_den_einstellungen(self):
        for _ in range(3):
            self._fehlversuch()
        self.assertContains(self._fehlversuch(), "10 Minuten", status_code=429)

    def test_die_gesperrte_seite_zeigt_kein_formular_mehr(self):
        for _ in range(3):
            self._fehlversuch()
        self.assertNotContains(self._fehlversuch(), 'name="password"', status_code=429)

    def test_eine_erfolgreiche_anmeldung_setzt_den_zaehler_zurueck(self):
        for _ in range(2):
            self._fehlversuch()
        self._richtig()
        self.client.post("/abmelden/")
        for _ in range(2):
            self._fehlversuch()
        self.assertEqual(self._fehlversuch().status_code, 200)

    def test_dauerbeschuss_verlaengert_die_sperre_nicht(self):
        """Die Frist laeuft ab dem ERSTEN Fehlversuch, nicht ab dem letzten.

        Setzte jeder weitere Fehlversuch sie neu, hielte ein Angreifer die
        Sperre unbegrenzt offen: aus dem Schutz gegen Durchprobieren wuerde ein
        Ausschaltknopf gegen die Berechtigten.

        `time.time` wird gestellt, weil beide Cache-Module - `base` beim Setzen
        der Frist, `locmem` beim Pruefen - darueber gehen. Ein echtes Warten
        waere eine Sekunde Laufzeit je Lauf und trotzdem ungenauer.

        Der Zwischenversuch muss UNTERHALB der Grenze liegen. Ein Versuch
        oberhalb wird schon in `post()` abgewiesen und erreicht das Setzen der
        Frist nie - dann liefen beide Bauarten gleich und der Test waere blind.
        """
        with mock.patch("time.time", return_value=1000.0):
            self._fehlversuch()
            self._fehlversuch()
        with mock.patch("time.time", return_value=1500.0):
            self._fehlversuch()
        with mock.patch("time.time", return_value=1700.0):
            self._richtig()
        self.assertIn("_auth_user_id", self.client.session)

    def test_ein_anderer_benutzername_wird_nicht_mitgesperrt(self):
        Person.objects.create_user("anna", password="ein-langes-passwort")
        for _ in range(4):
            self._fehlversuch("steffen")
        self._richtig("anna")
        self.assertIn("_auth_user_id", self.client.session)

    def test_der_schluessel_unterscheidet_gross_und_kleinschreibung_nicht(self):
        # Sonst hat derselbe Angreifer mit "Steffen" wieder fuenf Versuche frei.
        for _ in range(3):
            self._fehlversuch("STEFFEN")
        self.assertEqual(self._fehlversuch("steffen").status_code, 429)

    def test_ein_x_forwarded_for_hebt_die_sperre_nicht_auf(self):
        # Der Kopf ist frei waehlbar. Wuerde er in den Schluessel eingehen,
        # haette ein Angreifer je erfundener Adresse ein volles Kontingent.
        for _ in range(4):
            self._fehlversuch()
        antwort = self.client.post(
            "/anmelden/",
            {"username": "steffen", "password": "falsch"},
            HTTP_X_FORWARDED_FOR="203.0.113.7",
        )
        self.assertEqual(antwort.status_code, 429)


class BesuchMiddlewareTests(TestCase):
    """Der Aufrufer von `besuch_registrieren()`. Die Zeitlogik selbst steht in
    `BesuchTests` - hier geht es nur darum, dass und wann sie gerufen wird.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="ein-langes-passwort")

    def test_ein_aufruf_schreibt_die_aktivitaet_fort(self):
        self.client.force_login(self.person)
        self.client.get("/")
        self.person.refresh_from_db()
        self.assertIsNotNone(self.person.letzter_besuch)

    def test_ohne_anmeldung_wird_nichts_geschrieben(self):
        self.client.get("/")
        self.person.refresh_from_db()
        self.assertIsNone(self.person.letzter_besuch)

    def test_die_aktivitaet_steht_schon_waehrend_der_ansicht(self):
        """Die Middleware laeuft VOR der Ansicht, nicht danach.

        Andersherum saehe die erste Ansicht eines neuen Besuchs noch die
        Schwelle des vorletzten Besuchs, und alles aus dem letzten Besuch
        erschiene ein zweites Mal als neu.
        """
        gesehen = {}

        def ansicht(request):
            gesehen["wert"] = Person.objects.get(pk=self.person.pk).letzter_besuch
            return HttpResponse()

        anfrage = RequestFactory().get("/")
        anfrage.user = self.person
        BesuchMiddleware(ansicht)(anfrage)
        self.assertIsNotNone(gesehen["wert"])

    def test_ohne_request_user_faellt_die_middleware_nicht_um(self):
        # Ohne AuthenticationMiddleware soll sich die Systempruefung melden,
        # nicht hier ein AttributeError den Aufruf zerlegen.
        antwort = BesuchMiddleware(lambda r: HttpResponse("ok"))(RequestFactory().get("/"))
        self.assertEqual(antwort.status_code, 200)
