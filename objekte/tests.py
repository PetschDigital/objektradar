"""Zusagen des Datenmodells. Laeuft gegen Postgres, nicht gegen SQLite.

Der Unterschied ist nicht kosmetisch: SQLite liefert bei einer Division durch
null still NULL, Postgres wirft `division_by_zero`. Der Riegel gegen
Wohnflaeche 0 und der partielle Unique-Constraint sind nur hier bezeugt.

Je Zusage eine eigene Testmethode. Zwei Assertions in einer Methode messen die
zweite nicht mehr, sobald die erste faellt.
"""

import ast
import html as htmlwerkzeug
import math
import inspect
import re
import textwrap
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from importlib import import_module
from urllib.parse import parse_qs, unquote, urlparse
from unittest import mock
from decimal import Decimal

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import FieldError
from django.db import IntegrityError, connection, migrations, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.forms import modelform_factory
from django.templatetags.static import static
from django.urls import reverse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.html import escape

from konten.models import BESUCHSPAUSE

from . import forms, lesezeichen, portale, views
from .choices import (
    STATUS_AUSGEBLENDET,
    Land,
    Objekttyp,
    Portal,
    PreisQuelle,
    Quelle,
    Status,
    Wertung,
    Zustand,
)
from .forms import STATUS_VORBELEGUNG, ObjektForm
from .models import Bild, Notiz, Objekt, Preisverlauf, Statusaenderung, Votum
from .portale import portal_und_id

#: Der Modulname faengt mit einer Ziffer an - ein `import` schreibt sich dafuer
#: nicht hin. Der Zugriff ist noetig, weil die Zeugen unten die Funktion der
#: Migration aufrufen und getrennt davon pruefen, dass die Migration genau
#: diese Funktion auch ausfuehrt.
nachtragsmigration = import_module("objekte.migrations.0003_portal_und_inserats_id_nachtragen")

#: Der zweite Lauf desselben Nachtrags, jetzt mit den drei Portalen vom 02.09.
zweiter_nachtrag = import_module("objekte.migrations.0005_bestand_neue_portale_nachtragen")

#: Der Erfassungszeitpunkt am Preisverlauf, nachgezogen am 04.09.
erfassungsmigration = import_module("objekte.migrations.0006_preisverlauf_erfasst_am")

Person = get_user_model()


class ErfassungTests(TestCase):
    """Ein Objekt zu erfassen muss Sekunden dauern - also darf fast nichts Pflicht sein."""

    def test_objekt_nur_mit_url_ist_speicherbar(self):
        o = Objekt.objects.create(url="https://www.idealista.com/inmueble/12345/")
        self.assertIsNotNone(o.pk)

    def test_status_steht_ohne_zutun_auf_neu(self):
        o = Objekt.objects.create(url="https://x/1")
        self.assertEqual(o.status, Status.NEU)

    def test_zustand_steht_ohne_zutun_auf_unklar(self):
        o = Objekt.objects.create(url="https://x/1")
        self.assertEqual(o.zustand, Zustand.UNKLAR)

    def test_ohne_preis_entsteht_kein_verlaufseintrag(self):
        o = Objekt.objects.create(url="https://x/1")
        self.assertEqual(o.preise.count(), 0)


class PreisverlaufTests(TestCase):
    """Der Preisverlauf fuehrt, `aktueller_preis` wird redundant mitgeschrieben."""

    def test_anlegen_mit_preis_erzeugt_den_ersten_eintrag(self):
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("250000"))
        self.assertEqual(o.preise.count(), 1)

    def test_der_erste_eintrag_traegt_den_angelegten_preis(self):
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("250000"))
        self.assertEqual(o.preise.first().preis, Decimal("250000.00"))

    def test_ein_preis_direkt_am_feld_erzeugt_keinen_eintrag_mehr(self):
        # Der frueher unterstuetzte Weg. Er ist bewusst wirkungslos, weil eine
        # veraltete Instanz sonst ihren alten Preis als neuen Eintrag anhaengt.
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("250000"))
        o.aktueller_preis = Decimal("225000")
        o.save()
        self.assertEqual(o.preise.count(), 1)

    def test_ein_preis_direkt_am_feld_erreicht_die_datenbank_nicht(self):
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("250000"))
        o.aktueller_preis = Decimal("225000")
        o.save()
        o.refresh_from_db()
        self.assertEqual(o.aktueller_preis, Decimal("250000.00"))

    def test_speichern_ohne_preisaenderung_erzeugt_nichts(self):
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("250000"))
        o.ort = "Malaga"
        o.save()
        self.assertEqual(o.preise.count(), 1)

    def test_eintrag_im_verlauf_zieht_den_objektpreis_nach(self):
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("250000"))
        Preisverlauf.objects.create(
            objekt=o, preis=Decimal("199000"), quelle=PreisQuelle.SUCHAGENTEN_MAIL
        )
        o.refresh_from_db()
        self.assertEqual(o.aktueller_preis, Decimal("199000.00"))

    def test_der_rueckschreibende_eintrag_verdoppelt_sich_nicht(self):
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("250000"))
        Preisverlauf.objects.create(objekt=o, preis=Decimal("199000"))
        self.assertEqual(o.preise.count(), 2)

    def test_ein_alter_nachtrag_ueberschreibt_den_aktuellen_preis_nicht(self):
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("250000"))
        Preisverlauf.objects.create(
            objekt=o, preis=Decimal("300000"), datum=date(2020, 1, 1)
        )
        o.refresh_from_db()
        self.assertEqual(o.aktueller_preis, Decimal("250000.00"))

    def test_bei_gleichem_datum_fuehrt_der_zuletzt_angelegte_eintrag(self):
        # Ohne das zweite Sortierkriterium waere "der juengste" hier Zufall.
        o = Objekt.objects.create(url="https://x/1")
        heute = date(2026, 8, 28)
        Preisverlauf.objects.create(objekt=o, preis=Decimal("300000"), datum=heute)
        Preisverlauf.objects.create(objekt=o, preis=Decimal("280000"), datum=heute)
        self.assertEqual(o.preise.first().preis, Decimal("280000.00"))


class PreisSetzenTests(TestCase):
    """`preis_setzen()` ist der einzige Weg, auf dem sich der Preis aendert."""

    def setUp(self):
        self.person = Person.objects.create_user("steffen")
        self.objekt = Objekt.objects.create(
            url="https://x/1", aktueller_preis=Decimal("250000")
        )

    def test_erzeugt_genau_einen_eintrag(self):
        self.objekt.preis_setzen(self.person, Decimal("225000"))
        self.assertEqual(self.objekt.preise.count(), 2)

    def test_der_neue_eintrag_traegt_den_neuen_preis(self):
        self.objekt.preis_setzen(self.person, Decimal("225000"))
        self.assertEqual(self.objekt.preise.first().preis, Decimal("225000.00"))

    def test_schreibt_den_preis_in_die_datenbank_zurueck(self):
        self.objekt.preis_setzen(self.person, Decimal("225000"))
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.aktueller_preis, Decimal("225000.00"))

    def test_die_instanz_traegt_den_neuen_preis_ohne_refresh(self):
        self.objekt.preis_setzen(self.person, Decimal("225000"))
        self.assertEqual(self.objekt.aktueller_preis, Decimal("225000.00"))

    def test_setzt_zuletzt_geaendert_von(self):
        self.objekt.preis_setzen(self.person, Decimal("225000"))
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.zuletzt_geaendert_von_id, self.person.pk)

    def test_uebernimmt_die_angegebene_quelle(self):
        self.objekt.preis_setzen(
            self.person, Decimal("225000"), PreisQuelle.SUCHAGENTEN_MAIL
        )
        self.assertEqual(self.objekt.preise.first().quelle, PreisQuelle.SUCHAGENTEN_MAIL)

    def test_gibt_den_erzeugten_eintrag_zurueck(self):
        eintrag = self.objekt.preis_setzen(self.person, Decimal("225000"))
        self.assertEqual(eintrag.pk, self.objekt.preise.first().pk)

    def test_ruehrt_den_status_nicht_an(self):
        self.objekt.preis_setzen(self.person, Decimal("225000"))
        self.assertEqual(self.objekt.statusaenderungen.count(), 0)


class VeralteteInstanzTests(TestCase):
    """Ab Schritt 3 der Normalfall: der Mail-Parser senkt den Preis, waehrend
    eine Ansicht noch eine aeltere Kopie desselben Objekts in der Hand haelt.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen")
        self.objekt = Objekt.objects.create(
            url="https://x/1", aktueller_preis=Decimal("250000")
        )
        # Zweite Kopie, bevor gesenkt wird - sie traegt weiter 250000.
        self.veraltet = Objekt.objects.get(pk=self.objekt.pk)
        self.objekt.preis_setzen(self.person, Decimal("225000"))

    def test_speichern_dreht_den_preis_nicht_zurueck(self):
        self.veraltet.ort = "Malaga"
        self.veraltet.save()
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.aktueller_preis, Decimal("225000.00"))

    def test_speichern_erzeugt_keinen_verlaufseintrag(self):
        self.veraltet.ort = "Malaga"
        self.veraltet.save()
        self.assertEqual(self.objekt.preise.count(), 2)

    def test_die_eigentliche_aenderung_kommt_trotzdem_an(self):
        self.veraltet.ort = "Malaga"
        self.veraltet.save()
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.ort, "Malaga")

    def test_die_veraltete_instanz_traegt_danach_den_massgeblichen_preis(self):
        self.veraltet.save()
        self.assertEqual(self.veraltet.aktueller_preis, Decimal("225000.00"))

    def test_auch_ein_statuswechsel_dreht_den_preis_nicht_zurueck(self):
        self.veraltet.status_setzen(self.person, Status.IN_PRUEFUNG)
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.aktueller_preis, Decimal("225000.00"))


class QuadratmeterpreisTests(TestCase):
    """€/m² ist kein Feld, sondern die Annotation `qm_preis`."""

    def test_annotation_rechnet_preis_durch_wohnflaeche(self):
        o = Objekt.objects.create(
            url="https://x/1", aktueller_preis=Decimal("199000"), wohnflaeche=Decimal("100")
        )
        ann = Objekt.objects.mit_qm_preis().get(pk=o.pk)
        self.assertEqual(ann.qm_preis, Decimal("1990.00"))

    def test_ohne_wohnflaeche_ist_der_wert_leer(self):
        o = Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("199000"))
        ann = Objekt.objects.mit_qm_preis().get(pk=o.pk)
        self.assertIsNone(ann.qm_preis)

    def test_ohne_preis_ist_der_wert_leer(self):
        o = Objekt.objects.create(url="https://x/1", wohnflaeche=Decimal("100"))
        ann = Objekt.objects.mit_qm_preis().get(pk=o.pk)
        self.assertIsNone(ann.qm_preis)

    def test_wohnflaeche_null_ergibt_leer_statt_datenbankfehler(self):
        # Der eigentliche Grund fuer Postgres statt SQLite: SQLite liefert bei
        # x/0 still NULL und laesst diesen Riegel unbezeugt.
        o = Objekt.objects.create(
            url="https://x/1", aktueller_preis=Decimal("199000"), wohnflaeche=Decimal("0")
        )
        ann = Objekt.objects.mit_qm_preis().get(pk=o.pk)
        self.assertIsNone(ann.qm_preis)

    def test_es_gibt_keine_gleichnamige_property_am_modell(self):
        # Eine Property ohne Setter blockiert das Schreiben der Annotation.
        self.assertFalse(hasattr(Objekt, "qm_preis"))


class DublettenTests(TestCase):
    """Eindeutig ueber Portal + Inserats-ID, und nur wenn beide gesetzt sind."""

    def test_gleiches_portal_und_gleiche_id_wird_abgelehnt(self):
        Objekt.objects.create(url="https://x/1", portal=Portal.IDEALISTA, inserats_id="123")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Objekt.objects.create(
                    url="https://x/2", portal=Portal.IDEALISTA, inserats_id="123"
                )

    def test_gleiche_id_bei_anderem_portal_ist_erlaubt(self):
        Objekt.objects.create(url="https://x/1", portal=Portal.IDEALISTA, inserats_id="123")
        Objekt.objects.create(url="https://x/2", portal=Portal.IMMOSCOUT24, inserats_id="123")
        self.assertEqual(Objekt.objects.count(), 2)

    def test_ohne_inserats_id_greift_die_regel_nicht(self):
        Objekt.objects.create(url="https://x/1", portal=Portal.IDEALISTA)
        Objekt.objects.create(url="https://x/2", portal=Portal.IDEALISTA)
        self.assertEqual(Objekt.objects.count(), 2)

    def test_ohne_portal_greift_die_regel_nicht(self):
        Objekt.objects.create(url="https://x/1", inserats_id="123")
        Objekt.objects.create(url="https://x/2", inserats_id="123")
        self.assertEqual(Objekt.objects.count(), 2)

    def test_dieselbe_url_zweimal_ist_erlaubt(self):
        # Die Roh-URL taugt nicht als Schluessel und wird deshalb nicht geprueft.
        Objekt.objects.create(url="https://x/1")
        Objekt.objects.create(url="https://x/1")
        self.assertEqual(Objekt.objects.count(), 2)


class StatusTests(TestCase):
    """Der Status wird immer manuell gesetzt und die Aenderung protokolliert."""

    def setUp(self):
        self.anna = Person.objects.create_user("anna")
        self.objekt = Objekt.objects.create(url="https://x/1")

    def test_statuswechsel_wird_protokolliert(self):
        self.objekt.status_setzen(self.anna, Status.HEISSE_SPUR)
        self.assertEqual(self.objekt.statusaenderungen.count(), 1)

    def test_das_protokoll_haelt_person_und_beide_zustaende_fest(self):
        self.objekt.status_setzen(self.anna, Status.HEISSE_SPUR)
        s = self.objekt.statusaenderungen.first()
        self.assertEqual(
            (s.person_id, s.alter_status, s.neuer_status),
            (self.anna.pk, Status.NEU, Status.HEISSE_SPUR),
        )

    def test_der_status_am_objekt_steht_danach_neu(self):
        self.objekt.status_setzen(self.anna, Status.HEISSE_SPUR)
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.status, Status.HEISSE_SPUR)

    def test_derselbe_status_erzeugt_keinen_eintrag(self):
        self.objekt.status_setzen(self.anna, Status.NEU)
        self.assertEqual(self.objekt.statusaenderungen.count(), 0)

    def test_statuswechsel_ruehrt_den_preisverlauf_nicht_an(self):
        o = Objekt.objects.create(url="https://x/2", aktueller_preis=Decimal("250000"))
        o.status_setzen(self.anna, Status.HEISSE_SPUR)
        self.assertEqual(o.preise.count(), 1)


class SichtbarkeitTests(TestCase):
    """Verworfene Objekte werden ausgeblendet, nicht geloescht."""

    def setUp(self):
        self.anna = Person.objects.create_user("anna")

    def test_raus_verschwindet_aus_der_liste(self):
        o = Objekt.objects.create(url="https://x/1")
        o.status_setzen(self.anna, Status.RAUS)
        self.assertFalse(Objekt.objects.sichtbar().filter(pk=o.pk).exists())

    def test_raus_bleibt_in_der_datenbank(self):
        o = Objekt.objects.create(url="https://x/1")
        o.status_setzen(self.anna, Status.RAUS)
        self.assertTrue(Objekt.objects.filter(pk=o.pk).exists())

    def test_vom_markt_verschwindet_ebenfalls(self):
        o = Objekt.objects.create(url="https://x/1")
        o.status_setzen(self.anna, Status.VOM_MARKT)
        self.assertFalse(Objekt.objects.sichtbar().filter(pk=o.pk).exists())

    def test_in_pruefung_bleibt_sichtbar(self):
        o = Objekt.objects.create(url="https://x/1")
        o.status_setzen(self.anna, Status.IN_PRUEFUNG)
        self.assertTrue(Objekt.objects.sichtbar().filter(pk=o.pk).exists())


class VotumTests(TestCase):
    """Ein Votum je Person und Objekt, jederzeit aenderbar."""

    def setUp(self):
        self.steffen = Person.objects.create_user("steffen")
        self.anna = Person.objects.create_user("anna")
        self.objekt = Objekt.objects.create(url="https://x/1")

    def test_zweites_votum_derselben_person_wird_abgelehnt(self):
        Votum.objects.create(objekt=self.objekt, person=self.steffen, wertung=Wertung.DAFUER)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Votum.objects.create(
                    objekt=self.objekt, person=self.steffen, wertung=Wertung.RAUS
                )

    def test_eine_zweite_person_darf_abstimmen(self):
        Votum.objects.create(objekt=self.objekt, person=self.steffen, wertung=Wertung.DAFUER)
        Votum.objects.create(objekt=self.objekt, person=self.anna, wertung=Wertung.RAUS)
        self.assertEqual(self.objekt.vota.count(), 2)

    def test_ein_bestehendes_votum_ist_aenderbar(self):
        v = Votum.objects.create(objekt=self.objekt, person=self.steffen, wertung=Wertung.DAFUER)
        v.wertung = Wertung.RAUS
        v.save()
        v.refresh_from_db()
        self.assertEqual(v.wertung, Wertung.RAUS)


class LoeschschutzTests(TestCase):
    """Personen werden nicht geloescht - sonst faellt die Nachvollziehbarkeit."""

    def test_person_mit_votum_laesst_sich_nicht_loeschen(self):
        from django.db.models import ProtectedError

        p = Person.objects.create_user("steffen")
        Votum.objects.create(
            objekt=Objekt.objects.create(url="https://x/1"),
            person=p,
            wertung=Wertung.DAFUER,
        )
        with self.assertRaises(ProtectedError):
            p.delete()


class PreisfeldIstKeinEingabefeldTests(TestCase):
    """`editable=False` haelt `aktueller_preis` strukturell aus jedem Formular.

    `save()` verwirft eine Zuweisung ohnehin still. In einem ModelForm waere das
    eine Falle: jemand tippt einen Preis ein, nichts passiert, nichts meldet sich.
    """

    def test_das_feld_ist_nicht_editierbar(self):
        self.assertFalse(Objekt._meta.get_field("aktueller_preis").editable)

    def test_das_feld_bleibt_eine_echte_spalte(self):
        # Sonst schriebe `save()` es nicht und das Nachlesen liefe ins Leere.
        namen = [f.name for f in Objekt._meta.fields]
        self.assertIn("aktueller_preis", namen)

    def test_ein_formular_ueber_alle_felder_enthaelt_es_nicht(self):
        form = modelform_factory(Objekt, fields="__all__")
        self.assertNotIn("aktueller_preis", form.base_fields)

    def test_wer_es_ausdruecklich_ins_formular_nimmt_bekommt_einen_fehler(self):
        # Der eigentliche Riegel: es scheitert laut statt still.
        with self.assertRaises(FieldError):
            modelform_factory(Objekt, fields=["url", "aktueller_preis"])

    def test_preisverlauf_bleibt_das_eingabefeld(self):
        self.assertTrue(Preisverlauf._meta.get_field("preis").editable)


class AdminStatusActionTests(TestCase):
    """Der Statuswechsel im Admin geht denselben Weg wie spaeter die Oberflaeche."""

    def setUp(self):
        self.person = Person.objects.create_superuser("steffen", password="geheim123")
        self.objekt = Objekt.objects.create(url="https://x/1")
        self.modeladmin = admin.site._registry[Objekt]

    def _request(self):
        req = RequestFactory().post("/admin/objekte/objekt/")
        req.user = self.person
        req.session = {}
        req._messages = FallbackStorage(req)
        return req

    def _aktion(self, name):
        from objekte.admin import STATUS_ACTIONS

        return next(a for a in STATUS_ACTIONS if a.__name__ == name)

    def _ausfuehren(self, name):
        self._aktion(name)(
            self.modeladmin, self._request(), Objekt.objects.filter(pk=self.objekt.pk)
        )

    def test_es_gibt_eine_action_je_status(self):
        from objekte.admin import STATUS_ACTIONS

        self.assertEqual(len(STATUS_ACTIONS), len(Status.choices))

    def test_die_action_setzt_den_status(self):
        self._ausfuehren("status_auf_heisse_spur")
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.status, Status.HEISSE_SPUR)

    def test_die_action_protokolliert_die_aenderung(self):
        self._ausfuehren("status_auf_heisse_spur")
        self.assertEqual(self.objekt.statusaenderungen.count(), 1)

    def test_das_protokoll_traegt_den_angemeldeten_benutzer(self):
        self._ausfuehren("status_auf_heisse_spur")
        self.assertEqual(self.objekt.statusaenderungen.first().person_id, self.person.pk)

    def test_derselbe_status_erzeugt_keinen_eintrag(self):
        self._ausfuehren("status_auf_neu")
        self.assertEqual(self.objekt.statusaenderungen.count(), 0)

    def test_die_action_ruehrt_den_preis_nicht_an(self):
        o = Objekt.objects.create(url="https://x/2", aktueller_preis=Decimal("250000"))
        self._aktion("status_auf_raus")(
            self.modeladmin, self._request(), Objekt.objects.filter(pk=o.pk)
        )
        o.refresh_from_db()
        self.assertEqual(o.aktueller_preis, Decimal("250000.00"))

    def test_status_bleibt_im_formular_gesperrt(self):
        # Sonst gaebe es zwei Wege und nur einer protokolliert.
        req = self._request()
        self.assertNotIn("status", self.modeladmin.get_form(req).base_fields)


class SchnellerfassungTests(TestCase):
    """Ein Objekt einwerfen muss Sekunden dauern - ein URL-Feld, ein Knopf.

    Alles, was hier zur Pflicht wuerde, kostet die Liste ihre Pflege: wer beim
    Einwerfen erst Ort und Flaeche heraussuchen muss, wirft nichts mehr ein.

    `objekt_anlegen` antwortet grundsaetzlich mit einer Umleitung, auch im
    Fehlerfall - ein Neuladen legte sonst dasselbe Objekt ein zweites Mal an.
    """

    def setUp(self):
        self.person = Person.objects.create_user(
            "steffen", password="ein-langes-passwort", first_name="Steffen", last_name="P."
        )
        self.client.force_login(self.person)

    def _einwerfen(self, url="https://www.idealista.com/inmueble/12345/", **kwargs):
        return self.client.post("/einwerfen/", {"url": url}, **kwargs)

    # --- der gute Fall ---------------------------------------------------

    def test_einwerfen_legt_ein_objekt_an(self):
        self._einwerfen()
        self.assertEqual(Objekt.objects.count(), 1)

    def test_das_objekt_traegt_die_url_im_original(self):
        self._einwerfen()
        self.assertEqual(
            Objekt.objects.get().url, "https://www.idealista.com/inmueble/12345/"
        )

    def test_eine_url_ohne_schema_wird_angelegt(self):
        # Am Handy tippt niemand "https://" mit. Ein abgewiesener Einwurf
        # kostet genau die Sekunden, um die es hier geht.
        self._einwerfen("www.idealista.com/inmueble/12345/")
        self.assertEqual(Objekt.objects.count(), 1)

    def test_das_fehlende_schema_wird_mit_https_ergaenzt(self):
        self._einwerfen("www.idealista.com/inmueble/12345/")
        self.assertEqual(
            Objekt.objects.get().url, "https://www.idealista.com/inmueble/12345/"
        )

    def test_eine_protokollrelative_eingabe_bekommt_ebenfalls_https(self):
        self._einwerfen("//www.idealista.com/inmueble/12345/")
        self.assertEqual(
            Objekt.objects.get().url, "https://www.idealista.com/inmueble/12345/"
        )

    def test_ein_vorhandenes_schema_wird_nicht_ausgetauscht(self):
        # Aus http:// wird kein https://. Sonst zeigte die gespeicherte URL
        # woandershin als die eingeworfene.
        self._einwerfen("http://www.beispiel.de/x/")
        self.assertEqual(Objekt.objects.get().url, "http://www.beispiel.de/x/")

    def test_eine_url_mit_schema_bleibt_sonst_voellig_unveraendert(self):
        """Tracking-Parameter und abschliessender Schraegstrich bleiben stehen.

        Sie sind der Grund, warum die Roh-URL kein Dublettenschluessel ist -
        aber gespeichert wird trotzdem das, was eingeworfen wurde.
        """
        roh = "https://www.beispiel.de/x/?utm_source=mail&utm_medium=email"
        self._einwerfen(roh)
        self.assertEqual(Objekt.objects.get().url, roh)

    def test_eine_kaputte_eingabe_wird_auch_mit_schema_noch_abgewiesen(self):
        # "kein-link" wird zu "https://kein-link" - ein Host ohne TLD, den der
        # Validator weiterhin ablehnt. Das Voranstellen darf den Riegel nicht
        # aufmachen.
        self._einwerfen("kein-link")
        self.assertEqual(Objekt.objects.count(), 0)

    def test_die_dublette_greift_auch_bei_schemaloser_eingabe(self):
        # Zweimal dieselbe schemalose Form: gespeichert ist beide Male
        # dieselbe ergaenzte URL, also muss der Blick sie finden.
        self._einwerfen("www.beispiel.de/x")
        self._einwerfen("www.beispiel.de/x")
        self.assertEqual(Objekt.objects.count(), 1)

    def test_eine_schemalose_eingabe_findet_das_bestehende_objekt_mit_schema(self):
        self._einwerfen("https://www.beispiel.de/x")
        pk = Objekt.objects.get().pk
        self.assertEqual(self._einwerfen("www.beispiel.de/x")["Location"], f"/objekt/{pk}/")

    def test_umgebende_leerzeichen_werden_abgeschnitten(self):
        self._einwerfen("  https://beispiel.de/1  ")
        self.assertEqual(Objekt.objects.get().url, "https://beispiel.de/1")

    def test_das_objekt_traegt_die_einwerfende_person(self):
        self._einwerfen()
        self.assertEqual(Objekt.objects.get().eingestellt_von_id, self.person.pk)

    def test_das_objekt_traegt_die_person_auch_als_letzte_aenderung(self):
        self._einwerfen()
        self.assertEqual(Objekt.objects.get().zuletzt_geaendert_von_id, self.person.pk)

    def test_die_quelle_steht_auf_url_eingeworfen(self):
        # Nicht "von Hand angelegt" - das ist der Weg ueber den Admin. Ab
        # Schritt 2 haengt am selben Feld die Unterscheidung zum Suchagenten.
        self._einwerfen()
        self.assertEqual(Objekt.objects.get().quelle, Quelle.URL_EINGEWORFEN)

    def test_der_status_bleibt_auf_dem_default(self):
        self._einwerfen()
        self.assertEqual(Objekt.objects.get().status, Status.NEU)

    def test_der_zustand_bleibt_auf_dem_default(self):
        self._einwerfen()
        self.assertEqual(Objekt.objects.get().zustand, Zustand.UNKLAR)

    def test_einwerfen_erzeugt_keinen_preiseintrag(self):
        # Ohne Preis gibt es nichts zu verlaufen. Ein Eintrag ueber 0 € waere
        # als spaetere "Senkung" die falsche Ausgangslage.
        self._einwerfen()
        self.assertEqual(Objekt.objects.get().preise.count(), 0)

    def test_nach_dem_einwerfen_wird_umgeleitet(self):
        # POST/Redirect/GET: sonst legt ein Neuladen dasselbe Objekt noch einmal an.
        self.assertEqual(self._einwerfen().status_code, 302)

    def test_die_umleitung_fuehrt_auf_die_liste(self):
        self.assertEqual(self._einwerfen()["Location"], "/")

    def test_das_eingeworfene_objekt_steht_danach_in_der_liste(self):
        """Seit Abschnitt 4 steht in der Objektspalte `Portal · ID`, nicht die URL.

        Geprueft wird die verlinkte Zeile, nicht nur die Bezeichnung: eine
        Bezeichnung ohne Link waere in einer Liste, aus der man ins Objekt
        springt, nur die halbe Zusage.
        """
        antwort = self._einwerfen(follow=True)
        pk = Objekt.objects.get().pk
        # NACHGEZOGEN am 04.09.: der Verweis traegt jetzt eine Klasse, und
        # ohne Titel ist es `titel nackt` - solche Objekte sind erkennbar
        # unfertig und werden gedaempft gesetzt. Gemessen am Element und an
        # seinen Klassen, nicht an einer `class="…"`-Zeichenkette.
        verweise = [
            klassen
            for klassen in _klassen_von(antwort, "titel")
            if "nackt" in klassen
        ]
        self.assertEqual(len(verweise), 1)
        self.assertContains(antwort, f'href="/objekt/{pk}/">idealista · 12345</a>')

    def test_die_bestaetigung_verlinkt_auf_die_objektansicht(self):
        antwort = self._einwerfen(follow=True)
        pk = Objekt.objects.get().pk
        self.assertContains(antwort, f'<a href="/objekt/{pk}/">ergänzen</a>')

    # --- Dubletten --------------------------------------------------------

    def test_dieselbe_url_legt_kein_zweites_objekt_an(self):
        self._einwerfen()
        self._einwerfen()
        self.assertEqual(Objekt.objects.count(), 1)

    def test_dieselbe_url_leitet_auf_das_bestehende_objekt(self):
        self._einwerfen()
        pk = Objekt.objects.get().pk
        self.assertEqual(self._einwerfen()["Location"], f"/objekt/{pk}/")

    def test_dieselbe_url_meldet_die_dublette(self):
        self._einwerfen()
        self.assertContains(
            self._einwerfen(follow=True), "Das Inserat liegt schon in der Liste"
        )

    def test_ein_zusaetzlicher_schraegstrich_gilt_als_dublette(self):
        self._einwerfen("https://beispiel.de/1")
        self._einwerfen("https://beispiel.de/1/")
        self.assertEqual(Objekt.objects.count(), 1)

    def test_ein_fehlender_schraegstrich_gilt_ebenfalls_als_dublette(self):
        # Die Gegenrichtung. Ohne beidseitige Normalisierung faende der
        # Vergleich nur eine der beiden.
        self._einwerfen("https://beispiel.de/1/")
        self._einwerfen("https://beispiel.de/1")
        self.assertEqual(Objekt.objects.count(), 1)

    def test_eine_andere_url_ist_keine_dublette(self):
        self._einwerfen("https://beispiel.de/1")
        self._einwerfen("https://beispiel.de/2")
        self.assertEqual(Objekt.objects.count(), 2)

    def test_ein_verworfenes_objekt_wird_ebenfalls_als_dublette_erkannt(self):
        """Sonst prueft in drei Monaten jemand dasselbe Objekt erneut.

        Genau der Fall, fuer den verworfene Objekte nicht geloescht, sondern
        nur ausgeblendet werden. Ein Vergleich ueber `sichtbar()` uebersaehe ihn.
        """
        self._einwerfen("https://beispiel.de/1")
        Objekt.objects.get().status_setzen(self.person, Status.RAUS)
        self._einwerfen("https://beispiel.de/1")
        self.assertEqual(Objekt.objects.count(), 1)

    def test_bei_mehreren_treffern_gewinnt_das_aelteste(self):
        """Altbestand aus der Zeit vor dieser Pruefung.

        Ueber die Oberflaeche kann es zwei Objekte mit derselben URL nicht
        mehr geben - genau deshalb legt dieser Test sie direkt am Modell an.
        Das aeltere ist das Original; die spaeteren sind die Dubletten, die
        damals durchgerutscht sind. Wer auf das neuere geleitet wird, findet
        dort die Vota und Notizen nicht, die am Original haengen.
        """
        aelteres = Objekt.objects.create(url="https://beispiel.de/1")
        Objekt.objects.create(url="https://beispiel.de/1")
        self.assertEqual(
            self._einwerfen("https://beispiel.de/1")["Location"],
            f"/objekt/{aelteres.pk}/",
        )

    # --- der schlechte Fall ----------------------------------------------

    def test_eine_kaputte_url_legt_nichts_an(self):
        self._einwerfen("kein-link")
        self.assertEqual(Objekt.objects.count(), 0)

    def test_eine_kaputte_url_leitet_auf_die_liste_zurueck(self):
        # Kein gerendertes Formular: die View antwortet ausnahmslos mit Redirect.
        self.assertEqual(self._einwerfen("kein-link")["Location"], "/")

    def test_eine_kaputte_url_meldet_sich_als_fehler(self):
        self.assertContains(
            self._einwerfen("kein-link", follow=True), "Das ist kein gültiger Link."
        )

    def test_ein_leeres_feld_legt_nichts_an(self):
        self._einwerfen("")
        self.assertEqual(Objekt.objects.count(), 0)

    def test_ein_leeres_feld_meldet_sich_eigenstaendig(self):
        # Andere Meldung als bei einer kaputten URL - "kein gültiger Link" waere
        # bei leerem Feld irrefuehrend.
        self.assertContains(
            self._einwerfen("", follow=True), "Bitte einen Link eintragen."
        )

    def test_eine_zu_lange_url_legt_nichts_an(self):
        """Ohne eigene Laengenpruefung ein Datenbankfehler und damit ein 500er.

        Seit Abschnitt 6 steht kein Formular mehr davor, das `max_length`
        pruefen wuerde. Der Hostname ist ausdruecklich gueltig, sonst wiese
        schon der Validator ab und der Test waere fuer die Laenge blind.
        """
        self._einwerfen("https://beispiel.de/" + "a" * 600)
        self.assertEqual(Objekt.objects.count(), 0)

    def test_eine_zu_lange_url_erzeugt_keinen_serverfehler(self):
        self.assertEqual(self._einwerfen("https://beispiel.de/" + "a" * 600).status_code, 302)

    # --- Methode und Anmeldung -------------------------------------------

    def test_einwerfen_per_get_geht_nicht(self):
        self.assertEqual(self.client.get("/einwerfen/").status_code, 405)

    def test_einwerfen_ohne_anmeldung_legt_nichts_an(self):
        self.client.logout()
        self._einwerfen()
        self.assertEqual(Objekt.objects.count(), 0)

    def test_einwerfen_ohne_anmeldung_fuehrt_auf_die_anmeldeseite(self):
        self.client.logout()
        self.assertEqual(
            self._einwerfen()["Location"], "/anmelden/?next=/einwerfen/"
        )

    # --- das Feld selbst --------------------------------------------------

    def test_die_liste_traegt_das_erfassungsfeld(self):
        self.assertContains(self.client.get("/"), 'name="url"')

    def test_das_erfassungsfeld_sendet_an_die_einwurfadresse(self):
        self.assertContains(self.client.get("/"), 'action="/einwerfen/"')

    def test_das_erfassungsfeld_ist_das_einzige_eingabefeld_der_erfassung(self):
        """Ein zweites Feld waere der Anfang vom Formular mit zwanzig Feldern.

        Gezaehlt wird im Erfassungsformular, nicht auf der ganzen Seite - das
        CSRF-Token und das Abmelde-Formular im Kopf zaehlen nicht mit.
        """
        seite = self.client.get("/").content.decode()
        block = seite[seite.index('action="/einwerfen/"') :]
        block = block[: block.index("</form>")]
        self.assertEqual(len(re.findall(r"<input |<select |<textarea ", block)), 2)


class ObjektlisteTests(TestCase):
    """Die Liste ohne Filter und Sortierung: was steht drin, was steht nicht drin.

    Filter, Sortierung, Votum-Uebersicht und die Markierung von Preissenkungen
    kommen spaeter und sind hier bewusst nicht bezeugt.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="ein-langes-passwort")
        self.client.force_login(self.person)

    def _seite(self):
        return self.client.get("/")

    # --- was in der Liste steht -------------------------------------------

    def test_die_leere_liste_traegt_einen_hinweis(self):
        self.assertContains(self._seite(), "Noch kein Objekt in der Liste.")

    def test_ein_objekt_erscheint_in_der_liste(self):
        Objekt.objects.create(url="https://x/1", titel="Finca bei Ronda")
        self.assertContains(self._seite(), "Finca bei Ronda")

    def test_ein_objekt_ohne_titel_zeigt_die_url(self):
        # Der Normalfall direkt nach dem Einwerfen: mehr ist noch nicht bekannt.
        Objekt.objects.create(url="https://x/nur-ein-link")
        self.assertContains(self._seite(), ">https://x/nur-ein-link</a>")

    def test_die_liste_zeigt_den_ort(self):
        Objekt.objects.create(url="https://x/1", ort="Ronda")
        self.assertContains(self._seite(), "Ronda")

    def test_die_liste_zeigt_die_region(self):
        Objekt.objects.create(url="https://x/1", region="Serranía de Ronda")
        self.assertContains(self._seite(), "Serranía de Ronda")

    def test_die_liste_zeigt_das_land_ausgeschrieben(self):
        # Gespeichert ist "ES", angezeigt gehoert "Spanien" - ein nacktes
        # `{{ o.land }}` zeigte den Schluessel.
        Objekt.objects.create(url="https://x/1", land=Land.ES)
        self.assertContains(self._seite(), "Spanien")

    def test_die_liste_zeigt_die_wohnflaeche(self):
        Objekt.objects.create(url="https://x/1", wohnflaeche=Decimal("140"))
        self.assertContains(self._seite(), "140 m²")

    def test_die_liste_zeigt_die_grundstuecksgroesse(self):
        Objekt.objects.create(url="https://x/1", grundstuecksgroesse=Decimal("1200"))
        self.assertContains(self._seite(), "1.200 m²")

    def test_die_liste_zeigt_den_kaufpreis(self):
        Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("199000"))
        self.assertContains(self._seite(), "199.000 €")

    def test_die_liste_zeigt_den_zustand_ausgeschrieben(self):
        Objekt.objects.create(url="https://x/1", zustand=Zustand.KERNSANIERUNG)
        self.assertContains(self._seite(), "Kernsanierung")

    def test_die_liste_zeigt_den_status_ausgeschrieben(self):
        o = Objekt.objects.create(url="https://x/1")
        o.status_setzen(self.person, Status.HEISSE_SPUR)
        self.assertContains(self._seite(), "heiße Spur")

    # --- der Quadratmeterpreis --------------------------------------------

    def test_die_annotation_erreicht_die_ansicht(self):
        Objekt.objects.create(
            url="https://x/1", aktueller_preis=Decimal("199000"), wohnflaeche=Decimal("100")
        )
        self.assertEqual(self._seite().context["objekte"][0].qm_preis, Decimal("1990.00"))

    def test_die_liste_zeigt_den_quadratmeterpreis(self):
        Objekt.objects.create(
            url="https://x/1", aktueller_preis=Decimal("199000"), wohnflaeche=Decimal("100")
        )
        self.assertContains(self._seite(), "1.990 €")

    def test_ohne_wohnflaeche_bleibt_die_spalte_leer(self):
        # Definiert leer, nicht 0 - eine 0 liefe beim spaeteren Sortieren als
        # "am guenstigsten" nach oben. Betrifft vor allem Grundstuecke.
        Objekt.objects.create(url="https://x/1", aktueller_preis=Decimal("199000"))
        self.assertIsNone(self._seite().context["objekte"][0].qm_preis)

    # --- was nicht in der Liste steht --------------------------------------

    def test_verworfene_objekte_stehen_nicht_in_der_liste(self):
        o = Objekt.objects.create(url="https://x/1", titel="Ruine")
        o.status_setzen(self.person, Status.RAUS)
        self.assertNotContains(self._seite(), "Ruine")

    def test_verworfene_objekte_bleiben_in_der_datenbank(self):
        # Ausgeblendet, nicht geloescht - sonst prueft in drei Monaten jemand
        # dasselbe Objekt ein zweites Mal.
        o = Objekt.objects.create(url="https://x/1")
        o.status_setzen(self.person, Status.RAUS)
        self.assertTrue(Objekt.objects.filter(pk=o.pk).exists())

    def test_vom_markt_genommene_stehen_nicht_in_der_liste(self):
        o = Objekt.objects.create(url="https://x/1", titel="Verkauft")
        o.status_setzen(self.person, Status.VOM_MARKT)
        self.assertNotContains(self._seite(), "Verkauft")

    def test_objekte_in_pruefung_stehen_in_der_liste(self):
        o = Objekt.objects.create(url="https://x/1", titel="Wird geprüft")
        o.status_setzen(self.person, Status.IN_PRUEFUNG)
        self.assertContains(self._seite(), "Wird geprüft")

    # --- Reihenfolge und Kosten -------------------------------------------

    def test_das_zuletzt_eingeworfene_objekt_steht_oben(self):
        """Sonst ist das gerade Eingeworfene nicht wiederzufinden.

        Die Zeitpunkte werden ausdruecklich gesetzt: `Meta.ordering` ist
        `["-eingestellt_am"]` ohne zweites Kriterium, zwei Objekte aus
        derselben Mikrosekunde stuenden unbestimmt zueinander.
        """
        alt = Objekt.objects.create(url="https://x/alt", titel="Altes Objekt")
        neu = Objekt.objects.create(url="https://x/neu", titel="Neues Objekt")
        jetzt = timezone.now()
        Objekt.objects.filter(pk=alt.pk).update(eingestellt_am=jetzt - timedelta(days=1))
        Objekt.objects.filter(pk=neu.pk).update(eingestellt_am=jetzt)
        self.assertEqual(self._seite().context["objekte"][0].pk, neu.pk)

    def test_bei_gleichem_zeitstempel_steht_das_zuletzt_angelegte_oben(self):
        """Zwei Objekte aus derselben Mikrosekunde brauchen ein zweites Kriterium.

        `auto_now_add` vergibt den Zeitstempel; zwei Anlagen in einer Schleife
        koennen denselben treffen. Ohne `-id` waere "das zuletzt Eingeworfene
        steht oben" dann Zufall - und niemand saehe es, weil eine unbestimmte
        Reihenfolge meistens trotzdem die erwartete liefert. Ab Schritt 3 ist
        das der Normalfall: der Mail-Parser legt mehrere Objekte am Stueck an.
        """
        frueher = Objekt.objects.create(url="https://x/frueher", titel="Zuerst angelegt")
        spaeter = Objekt.objects.create(url="https://x/spaeter", titel="Danach angelegt")
        gleich = timezone.now()
        Objekt.objects.filter(pk__in=[frueher.pk, spaeter.pk]).update(eingestellt_am=gleich)
        self.assertEqual(self._seite().context["objekte"][0].pk, spaeter.pk)

    def test_das_zweite_sortierkriterium_steht_in_der_abfrage(self):
        """Strukturtest, nicht nur Verhaltenstest.

        Der Verhaltenstest darueber kann blind-gruen werden: liefert die
        Datenbank die gewuenschte Reihenfolge geschenkt - etwa weil ein Index
        oder die Einfuegereihenfolge sie zufaellig trifft - haelt er auch ohne
        das zweite Kriterium. Deshalb wird hier die kompilierte Abfrage selbst
        angesehen.
        """
        with CaptureQueriesContext(connection) as abfragen:
            list(Objekt.objects.sichtbar().mit_qm_preis())
        sql = abfragen.captured_queries[-1]["sql"].lower()
        self.assertIn("order by", sql)
        self.assertIn('"id" desc', sql)

    def test_mehr_objekte_kosten_nicht_mehr_abfragen(self):
        """Riegel gegen ein N+1. In Punkt 5 von 1 gegen 7 auf 5 gegen 50 gezogen.

        Sieben Zeilen waren zu wenig, um ein N+1 sichtbar zu machen. Punkt 5
        ist die Runde, in der es zaehlt: Paginator, drei Aggregate ueber
        `vota` und die Zahl der aktiven Personen kommen gleichzeitig dazu, und
        jede dieser drei Stellen liesse sich versehentlich je Zeile abfragen.

        Gemessen wird MIT gesetztem Filter und gesetzter Sortierung. Beides
        veraendert den Abfragepfad; ein N+1, das nur im gefilterten Stand
        auftraete, bliebe sonst unentdeckt.

        Die erwartete Zahl wird beim ersten Durchgang ERMITTELT und nicht
        hingeschrieben: Sitzung und Middleware fragen ohnehin mit, und deren
        Zahl ist nicht die Zusage, die hier gehalten werden soll. Fuenfzig ist
        die Seitengroesse - beide Messungen liegen damit auf einer Seite, und
        gezaehlt wird der Zeilenzuwachs und nicht der Sprung ins Blaettern.
        """
        adresse = "/?status=neu&sortierung=-qm_preis"
        self.client.get(adresse)  # Aufwaermen, damit Verbindungsaufbau nicht mitzaehlt.
        for nummer in range(5):
            Objekt.objects.create(url=f"https://x/{nummer}")
        with CaptureQueriesContext(connection) as mit_fuenf:
            self.client.get(adresse)
        for nummer in range(5, views.OBJEKTE_JE_SEITE):
            Objekt.objects.create(url=f"https://x/{nummer}")
        with self.assertNumQueries(len(mit_fuenf)):
            self.client.get(adresse)

    def test_mehr_vota_kosten_nicht_mehr_abfragen(self):
        """Derselbe Riegel fuer die Votum-Spalte.

        Der Zeuge darueber legt Objekte OHNE Vota an - eine Zaehlschleife
        ueber `objekt.vota` bliebe dort zwar auch teuer, aber eine
        Implementierung, die nur bei vorhandenen Vota nachfragt, kaeme
        ungesehen durch.
        """
        adresse = "/?status=neu&sortierung=-qm_preis"
        weitere = [Person.objects.create_user(f"person{n}") for n in range(4)]
        self.client.get(adresse)  # Aufwaermen.
        for nummer in range(5):
            objekt = Objekt.objects.create(url=f"https://x/{nummer}")
            for person in weitere:
                Votum.objects.create(objekt=objekt, person=person, wertung=Wertung.DAFUER)
        with CaptureQueriesContext(connection) as mit_fuenf:
            self.client.get(adresse)
        for nummer in range(5, views.OBJEKTE_JE_SEITE):
            objekt = Objekt.objects.create(url=f"https://x/{nummer}")
            for person in weitere:
                Votum.objects.create(objekt=objekt, person=person, wertung=Wertung.DAFUER)
        with self.assertNumQueries(len(mit_fuenf)):
            self.client.get(adresse)


    def test_mehr_preisverlauf_kostet_nicht_mehr_abfragen(self):
        """Der Riegel fuer die Preissenkungsmarkierung - der wichtigste dieser Runde.

        Die beiden Zeugen darueber legen Objekte OHNE Preisverlauf an. Eine
        Fassung, die den vorletzten Eintrag je Zeile einzeln nachschlaegt,
        kaeme dort ungesehen durch: wo es keinen Verlauf gibt, gibt es auch
        nichts nachzuschlagen. Gemessen wird deshalb an Objekten MIT Verlauf -
        bei fuenfzig Zeilen ist der Unterschied zwischen einer Abfrage und
        einundfuenfzig.

        Zwei Eintraege je Objekt, nicht einer: mit nur einem Eintrag bliebe
        `vorheriger_preis` ueberall NULL, das Template betraete den Zweig nie,
        und ein N+1 in genau diesem Zweig bliebe unentdeckt.

        Aufbau wie bei den Geschwistern darueber: mit gesetztem Filter und
        gesetzter Sortierung, die erwartete Zahl beim ersten Durchgang
        ERMITTELT statt hingeschrieben, und beide Messungen auf einer Seite.
        """
        adresse = "/?status=neu&sortierung=-qm_preis"

        def anlegen(von, bis):
            for nummer in range(von, bis):
                objekt = Objekt.objects.create(
                    url=f"https://x/{nummer}", aktueller_preis=Decimal("200000")
                )
                objekt.preis_setzen(self.person, Decimal("180000"))

        self.client.get(adresse)  # Aufwaermen, damit Verbindungsaufbau nicht mitzaehlt.
        anlegen(0, 5)
        with CaptureQueriesContext(connection) as mit_fuenf:
            self.client.get(adresse)
        anlegen(5, views.OBJEKTE_JE_SEITE)
        with self.assertNumQueries(len(mit_fuenf)):
            self.client.get(adresse)

    def test_die_markierung_ist_bei_dieser_messung_ueberhaupt_da(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darueber.

        Zeigte die Liste die Markierung gar nicht an - weil die Annotation
        fehlt, das Template den Zweig nicht betritt oder der Filter die
        Objekte ausblendet -, waere die Abfragezahl selbstverstaendlich
        konstant und der Zeuge darueber gruen, ohne irgendetwas zu messen.
        """
        objekt = Objekt.objects.create(
            url="https://x/1", aktueller_preis=Decimal("200000")
        )
        objekt.preis_setzen(self.person, Decimal("180000"))
        self.assertContains(
            self.client.get("/?status=neu&sortierung=-qm_preis"), "preisaenderung"
        )


class DatenblockParser(HTMLParser):
    """Liest den Datenblock der Objektansicht als Paare Beschriftung -> Wert.

    Gemessen wird damit, was neben einem Feldnamen WIRKLICH steht. Ein Zeuge
    auf die blosse Anwesenheit einer Zeichenkette faende einen Strich auch
    dann, wenn er zu einem ganz anderen Feld gehoert.
    """

    def __init__(self):
        super().__init__()
        self.paare = {}
        self._im_block = False
        self._marke = None
        self._text = ""
        self._name = None

    def handle_starttag(self, tag, attrs):
        werte = dict(attrs)
        if tag == "dl" and "daten" in werte.get("class", "").split():
            self._im_block = True
        elif self._im_block and tag in ("dt", "dd"):
            self._marke = tag
            self._text = ""

    def handle_endtag(self, tag):
        if tag == "dl":
            self._im_block = False
        elif self._im_block and tag == self._marke:
            text = " ".join(self._text.split())
            if tag == "dt":
                self._name = text
            elif self._name is not None:
                self.paare[self._name] = text
                self._name = None
            self._marke = None

    def handle_data(self, daten):
        if self._marke:
            self._text += daten


def daten_paare(antwort):
    parser = DatenblockParser()
    parser.feed(antwort.content.decode())
    return parser.paare


class ObjektansichtTests(TestCase):
    """Ein Template, vier getrennte Aktionen. Kein Inline-Edit."""

    def setUp(self):
        self.person = Person.objects.create_user(
            "steffen", password="lang-genug-123", first_name="Steffen", last_name="P."
        )
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(
            url="https://beispiel.de/1",
            titel="Finca bei Ronda",
            ort="Ronda",
            wohnflaeche=Decimal("100"),
            aktueller_preis=Decimal("199000"),
        )

    def _seite(self):
        return self.client.get(f"/objekt/{self.objekt.pk}/")

    def test_die_ansicht_zeigt_den_titel(self):
        self.assertContains(self._seite(), "Finca bei Ronda")

    def test_die_ansicht_zeigt_den_kaufpreis(self):
        self.assertContains(self._seite(), "199.000 €")

    def test_die_ansicht_zeigt_den_quadratmeterpreis(self):
        # Die Annotation muss auch hier gezogen werden - es gibt bewusst keine
        # gleichnamige Property am Modell.
        self.assertContains(self._seite(), "1.990 €/m²")

    def test_gefuellte_felder_werden_gezeigt(self):
        self.assertContains(self._seite(), "Wohnfläche")

    def test_leere_felder_werden_ANGEZEIGT(self):
        """UMGEDREHT am 03.09. Die Zusage dahinter ist die andere geworden.

        Bis dahin galt: ein leeres Feld wird ausgelassen, weil eine Liste aus
        zwanzig Gedankenstrichen die drei Zeilen verdeckt, die etwas sagen.
        Das ist die Haltung der LISTE - dort stehen fuenfzig Zeilen
        nebeneinander und jede Spalte kostet Platz.

        Auf der Objektansicht ist es umgekehrt: ein leeres Feld ist die
        AUFFORDERUNG, es zu fuellen, und genau dafuer gibt es diese Seite. Was
        gar nicht dasteht, faellt niemandem auf und wird nie nachgetragen.

        Der Zeuge misst weiter dieselbe Stelle, nur mit umgekehrtem Vorzeichen:
        `baujahr` ist am Objekt dieser Klasse nicht gesetzt.
        """
        self.assertIsNone(self.objekt.baujahr)
        self.assertContains(self._seite(), "Baujahr")

    def test_ein_leeres_feld_zeigt_einen_strich_und_nicht_nichts(self):
        """Man muss SEHEN, dass die Angabe fehlt - sonst haelt man sie fuer Null.

        Getrennt vom Zeugen darueber: dass die Beschriftung dasteht, sagt noch
        nicht, dass daneben etwas steht. Ein leeres `<dd>` truege den Namen des
        Feldes und liesse offen, ob der Wert fehlt oder 0 ist.

        Gemessen wird am PAAR und nicht an der blossen Anwesenheit eines
        Strichs irgendwo auf der Seite: die Sabotage-Gegenprobe hat genau das
        aufgedeckt - ein Zeuge auf `assertContains("—")` blieb gruen, waehrend
        das Baujahr seinen Strich schon verloren hatte, weil neun andere
        Felder ihre noch trugen.
        """
        self.assertIsNone(self.objekt.baujahr)
        self.assertEqual(daten_paare(self._seite()).get("Baujahr"), "—")

    def test_der_link_zum_inserat_traegt_noopener(self):
        self.assertContains(self._seite(), 'rel="noopener noreferrer"')

    def test_ein_verworfenes_objekt_bleibt_aufrufbar(self):
        # Aus der Liste ausgeblendet, aber erreichbar - sonst liesse es sich
        # nie zurueckholen.
        self.objekt.status_setzen(self.person, Status.RAUS)
        self.assertEqual(self._seite().status_code, 200)

    def test_die_ansicht_zeigt_den_preisverlauf(self):
        self.objekt.preis_setzen(self.person, Decimal("179000"))
        self.assertContains(self._seite(), "179.000 €")

    def test_die_ansicht_zeigt_die_vota_der_anderen(self):
        """NACHGEZOGEN am 04.09. Die Zusage hat eine Vorbedingung bekommen.

        Bis dahin sah jeder die Vota der anderen. Seit dem verdeckten Votum
        sieht sie nur, wer an DIESEM Objekt selbst gestimmt hat - deshalb
        stimmt die angemeldete Person hier zuerst ab. Ohne diese Zeile maesse
        der Zeuge die Verdeckung und nicht die Anzeige.

        Der Zeuge bleibt trotzdem stehen und wird nicht durch die neuen
        ersetzt: er ist der Riegel dagegen, dass die Freischaltung zwar
        greift, aber gar nichts mehr freischaltet.
        """
        anna = Person.objects.create_user("anna", first_name="Anna", last_name="B.")
        Votum.objects.create(
            objekt=self.objekt, person=anna, wertung=Wertung.DAFUER, begruendung="Lage"
        )
        Votum.objects.create(
            objekt=self.objekt, person=self.person, wertung=Wertung.ANSCHAUEN
        )
        self.assertContains(self._seite(), "Anna B.")

    def test_das_eigene_votum_ist_erkennbar(self):
        Votum.objects.create(
            objekt=self.objekt, person=self.person, wertung=Wertung.DAFUER
        )
        self.assertContains(self._seite(), 'value="dafuer" aria-pressed="true"')

    def test_die_ansicht_verlangt_eine_anmeldung(self):
        self.client.logout()
        self.assertEqual(self._seite().status_code, 302)

    def test_die_liste_verlinkt_auf_die_objektansicht(self):
        self.assertContains(self.client.get("/"), f'href="/objekt/{self.objekt.pk}/"')


class BearbeitenTests(TestCase):
    """`kaufpreis` ist kein Modellfeld, und die vier Schritte in `form_valid`
    stehen in einer bestimmten Reihenfolge.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(
            url="https://beispiel.de/1",
            titel="Finca",
            wohnflaeche=Decimal("140"),
            aktueller_preis=Decimal("250000"),
        )
        self.adresse = f"/objekt/{self.objekt.pk}/bearbeiten/"

    def _formulardaten(self, **abweichungen):
        """Der POST-Rumpf, so wie ihn der Browser aus dem gerenderten Formular sendet.

        Bewusst ueber `widget.format_value()` und nicht ueber die Rohwerte:
        genau der gerenderte Text geht zurueck. Ein Test, der stattdessen
        "140" schickt, wo das Formular "140,00" anzeigt, prueft den Rundlauf
        nicht, sondern umgeht ihn.
        """
        formular = self.client.get(self.adresse).context["form"]
        daten = {}
        for name, feld in formular.fields.items():
            gerendert = feld.widget.format_value(formular[name].value())
            if isinstance(gerendert, list):
                gerendert = gerendert[0] if gerendert else ""
            daten[name] = "" if gerendert is None else gerendert
        daten.update(abweichungen)
        return daten

    # --- Zuschnitt des Formulars ------------------------------------------

    def test_der_aktuelle_preis_ist_kein_formularfeld(self):
        formular = self.client.get(self.adresse).context["form"]
        self.assertNotIn("aktueller_preis", formular.fields)

    def test_stattdessen_gibt_es_das_zusatzfeld_kaufpreis(self):
        formular = self.client.get(self.adresse).context["form"]
        self.assertIn("kaufpreis", formular.fields)

    def test_der_status_ist_nicht_im_formular(self):
        # Er laeuft ueber `status_setzen()`, das die Aenderung protokolliert.
        formular = self.client.get(self.adresse).context["form"]
        self.assertNotIn("status", formular.fields)

    def test_das_kaufpreisfeld_ist_mit_dem_bestehenden_preis_vorbelegt(self):
        formular = self.client.get(self.adresse).context["form"]
        self.assertEqual(formular["kaufpreis"].value(), Decimal("250000.00"))

    # --- Zahlenformat ------------------------------------------------------

    def test_der_preis_erscheint_im_deutschen_format(self):
        self.assertContains(self.client.get(self.adresse), 'value="250.000,00"')

    def test_die_wohnflaeche_erscheint_im_deutschen_format(self):
        self.assertContains(self.client.get(self.adresse), 'value="140,00"')

    def test_dezimalfelder_laufen_nicht_als_zahlenfeld(self):
        """`<input type="number">` raeumt einen lokalisierten Wert kommentarlos leer.

        Wer dann speichert, loescht den Wert, ohne je etwas getippt zu haben.
        """
        seite = self.client.get(self.adresse).content.decode()
        self.assertNotIn('type="number" name="wohnflaeche"', seite)

    def test_rundlauf_des_zahlenformats(self):
        """Anzeigen, unveraendert absenden, Wert steht noch.

        Faengt die Mischung aus lokalisiertem Rendern und unlokalisiertem
        Lesen: dann wuerde "140,00" als 14000 gelesen.
        """
        self.client.post(self.adresse, self._formulardaten())
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.wohnflaeche, Decimal("140.00"))

    def test_der_rundlauf_laesst_auch_den_preis_stehen(self):
        self.client.post(self.adresse, self._formulardaten())
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.aktueller_preis, Decimal("250000.00"))

    # --- Preis --------------------------------------------------------------

    def test_preisaenderung_erzeugt_genau_einen_neuen_eintrag(self):
        self.client.post(self.adresse, self._formulardaten(kaufpreis="225.000,00"))
        self.assertEqual(self.objekt.preise.count(), 2)

    def test_preisaenderung_aktualisiert_den_aktuellen_preis(self):
        self.client.post(self.adresse, self._formulardaten(kaufpreis="225.000,00"))
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.aktueller_preis, Decimal("225000.00"))

    def test_speichern_ohne_preisaenderung_erzeugt_keinen_eintrag(self):
        self.client.post(self.adresse, self._formulardaten(titel="Finca, neu benannt"))
        self.assertEqual(self.objekt.preise.count(), 1)

    def test_ein_leeres_preisfeld_laesst_den_preis_stehen(self):
        # Leer heisst "nicht aendern", nicht "Preis loeschen".
        self.client.post(self.adresse, self._formulardaten(kaufpreis=""))
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.aktueller_preis, Decimal("250000.00"))

    def test_ein_leeres_preisfeld_erzeugt_keinen_eintrag(self):
        self.client.post(self.adresse, self._formulardaten(kaufpreis=""))
        self.assertEqual(self.objekt.preise.count(), 1)

    def test_ein_erster_preis_laesst_sich_von_hand_erfassen(self):
        """Ohne das Zusatzfeld liesse sich nie ein Preis erfassen.

        `Objekt.save()` legt den ersten Verlaufseintrag nur beim ANLEGEN mit
        Preis an - was ueber ein Formular nie passiert, weil
        `aktueller_preis` `editable=False` ist.
        """
        ohne = Objekt.objects.create(url="https://beispiel.de/ohne-preis")
        self.client.post(
            f"/objekt/{ohne.pk}/bearbeiten/",
            {"url": ohne.url, "zustand": Zustand.UNKLAR, "kaufpreis": "99.000,00"},
        )
        ohne.refresh_from_db()
        self.assertEqual(ohne.aktueller_preis, Decimal("99000.00"))

    def test_ein_preis_von_null_gilt_als_eingabe_nicht_als_leer(self):
        """0 € ist ein Wert, kein leeres Feld.

        Mit einer Wahrheitspruefung statt `is not None` fiele die 0 durch und
        nichts passierte - genau die Falle, vor der schon `aktueller_preis`
        gewarnt wird: jemand tippt einen Preis ein, nichts geschieht, nichts
        meldet sich. Ob 0 € fachlich sinnvoll ist, entscheidet nicht der Code.
        """
        self.client.post(self.adresse, self._formulardaten(kaufpreis="0,00"))
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.aktueller_preis, Decimal("0.00"))

    # --- Reihenfolge der vier Schritte -------------------------------------

    def _mit_aufzeichnung(self, daten):
        """Ruft das Speichern auf und protokolliert die Aufrufe am Objekt.

        Strukturtest, weil das Verhalten die Reihenfolge NICHT unterscheidet:
        innerhalb einer Anfrage traegt die Instanz denselben Preis vor wie
        nach `save()`. Der Unterschied zeigt sich erst, wenn zwischen dem
        Laden des Formulars und dem Speichern jemand anders den Preis
        aendert - und das ist ueber den Testclient nicht erreichbar. Gemessen
        am 28.08.2026: die Vertauschung blieb ohne diesen Test unbemerkt.
        """
        reihenfolge = []
        echtes_save = Objekt.save
        echtes_preis_setzen = Objekt.preis_setzen

        def save(self, *args, **kwargs):
            reihenfolge.append("save")
            return echtes_save(self, *args, **kwargs)

        def preis_setzen(self, *args, **kwargs):
            reihenfolge.append("preis_setzen")
            return echtes_preis_setzen(self, *args, **kwargs)

        with mock.patch.object(Objekt, "save", save), mock.patch.object(
            Objekt, "preis_setzen", preis_setzen
        ):
            self.client.post(self.adresse, daten)
        return reihenfolge

    def test_das_objekt_wird_vor_dem_preis_gespeichert(self):
        """Schritt 3 vor Schritt 4.

        `objekt.save()` holt `aktueller_preis` aus der Datenbank zurueck. Erst
        danach ist der Vergleich in Schritt 4 gegen den massgeblichen Wert
        gerichtet; vorher waere die Bezugsgroesse der Stand vom Oeffnen des
        Formulars.
        """
        reihenfolge = self._mit_aufzeichnung(self._formulardaten(kaufpreis="225.000,00"))
        self.assertLess(reihenfolge.index("save"), reihenfolge.index("preis_setzen"))

    def test_ohne_preisaenderung_wird_das_objekt_genau_einmal_gespeichert(self):
        """`form.save(commit=False)`, nicht `form.save()`.

        Mit `commit=True` speichert das Formular einmal und Schritt 3 gleich
        noch einmal. Das Ergebnis stimmt trotzdem, deshalb faellt es keinem
        Verhaltenstest auf - es ist eine Schreiboperation zu viel je
        Bearbeitung und die Abweichung von der vorgegebenen Abfolge.
        """
        reihenfolge = self._mit_aufzeichnung(self._formulardaten(ort="Ronda"))
        self.assertEqual(reihenfolge, ["save"])

    # --- Herkunft der Aenderung --------------------------------------------

    def test_eine_aenderung_kommt_an(self):
        self.client.post(self.adresse, self._formulardaten(ort="Ronda"))
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.ort, "Ronda")

    def test_zuletzt_geaendert_von_wird_gesetzt(self):
        anna = Person.objects.create_user("anna", password="lang-genug-123")
        self.client.force_login(anna)
        self.client.post(self.adresse, self._formulardaten(ort="Ronda"))
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.zuletzt_geaendert_von_id, anna.pk)

    def test_nach_dem_speichern_fuehrt_der_weg_auf_die_objektansicht(self):
        antwort = self.client.post(self.adresse, self._formulardaten())
        self.assertEqual(antwort["Location"], f"/objekt/{self.objekt.pk}/")

    # --- Constraint ---------------------------------------------------------

    def test_kollidierendes_paar_ist_kein_serverfehler(self):
        """Django 5.2 prueft Constraints schon im ModelForm.

        Ohne diese Pruefung liefe der partielle Unique-Index erst beim INSERT
        auf und der Anwender saehe einen 500er statt eines Formularfehlers.
        """
        Objekt.objects.create(
            url="https://beispiel.de/9", portal=Portal.IDEALISTA, inserats_id="123"
        )
        antwort = self.client.post(
            self.adresse,
            self._formulardaten(portal=Portal.IDEALISTA, inserats_id="123"),
        )
        self.assertEqual(antwort.status_code, 200)

    def test_das_kollidierende_paar_erscheint_als_formularfehler(self):
        Objekt.objects.create(
            url="https://beispiel.de/9", portal=Portal.IDEALISTA, inserats_id="123"
        )
        antwort = self.client.post(
            self.adresse,
            self._formulardaten(portal=Portal.IDEALISTA, inserats_id="123"),
        )
        self.assertTrue(antwort.context["form"].errors)

    def test_das_kollidierende_paar_wird_nicht_gespeichert(self):
        Objekt.objects.create(
            url="https://beispiel.de/9", portal=Portal.IDEALISTA, inserats_id="123"
        )
        self.client.post(
            self.adresse,
            self._formulardaten(portal=Portal.IDEALISTA, inserats_id="123"),
        )
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.inserats_id, "")

    # --- Anmeldung ----------------------------------------------------------

    def test_bearbeiten_verlangt_eine_anmeldung(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.adresse).status_code, 302)


class VotumOberflaecheTests(TestCase):
    """Ein Votum je Person und Objekt, jederzeit aenderbar."""

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(url="https://beispiel.de/1")
        self.adresse = f"/objekt/{self.objekt.pk}/votum/"

    def _abstimmen(self, wertung=Wertung.DAFUER, begruendung=""):
        return self.client.post(
            self.adresse, {"wertung": wertung, "begruendung": begruendung}
        )

    def test_ein_votum_wird_gespeichert(self):
        self._abstimmen()
        self.assertEqual(self.objekt.vota.count(), 1)

    def test_das_votum_traegt_die_abstimmende_person(self):
        self._abstimmen()
        self.assertEqual(self.objekt.vota.get().person_id, self.person.pk)

    def test_die_begruendung_wird_gespeichert(self):
        self._abstimmen(begruendung="Lage stimmt")
        self.assertEqual(self.objekt.vota.get().begruendung, "Lage stimmt")

    def test_das_zweite_votum_legt_kein_zweites_an(self):
        # `update_or_create`, nicht `create` - ein `create` liefe in den
        # Unique-Constraint und damit in einen 500er.
        self._abstimmen(Wertung.DAFUER)
        self._abstimmen(Wertung.RAUS)
        self.assertEqual(self.objekt.vota.count(), 1)

    def test_das_zweite_votum_ersetzt_die_wertung(self):
        self._abstimmen(Wertung.DAFUER)
        self._abstimmen(Wertung.RAUS)
        self.assertEqual(self.objekt.vota.get().wertung, Wertung.RAUS)

    def test_eine_zweite_person_stimmt_eigenstaendig_ab(self):
        self._abstimmen(Wertung.DAFUER)
        anna = Person.objects.create_user("anna", password="lang-genug-123")
        self.client.force_login(anna)
        self._abstimmen(Wertung.RAUS)
        self.assertEqual(self.objekt.vota.count(), 2)

    def test_eine_unbekannte_wertung_legt_nichts_an(self):
        self._abstimmen("vielleicht")
        self.assertEqual(self.objekt.vota.count(), 0)

    def test_eine_unbekannte_wertung_meldet_sich(self):
        antwort = self.client.post(
            self.adresse, {"wertung": "vielleicht"}, follow=True
        )
        self.assertContains(antwort, "Unbekannte Wertung.")

    def test_votum_per_get_geht_nicht(self):
        self.assertEqual(self.client.get(self.adresse).status_code, 405)

    def test_ein_votum_ruehrt_zuletzt_geaendert_am_nicht_an(self):
        """Das ist der Grund fuer die spaetere Aktivitaets-Annotation.

        Ein Votum fasst das Objekt nicht an. Ohne `mit_aktivitaet()` bliebe
        ein Objekt, das gerade ein Votum bekommen hat, in der Liste
        unmarkiert.
        """
        vorher = Objekt.objects.get(pk=self.objekt.pk).zuletzt_geaendert_am
        self._abstimmen()
        self.assertEqual(
            Objekt.objects.get(pk=self.objekt.pk).zuletzt_geaendert_am, vorher
        )


class StatusOberflaecheTests(TestCase):
    """Der Status wird immer manuell gesetzt und die Aenderung protokolliert."""

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(url="https://beispiel.de/1")
        self.adresse = f"/objekt/{self.objekt.pk}/status/"

    def test_der_status_wird_gesetzt(self):
        self.client.post(self.adresse, {"status": Status.HEISSE_SPUR})
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.status, Status.HEISSE_SPUR)

    def test_der_wechsel_wird_protokolliert(self):
        self.client.post(self.adresse, {"status": Status.HEISSE_SPUR})
        self.assertEqual(self.objekt.statusaenderungen.count(), 1)

    def test_das_protokoll_traegt_die_angemeldete_person(self):
        self.client.post(self.adresse, {"status": Status.HEISSE_SPUR})
        self.assertEqual(
            self.objekt.statusaenderungen.get().person_id, self.person.pk
        )

    def test_derselbe_status_erzeugt_keinen_eintrag(self):
        self.client.post(self.adresse, {"status": Status.NEU})
        self.assertEqual(self.objekt.statusaenderungen.count(), 0)

    def test_derselbe_status_meldet_keinen_erfolg(self):
        # Eine Bestaetigung fuer eine nicht stattgefundene Aenderung ist eine
        # Falschmeldung.
        antwort = self.client.post(self.adresse, {"status": Status.NEU}, follow=True)
        self.assertNotContains(antwort, "Status steht auf")

    def test_ein_unbekannter_status_aendert_nichts(self):
        self.client.post(self.adresse, {"status": "erfunden"})
        self.objekt.refresh_from_db()
        self.assertEqual(self.objekt.status, Status.NEU)

    def test_status_per_get_geht_nicht(self):
        self.assertEqual(self.client.get(self.adresse).status_code, 405)

    def test_ein_statuswechsel_schreibt_zuletzt_geaendert_am_fort(self):
        """Anders als Votum und Notiz.

        Abschnitt 10 der Spezifikation sagt pauschal, keine der drei Aktionen
        schreibe `zuletzt_geaendert_am`. Fuer den Status stimmt das nicht:
        `Objekt.status_setzen()` speichert das Objekt mit, und
        `zuletzt_geaendert_am` ist `auto_now`. Abschnitt 3 sagt dasselbe
        andersherum ("Statuswechsel laufen ueber `objekt.save()` und sind
        bereits enthalten"). Bezeugt ist hier, was der Code tut - der
        Widerspruch ist gemeldet, nicht aufgeloest.
        """
        vorher = Objekt.objects.get(pk=self.objekt.pk).zuletzt_geaendert_am
        self.client.post(self.adresse, {"status": Status.HEISSE_SPUR})
        self.assertGreater(
            Objekt.objects.get(pk=self.objekt.pk).zuletzt_geaendert_am, vorher
        )


class NotizOberflaecheTests(TestCase):
    """Freitext am Objekt, unabhaengig vom Votum, chronologisch."""

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(url="https://beispiel.de/1")
        self.adresse = f"/objekt/{self.objekt.pk}/notiz/"

    def test_eine_notiz_wird_gespeichert(self):
        self.client.post(self.adresse, {"text": "Dach sieht neu aus"})
        self.assertEqual(self.objekt.notizen.count(), 1)

    def test_die_notiz_traegt_die_schreibende_person(self):
        self.client.post(self.adresse, {"text": "Dach sieht neu aus"})
        self.assertEqual(self.objekt.notizen.get().person_id, self.person.pk)

    def test_eine_leere_notiz_wird_abgewiesen(self):
        self.client.post(self.adresse, {"text": ""})
        self.assertEqual(self.objekt.notizen.count(), 0)

    def test_nur_leerzeichen_gelten_als_leer(self):
        self.client.post(self.adresse, {"text": "   \n  "})
        self.assertEqual(self.objekt.notizen.count(), 0)

    def test_die_leere_notiz_meldet_sich(self):
        antwort = self.client.post(self.adresse, {"text": ""}, follow=True)
        self.assertContains(antwort, "Eine leere Notiz wird nicht gespeichert.")

    def test_mehrere_notizen_je_person_sind_erlaubt(self):
        self.client.post(self.adresse, {"text": "erste"})
        self.client.post(self.adresse, {"text": "zweite"})
        self.assertEqual(self.objekt.notizen.count(), 2)

    def test_die_juengste_notiz_steht_oben(self):
        self.client.post(self.adresse, {"text": "erste"})
        self.client.post(self.adresse, {"text": "zweite"})
        self.assertEqual(self.objekt.notizen.first().text, "zweite")

    def test_notiz_per_get_geht_nicht(self):
        self.assertEqual(self.client.get(self.adresse).status_code, 405)

    def test_eine_notiz_ruehrt_zuletzt_geaendert_am_nicht_an(self):
        vorher = Objekt.objects.get(pk=self.objekt.pk).zuletzt_geaendert_am
        self.client.post(self.adresse, {"text": "Dach sieht neu aus"})
        self.assertEqual(
            Objekt.objects.get(pk=self.objekt.pk).zuletzt_geaendert_am, vorher
        )


class PortalUndIdTests(SimpleTestCase):
    """Zusagen 1 bis 3: Portal und Inserats-ID aus der URL, ohne Seitenabruf.

    Keine Datenbank noetig - genau das ist der Zweck des eigenen Moduls.
    """

    # --- Idealista (Zusage 1) --------------------------------------------

    def test_idealista_mit_sprachpraefix(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.com/en/inmueble/12345/"),
            ("idealista", "12345"),
        )

    def test_idealista_ohne_sprachpraefix(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.com/inmueble/12345/"),
            ("idealista", "12345"),
        )

    def test_idealista_ohne_abschliessenden_schraegstrich(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.com/inmueble/12345"),
            ("idealista", "12345"),
        )

    def test_idealista_mit_query_parametern(self):
        # Die Parameter stehen nicht im Pfad - `urlsplit` hat sie abgetrennt,
        # bevor der Ausdruck sein `$` erreicht.
        self.assertEqual(
            portal_und_id("https://www.idealista.com/inmueble/12345/?utm_source=mail"),
            ("idealista", "12345"),
        )

    def test_idealista_ohne_www(self):
        self.assertEqual(
            portal_und_id("https://idealista.com/inmueble/12345/"),
            ("idealista", "12345"),
        )

    def test_idealista_italien_wird_nicht_mehr_erkannt(self):
        """`.it` ist am 02.09. herausgefallen - und zwar mitsamt dem Pfadmuster.

        Die Domain stand hier, ohne dass je ein Pfad fuer sie belegt war: das
        spanische `inmueble` traf auf ihr nur, weil niemand eine echte
        italienische URL dagegengehalten hat. Damit taeuschte sie Abdeckung
        vor, die es nicht gab.

        Der frueher hier stehende Zeuge behauptete das Gegenteil - er war
        gruen, weil die Domain in der Liste stand, nicht weil die Erkennung
        etwas leistete. Eine `.it`-URL laeuft jetzt auf "sonstiges", also auf
        das leere Paar, und das ist der richtige Ausgang.
        """
        self.assertEqual(
            portal_und_id("https://www.idealista.it/inmueble/12345/"), ("", "")
        )

    def test_idealista_portugal_wird_nicht_mehr_erkannt(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.pt/inmueble/12345/"), ("", "")
        )

    def test_ein_landessprachlicher_pfad_auf_it_wird_ebenfalls_nicht_erkannt(self):
        """Der zweite Riegel an derselben Domain.

        Der Zeuge darueber koennte auch dann gruen sein, wenn nur das
        spanische Pfadmuster nicht mehr traefe, die Domain aber noch in der
        Tabelle stuende. Hier steht der landessprachliche Pfad - beide Wege
        auf `.it` fuehren ins Leere.
        """
        self.assertEqual(
            portal_und_id("https://www.idealista.it/immobile/12345/"), ("", "")
        )

    # --- fotocasa ---------------------------------------------------------

    def test_fotocasa_expose(self):
        """Die URL aus der Spezifikation, woertlich.

        Der Ausstattungspfad davor ist beliebig lang und darf nicht Teil des
        Musters werden; das letzte Segment `/d` wird uebersprungen.
        """
        self.assertEqual(
            portal_und_id(
                "https://www.fotocasa.es/de/kaufen/wohnimmobilie/marbella/"
                "aire-acondicionado-heizung-terrasse-schwimmbad/190346632/d"
            ),
            ("fotocasa", "190346632"),
        )

    def test_fotocasa_neubau_liefert_die_zweite_zahl(self):
        """Zwei Zahlen im Pfad - gefragt ist die LETZTE.

        Die Neubau-URL traegt `.../20561853/189207445`. Ein Muster, das die
        erste Zahl nimmt, ist an der Expose-URL darueber nicht zu
        unterscheiden und faellt still auf den falschen Wert. Die
        Spezifikation fuehrt diesen Fall ausdruecklich als Strukturbeleg auf.
        """
        self.assertEqual(
            portal_und_id(
                "https://www.fotocasa.es/de/kaufen/wohnimmobilie/neubau/marbella/"
                "20561853/189207445"
            ),
            ("fotocasa", "189207445"),
        )

    def test_fotocasa_neubau_liefert_NICHT_die_erste_zahl(self):
        """Getrennter Zeuge, weil es die getrennte Zusage ist.

        Der Zeuge darueber faellt auch dann, wenn gar nichts erkannt wird -
        dieser sagt zusaetzlich, dass nicht die falsche der beiden Zahlen
        herauskommt.
        """
        _, inserats_id = portal_und_id(
            "https://www.fotocasa.es/de/kaufen/wohnimmobilie/neubau/marbella/"
            "20561853/189207445"
        )
        self.assertNotEqual(inserats_id, "20561853")

    def test_fotocasa_sprachvariante_liefert_dieselbe_id(self):
        """Sprachunabhaengig - sonst legen zwei Personen dasselbe Objekt doppelt an.

        Dasselbe Inserat hat auf der deutschen und der spanischen Fassung
        einen anderen Pfad (`kaufen/wohnimmobilie` gegen `comprar/vivienda`),
        aber dieselbe ID. Haengt das Muster am Sprachpraefix oder an den
        Woertern dahinter, greift der Dublettenschutz nicht mehr.

        Die spanische Fassung ist hier NACHGEBILDET und nicht abgerufen - die
        Spezifikation belegt nur die deutsche. Bezeugt wird deshalb nicht,
        wie fotocasa seine spanischen Pfade schreibt, sondern die Zusage, um
        die es geht: das Muster darf am Pfadanfang nicht haengen.
        """
        self.assertEqual(
            portal_und_id(
                "https://www.fotocasa.es/es/comprar/vivienda/marbella/"
                "aire-acondicionado-calefaccion-terraza-piscina/190346632/d"
            ),
            ("fotocasa", "190346632"),
        )

    def test_fotocasa_ohne_sprachpraefix_liefert_dieselbe_id(self):
        self.assertEqual(
            portal_und_id(
                "https://www.fotocasa.es/kaufen/wohnimmobilie/marbella/"
                "aire-acondicionado-heizung-terrasse-schwimmbad/190346632/d"
            ),
            ("fotocasa", "190346632"),
        )

    def test_eine_fotocasa_suchseite_ergibt_beide_werte_leer(self):
        """Eine Trefferliste ist kein Inserat.

        Sie endet ebenfalls auf einen einzelnen Buchstaben (`/l`), traegt
        davor aber keine Zahl - und faellt damit auf das leere Paar.

        GEMELDETE DUENNE STELLE: die Regel "einzelner Buchstabe am Ende wird
        uebersprungen" unterscheidet `/d` nicht von `/l`. Waere das vorletzte
        Segment einer Suchseite eine Zahl - etwa eine Postleitzahl -, bekaeme
        sie einen Schluessel, den sie nicht verdient. Belegt ist ein solcher
        Pfad nicht; die Regel kommt woertlich aus der Spezifikation und wurde
        deshalb nicht enger gefasst.
        """
        self.assertEqual(
            portal_und_id(
                "https://www.fotocasa.es/es/comprar/viviendas/marbella/todas-las-zonas/l"
            ),
            ("", ""),
        )

    # --- milanuncios ------------------------------------------------------

    def test_milanuncios_anzeige(self):
        """Die URL aus der Spezifikation, woertlich.

        Die ID ist die Zahl nach dem letzten Bindestrich vor `.htm`.

        RICHTIGGESTELLT am 02.09. Hier stand, ein nicht-gieriges Muster haette
        den ERSTEN Bindestrich genommen. Das ist falsch, und die
        Sabotage-Gegenprobe hat es aufgedeckt: der Anker `\.htm$` nagelt die
        Ziffern fest, gierig oder nicht. Was diese URL wirklich bewacht, ist
        nur, dass ueberhaupt das richtige Paar herauskommt - die schaerferen
        Zusagen tragen die beiden Zeugen darunter.
        """
        self.assertEqual(
            portal_und_id(
                "https://www.milanuncios.com/venta-de-apartamentos-en-san-pedro-"
                "de-alcantara-malaga/marbella-607639645.htm"
            ),
            ("milanuncios", "607639645"),
        )

    def test_milanuncios_haengt_nicht_am_pfadanfang(self):
        """Sprachunabhaengig, dieselbe Zusage wie bei fotocasa.

        milanuncios fuehrt kein Sprachpraefix - dieser Zeuge belegt deshalb
        nicht eine zweite Fassung desselben Inserats, sondern dass das Muster
        nur am Ende des Pfades greift. Faellt er, haengt die Erkennung an den
        Woertern davor, und die naechste Anzeigenform faellt still heraus.
        """
        self.assertEqual(
            portal_und_id(
                "https://www.milanuncios.com/de/irgendein-anderer-pfad/"
                "marbella-607639645.htm"
            ),
            ("milanuncios", "607639645"),
        )

    def test_milanuncios_nimmt_nicht_die_erste_zahl_im_pfad(self):
        """Eine Zahl weiter vorn im Pfad darf nicht gewinnen.

        Kleinanzeigenpfade tragen oft Merkmale mit Zahlen - Zimmerzahl,
        Flaeche, Baujahr. Eine Fassung, die einfach die erste Ziffernfolge im
        Pfad nimmt, liefert hier `3` und truege damit fuer JEDE
        Dreizimmerwohnung denselben Schluessel: der Dublettenschutz waere
        still tot. Die vier Zeugen darueber sehen das nicht, weil die URL aus
        der Spezifikation vor der ID gar keine Ziffern enthaelt.
        """
        self.assertEqual(
            portal_und_id(
                "https://www.milanuncios.com/venta-de-pisos-3-dormitorios-en-marbella/"
                "marbella-607639645.htm"
            ),
            ("milanuncios", "607639645"),
        )

    def test_eine_milanuncios_url_ohne_htm_ergibt_beide_werte_leer(self):
        """Die duenne Stelle, ausdruecklich bezeugt statt verschwiegen.

        Das Muster haengt an einem einzigen Beleg. Kleinanzeigenportale
        fuehren oft mehrere Anzeigentypen mit abweichenden Pfaden. Passt eine
        URL nicht, faellt sie auf "sonstiges" - das ist der richtige Ausgang,
        kein Fehler, und dieser Zeuge haelt genau das fest.
        """
        self.assertEqual(
            portal_und_id("https://www.milanuncios.com/anuncios/marbella-607639645"),
            ("", ""),
        )

    # --- pisos ------------------------------------------------------------

    def test_pisos_expose(self):
        """Die erste URL aus der Spezifikation, woertlich.

        Die ID ist der VOLLSTAENDIGE Block aus zwei durch Unterstrich
        getrennten Zahlen.
        """
        self.assertEqual(
            portal_und_id(
                "https://www.pisos.com/comprar/"
                "atico-cabopino_reserva_de_marbella-65035296319_108900/"
            ),
            ("pisos", "65035296319_108900"),
        )

    def test_pisos_promotion(self):
        """Die zweite URL aus der Spezifikation, woertlich - anderer Pfadaufbau."""
        self.assertEqual(
            portal_und_id("https://www.pisos.com/promocion-los_pacos-6109286238_109700/"),
            ("pisos", "6109286238_109700"),
        )

    def test_pisos_nimmt_nicht_nur_die_agenturkennung(self):
        """Der wichtigste der drei pisos-Zeugen.

        Die zweite Zahl ist in beiden Belegen sechsstellig und beginnt mit
        `10` - vermutlich eine Makler- oder Agenturkennung. Naehme das Muster
        nur sie, truegen ALLE Objekte desselben Maklers denselben Schluessel,
        und der Dublettenschutz waere still tot: jedes zweite Inserat dieses
        Maklers liefe als Dublette des ersten auf. Genau dieser Fehler waere
        unsichtbar, deshalb ein eigener Zeuge dagegen.
        """
        _, inserats_id = portal_und_id(
            "https://www.pisos.com/promocion-los_pacos-6109286238_109700/"
        )
        self.assertNotEqual(inserats_id, "109700")

    def test_pisos_nimmt_nicht_nur_die_erste_zahl(self):
        _, inserats_id = portal_und_id(
            "https://www.pisos.com/promocion-los_pacos-6109286238_109700/"
        )
        self.assertNotEqual(inserats_id, "6109286238")

    def test_pisos_sprachvariante_liefert_dieselbe_id(self):
        """Wie bei fotocasa nachgebildet: das Muster darf am Pfadanfang nicht haengen."""
        self.assertEqual(
            portal_und_id(
                "https://www.pisos.com/de/kaufen/"
                "atico-cabopino_reserva_de_marbella-65035296319_108900/"
            ),
            ("pisos", "65035296319_108900"),
        )

    def test_idealista_auf_einer_subdomain(self):
        self.assertEqual(
            portal_und_id("https://m.idealista.com/inmueble/12345/"),
            ("idealista", "12345"),
        )

    # --- ImmoScout24 (Zusage 2) -------------------------------------------

    def test_immoscout24_expose(self):
        self.assertEqual(
            portal_und_id("https://www.immobilienscout24.de/expose/98765"),
            ("immoscout24", "98765"),
        )

    def test_immoscout24_mit_fragment(self):
        self.assertEqual(
            portal_und_id("https://www.immobilienscout24.de/expose/98765#/"),
            ("immoscout24", "98765"),
        )

    def test_immoscout24_mit_angehaengtem_unterpfad(self):
        # Der Pfad wird bewusst nicht bis zum Ende geprueft.
        self.assertEqual(
            portal_und_id(
                "https://www.immobilienscout24.de/expose/98765/karte/umgebung"
            ),
            ("immoscout24", "98765"),
        )

    def test_immoscout24_auf_einer_subdomain(self):
        self.assertEqual(
            portal_und_id("https://www.sandbox.immobilienscout24.de/expose/98765"),
            ("immoscout24", "98765"),
        )

    # --- das leere Paar (Zusage 3) ----------------------------------------

    def test_eine_unbekannte_domain_ergibt_beide_werte_leer(self):
        self.assertEqual(portal_und_id("https://beispiel.de/inmueble/12345/"), ("", ""))

    def test_ein_passender_pfad_auf_fremder_domain_ergibt_beide_werte_leer(self):
        # `endswith` allein traefe hier zu - deshalb der Punkt in `_passt()`.
        self.assertEqual(
            portal_und_id("https://nichtidealista.com/inmueble/12345/"), ("", "")
        )

    def test_eine_fehlende_ziffernfolge_ergibt_beide_werte_leer(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.com/inmueble/abc/"), ("", "")
        )

    def test_ein_fremder_pfad_auf_richtiger_domain_ergibt_beide_werte_leer(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.com/geo/venta-viviendas/madrid/"),
            ("", ""),
        )

    def test_immoscout24_ohne_expose_ergibt_beide_werte_leer(self):
        self.assertEqual(
            portal_und_id("https://www.immobilienscout24.de/suche/de/wohnung-kaufen"),
            ("", ""),
        )

    def test_eine_andere_laenderdomain_von_immoscout24_wird_nicht_erraten(self):
        # `immobilienscout24.at` ist nicht bezeugt - und ein geratenes Paar ist
        # schaedlicher als ein leeres.
        self.assertEqual(
            portal_und_id("https://www.immobilienscout24.at/expose/98765"), ("", "")
        )

    def test_eine_eingabe_ohne_host_ergibt_beide_werte_leer(self):
        self.assertEqual(portal_und_id("kein-link"), ("", ""))

    def test_eine_leere_eingabe_ergibt_beide_werte_leer(self):
        self.assertEqual(portal_und_id(""), ("", ""))

    def test_eine_kaputte_ipv6_klammer_wirft_nicht(self):
        # `urlsplit` wirft hier `ValueError`. In einer Datenmigration waere das
        # ein Abbruch mitten im Lauf.
        self.assertEqual(portal_und_id("https://[kaputt/inmueble/1/"), ("", ""))

    def test_zugangsdaten_im_host_taeuschen_die_domain_nicht_vor(self):
        # `https://www.idealista.com@boese.de/...` - alles vor dem `@` ist
        # Benutzername. Wer den Host aus dem rohen `netloc` liest, faellt darauf
        # herein und schreibt einem fremden Server das Portal `idealista` zu.
        self.assertEqual(
            portal_und_id("https://www.idealista.com@beispiel.de/inmueble/12345/"),
            ("", ""),
        )

    def test_ein_port_hinter_dem_host_stoert_nicht(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.com:443/inmueble/12345/"),
            ("idealista", "12345"),
        )

    def test_ein_host_in_grossbuchstaben_wird_erkannt(self):
        self.assertEqual(
            portal_und_id("https://WWW.IDEALISTA.COM/inmueble/12345/"),
            ("idealista", "12345"),
        )


class PortalModulTests(TestCase):
    """Die Zusagen ueber das Modul selbst: rein, und passend zu `choices.Portal`."""

    def test_das_modul_importiert_django_nicht(self):
        """Sonst holt sich die Datenmigration die halbe App in ihren Zustand.

        Ueber den Syntaxbaum, nicht ueber `sys.modules`: `tests.py` hat Django
        laengst importiert, eine Pruefung auf "ist geladen" waere hier immer rot
        und anderswo immer gruen.
        """
        with open(portale.__file__, encoding="utf-8") as datei:
            baum = ast.parse(datei.read())
        namen = []
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Import):
                namen += [a.name for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom):
                namen.append(knoten.module or "")
        self.assertEqual([n for n in namen if n.split(".")[0] == "django"], [])

    def test_die_funktion_fragt_die_datenbank_nicht(self):
        with self.assertNumQueries(0):
            portal_und_id("https://www.idealista.com/inmueble/12345/")

    def test_der_idealista_schluessel_passt_zu_den_auswahllisten(self):
        """Riegel gegen eine stille Umbenennung in `choices.py`.

        `portale.py` darf Django nicht importieren und traegt den Schluessel
        deshalb als nackte Zeichenkette. Ohne diesen Zeugen schriebe die View
        nach einer Umbenennung weiter den alten Wert - und nichts wuerde rot.
        """
        self.assertEqual(portale.PORTAL_IDEALISTA, Portal.IDEALISTA.value)

    def test_der_immoscout24_schluessel_passt_zu_den_auswahllisten(self):
        self.assertEqual(portale.PORTAL_IMMOSCOUT24, Portal.IMMOSCOUT24.value)

    def test_der_fotocasa_schluessel_passt_zu_den_auswahllisten(self):
        self.assertEqual(portale.PORTAL_FOTOCASA, Portal.FOTOCASA.value)

    def test_der_milanuncios_schluessel_passt_zu_den_auswahllisten(self):
        self.assertEqual(portale.PORTAL_MILANUNCIOS, Portal.MILANUNCIOS.value)

    def test_der_pisos_schluessel_passt_zu_den_auswahllisten(self):
        self.assertEqual(portale.PORTAL_PISOS, Portal.PISOS.value)

    def test_die_portaltabelle_ist_nicht_leer(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darunter.

        Waere `PORTALE` leer, liefe die Schleife im naechsten Zeugen ueber
        nichts und er bliebe gruen - waehrend die Erkennung gar nicht mehr
        arbeitet.
        """
        self.assertNotEqual(portale.PORTALE, ())

    def test_jeder_schluessel_der_portaltabelle_steht_in_den_auswahllisten(self):
        """Deckt auch das Portal ab, das erst noch dazukommt.

        Die fuenf Einzelzeugen darueber sind ausgeschrieben und benennen ihre
        Zusage - aber sie sagen nichts ueber eine SECHSTE Zeile, die jemand
        spaeter in `PORTALE` eintraegt und in `choices.py` vergisst. Dann
        schriebe die View einen Schluessel, den keine Auswahlliste kennt: das
        Feld bliebe ohne Beschriftung und der Filter fuende das Objekt nie.
        """
        for portal, _, _ in portale.PORTALE:
            with self.subTest(portal=portal):
                self.assertIn(portal, Portal.values)


class SchnellerfassungMitSchluesselTests(TestCase):
    """Zusagen 4 bis 7: der Dublettenschutz haengt nicht mehr an der URL allein."""

    #: Dasselbe Inserat in zwei Schreibweisen. Sie unterscheiden sich absichtlich
    #: so, dass `rstrip("/")` sie NICHT zusammenfuehrt - sonst machte schon der
    #: alte URL-Vergleich Zusage 5 gruen und der starke Vergleich bliebe
    #: unbezeugt. Bewacht wird das von
    #: `test_der_alte_url_vergleich_fuehrt_die_beiden_schreibweisen_nicht_zusammen`.
    ERSTE = "https://www.idealista.com/en/inmueble/12345/"
    ZWEITE = "https://www.idealista.com/inmueble/12345?utm_source=newsletter"

    def setUp(self):
        self.person = Person.objects.create_user(
            "steffen", password="ein-langes-passwort", first_name="Steffen", last_name="P."
        )
        self.client.force_login(self.person)

    def _einwerfen(self, url, **kwargs):
        return self.client.post("/einwerfen/", {"url": url}, **kwargs)

    # --- Zusage 4: der Einwurf schreibt beide Werte -----------------------

    def test_der_einwurf_schreibt_das_portal_ans_objekt(self):
        self._einwerfen(self.ERSTE)
        self.assertEqual(Objekt.objects.get().portal, Portal.IDEALISTA)

    def test_der_einwurf_schreibt_die_inserats_id_ans_objekt(self):
        self._einwerfen(self.ERSTE)
        self.assertEqual(Objekt.objects.get().inserats_id, "12345")

    def test_der_einwurf_schreibt_auch_fuer_immoscout24(self):
        self._einwerfen("https://www.immobilienscout24.de/expose/98765#/")
        self.assertEqual(
            (Objekt.objects.get().portal, Objekt.objects.get().inserats_id),
            (Portal.IMMOSCOUT24, "98765"),
        )

    def test_die_url_bleibt_dabei_unveraendert(self):
        # Portal und ID werden aus der URL GELESEN, sie wird nicht nach ihnen
        # umgeschrieben.
        self._einwerfen(self.ZWEITE)
        self.assertEqual(Objekt.objects.get().url, self.ZWEITE)

    # --- Zusage 5: der eigentliche Zweck der Runde ------------------------

    def test_zwei_schreibweisen_desselben_inserats_legen_ein_objekt_an(self):
        self._einwerfen(self.ERSTE)
        self._einwerfen(self.ZWEITE)
        self.assertEqual(Objekt.objects.count(), 1)

    def test_die_zweite_schreibweise_leitet_auf_das_bestehende_objekt(self):
        self._einwerfen(self.ERSTE)
        bestehendes = Objekt.objects.get()
        self.assertEqual(
            self._einwerfen(self.ZWEITE)["Location"], f"/objekt/{bestehendes.pk}/"
        )

    def test_die_zweite_schreibweise_meldet_die_dublette(self):
        self._einwerfen(self.ERSTE)
        self.assertContains(
            self._einwerfen(self.ZWEITE, follow=True),
            "Das Inserat liegt schon in der Liste.",
        )

    def test_die_zweite_schreibweise_versucht_gar_kein_insert(self):
        """Stufe 1 laeuft VOR dem Insert - sonst ist die Vorpruefung ueberfluessig.

        Der eigentliche Zeuge fuer Zusage 5. Die drei darueber sind es NICHT:
        gemessen am 2026-08-29 blieben sie alle drei gruen, als die
        Vorpruefung testweise entfernt wurde. Ohne sie schlaegt der Einwurf am
        Unique-Index auf, der `except`-Zweig faengt ihn, und die Antwort nach
        aussen ist Zeichen fuer Zeichen dieselbe - ein Objekt, dieselbe
        Umleitung, dieselbe Meldung. Sie bezeugen damit den Unique-Index, nicht
        die Vorpruefung.

        Sichtbar ist der Unterschied nur an der Abfrage. Gezaehlt wird auf die
        Tabelle, nicht auf "irgendein INSERT": Sitzung und Meldungen schreiben
        im selben Request mit.
        """
        self._einwerfen(self.ERSTE)
        with CaptureQueriesContext(connection) as abfragen:
            self._einwerfen(self.ZWEITE)
        versuche = [
            q["sql"]
            for q in abfragen.captured_queries
            if q["sql"].lstrip().lower().startswith('insert into "objekte_objekt"')
        ]
        self.assertEqual(versuche, [])

    def test_der_alte_url_vergleich_fuehrt_die_beiden_schreibweisen_nicht_zusammen(self):
        """Die Gegenprobe zu Zusage 5, als Zeuge statt als Handgriff.

        Ohne ihn koennte jemand die beiden Konstanten oben irgendwann auf zwei
        URLs setzen, die sich nur im abschliessenden Schraegstrich
        unterscheiden. Die drei Zeugen darueber blieben gruen - und bezeugten
        von da an den alten URL-Vergleich statt den neuen Schluessel.
        """
        Objekt.objects.create(url=self.ERSTE)
        self.assertIsNone(views.dublette(self.ZWEITE))

    def test_der_starke_vergleich_findet_das_bestehende_objekt(self):
        # Die andere Haelfte derselben Gegenprobe: es ist wirklich Stufe 1, die
        # den Treffer liefert.
        bestehendes = Objekt.objects.create(
            url=self.ERSTE, portal=Portal.IDEALISTA, inserats_id="12345"
        )
        self.assertEqual(
            views.dublette_ueber_schluessel(Portal.IDEALISTA, "12345"), bestehendes
        )

    def test_ein_verworfenes_objekt_wird_auch_ueber_den_schluessel_gefunden(self):
        self._einwerfen(self.ERSTE)
        Objekt.objects.get().status_setzen(self.person, Status.RAUS)
        self._einwerfen(self.ZWEITE)
        self.assertEqual(Objekt.objects.count(), 1)

    # --- Zusage 6: Portale ohne Muster tragen weiter ----------------------

    def test_eine_domain_ohne_muster_legt_weiterhin_an(self):
        self._einwerfen("https://beispiel.de/inserat/1")
        self.assertEqual(Objekt.objects.count(), 1)

    def test_eine_domain_ohne_muster_bleibt_ohne_portal(self):
        self._einwerfen("https://beispiel.de/inserat/1")
        self.assertEqual(Objekt.objects.get().portal, "")

    def test_eine_domain_ohne_muster_bleibt_ohne_inserats_id(self):
        self._einwerfen("https://beispiel.de/inserat/1")
        self.assertEqual(Objekt.objects.get().inserats_id, "")

    def test_ohne_muster_traegt_weiter_der_url_vergleich(self):
        self._einwerfen("https://beispiel.de/inserat/1")
        self._einwerfen("https://beispiel.de/inserat/1/")
        self.assertEqual(Objekt.objects.count(), 1)

    def test_zwei_unerkannte_inserate_sind_keine_dublette(self):
        """Der Riegel in `dublette_ueber_schluessel`: `("", "")` ist kein Schluessel.

        Ohne die Leerpruefung faende Stufe 1 fuer JEDES unerkannte Inserat das
        erstbeste andere unerkannte - und leitete den Einwurf auf ein voellig
        fremdes Objekt um. Das ist der teuerste denkbare Fehler dieser Runde,
        weil er wie eine erkannte Dublette aussieht.
        """
        self._einwerfen("https://beispiel.de/inserat/1")
        self._einwerfen("https://beispiel.de/inserat/2")
        self.assertEqual(Objekt.objects.count(), 2)

    # --- Zusage 7: der Wettlauf -------------------------------------------

    def _wettlauf(self):
        """Stellt den Wettlauf nach: die Pruefung sieht nichts, der Insert schon.

        Genau die Luecke zwischen Pruefung und Insert, in die ein zweiter
        Einwurf faellt. Blind gemacht wird nur der ERSTE Blick - der zweite, den
        der `except`-Zweig tut, laeuft echt gegen die Datenbank. Waere auch er
        gemockt, bezeugte der Test nur noch den Mock.
        """
        echt = views.dublette_ueber_schluessel
        blicke = []

        def blind(*args, **kwargs):
            blicke.append(None)
            return None if len(blicke) == 1 else echt(*args, **kwargs)

        return mock.patch.object(views, "dublette_ueber_schluessel", blind)

    def _bestehendes_anlegen(self):
        return Objekt.objects.create(
            url=self.ERSTE, portal=Portal.IDEALISTA, inserats_id="12345"
        )

    def test_ein_wettlauf_ist_kein_serverfehler(self):
        self._bestehendes_anlegen()
        with self._wettlauf():
            self.assertEqual(self._einwerfen(self.ZWEITE).status_code, 302)

    def test_ein_wettlauf_leitet_auf_das_bestehende_objekt(self):
        bestehendes = self._bestehendes_anlegen()
        with self._wettlauf():
            self.assertEqual(
                self._einwerfen(self.ZWEITE)["Location"], f"/objekt/{bestehendes.pk}/"
            )

    def test_ein_wettlauf_legt_kein_zweites_objekt_an(self):
        self._bestehendes_anlegen()
        with self._wettlauf():
            self._einwerfen(self.ZWEITE)
        self.assertEqual(Objekt.objects.count(), 1)

    def test_ein_wettlauf_sieht_aus_wie_eine_erkannte_dublette(self):
        self._bestehendes_anlegen()
        with self._wettlauf():
            antwort = self._einwerfen(self.ZWEITE, follow=True)
        self.assertContains(antwort, "Das Inserat liegt schon in der Liste.")

    def test_nach_dem_wettlauf_ist_die_verbindung_weiter_benutzbar(self):
        """Der Grund fuer das enge `transaction.atomic()` um den Insert.

        Ohne den eigenen Transaktionsrahmen macht ein gefangener
        `IntegrityError` jede folgende Abfrage unbrauchbar - und der
        `except`-Zweig fragt die Datenbank noch einmal. Der Zeuge laeuft
        deshalb ueber `follow=True`: die Folgeseite rendert die ganze Liste.
        """
        self._bestehendes_anlegen()
        with self._wettlauf():
            antwort = self._einwerfen(self.ZWEITE, follow=True)
        self.assertEqual(antwort.status_code, 200)

    def test_ein_integrityerror_ohne_treffer_meldet_sich_als_fehler(self):
        """Kein stilles Verschlucken: hier ist wirklich nichts gespeichert worden."""
        with mock.patch.object(Objekt.objects, "create", side_effect=IntegrityError):
            antwort = self._einwerfen(self.ERSTE, follow=True)
        self.assertContains(antwort, "Das Inserat konnte nicht angelegt werden.")

    def test_ein_integrityerror_ohne_treffer_legt_nichts_an(self):
        with mock.patch.object(Objekt.objects, "create", side_effect=IntegrityError):
            self._einwerfen(self.ERSTE)
        self.assertEqual(Objekt.objects.count(), 0)

    def test_ein_integrityerror_ohne_treffer_meldet_keinen_erfolg(self):
        with mock.patch.object(Objekt.objects, "create", side_effect=IntegrityError):
            antwort = self._einwerfen(self.ERSTE, follow=True)
        self.assertNotContains(antwort, "Objekt angelegt.")

    def test_ein_integrityerror_ohne_treffer_fuehrt_auf_die_liste(self):
        with mock.patch.object(Objekt.objects, "create", side_effect=IntegrityError):
            self.assertEqual(self._einwerfen(self.ERSTE)["Location"], "/")


class NachtragsmigrationTests(TestCase):
    """Zusagen 8 und 9: der Nachtrag an Bestandsobjekten.

    Gerechnet wird gegen den HISTORISCHEN Modellzustand aus dem
    Migrations-Loader, nicht gegen `objekte.models.Objekt`: eine Migration, die
    nur gegen das heutige Modell bezeugt ist, bleibt gruen, bis das Modell sich
    bewegt - und faellt dann an einer Stelle, die mit ihr nichts zu tun hat.

    Ein Schemawechsel haengt nicht daran: 0003 ist reine Daten, das Schema bei
    0002 und bei 0003 ist dasselbe, und die Test-Datenbank steht ohnehin schon
    auf dem Endstand.
    """

    def setUp(self):
        self.alte_apps = (
            MigrationExecutor(connection)
            .loader.project_state(("objekte", "0002_alter_objekt_options"))
            .apps
        )

    def _nachtragen(self):
        nachtragsmigration.nachtragen(self.alte_apps, None)

    # --- die Verdrahtung: laeuft diese Funktion ueberhaupt? ---------------

    def test_die_migration_fuehrt_genau_diese_funktion_aus(self):
        """Ohne diesen Zeugen sind alle folgenden blind.

        Sie rufen `nachtragen()` direkt auf. Waere die Funktion nicht in den
        Operationen verdrahtet, liefe die Migration im Betrieb nichts - und die
        Zeugen unten waeren trotzdem gruen.
        """
        (operation,) = nachtragsmigration.Migration.operations
        self.assertIs(operation.code, nachtragsmigration.nachtragen)

    def test_die_migration_ist_rueckwaerts_ein_noop(self):
        (operation,) = nachtragsmigration.Migration.operations
        self.assertIs(operation.reverse_code, migrations.RunPython.noop)

    # --- Zusage 8 ---------------------------------------------------------

    def test_traegt_portal_und_id_an_einem_bestandsobjekt_nach(self):
        bestand = Objekt.objects.create(url="https://www.idealista.com/en/inmueble/54321/")
        self._nachtragen()
        bestand.refresh_from_db()
        self.assertEqual(
            (bestand.portal, bestand.inserats_id), (Portal.IDEALISTA, "54321")
        )

    def test_ein_objekt_ohne_erkennbares_muster_bleibt_leer(self):
        bestand = Objekt.objects.create(url="https://beispiel.de/inserat/1")
        self._nachtragen()
        bestand.refresh_from_db()
        self.assertEqual((bestand.portal, bestand.inserats_id), ("", ""))

    def test_der_nachtrag_ruehrt_die_url_nicht_an(self):
        bestand = Objekt.objects.create(url="https://www.idealista.com/en/inmueble/54321/")
        self._nachtragen()
        bestand.refresh_from_db()
        self.assertEqual(bestand.url, "https://www.idealista.com/en/inmueble/54321/")

    def test_der_nachtrag_schreibt_zuletzt_geaendert_am_nicht_fort(self):
        # Ein Nachtrag ist keine Aenderung, die jemand vorgenommen hat.
        bestand = Objekt.objects.create(url="https://www.idealista.com/inmueble/54321/")
        vorher = Objekt.objects.values_list("zuletzt_geaendert_am", flat=True).get()
        self._nachtragen()
        self.assertEqual(
            Objekt.objects.values_list("zuletzt_geaendert_am", flat=True).get(), vorher
        )

    def test_ein_zweiter_lauf_aendert_nichts_mehr(self):
        Objekt.objects.create(url="https://www.idealista.com/inmueble/54321/")
        self._nachtragen()
        self._nachtragen()
        self.assertEqual(Objekt.objects.get().inserats_id, "54321")

    # --- Zusage 9 ---------------------------------------------------------

    def test_ein_bereits_vergebenes_paar_laesst_das_objekt_unangetastet(self):
        Objekt.objects.create(
            url="https://www.idealista.com/inmueble/54321/",
            portal=Portal.IDEALISTA,
            inserats_id="54321",
        )
        nachzuegler = Objekt.objects.create(
            url="https://www.idealista.com/en/inmueble/54321/"
        )
        self._nachtragen()
        nachzuegler.refresh_from_db()
        self.assertEqual((nachzuegler.portal, nachzuegler.inserats_id), ("", ""))

    def test_zwei_bestandsobjekte_auf_dasselbe_inserat_brechen_den_lauf_nicht_ab(self):
        """Beide leer, beide zeigen auf dasselbe Inserat - der Fall aus dem Altbestand.

        Ohne die Menge `vergeben` schlaegt der zweite Schreiber am Unique-Index
        auf und die Migration bricht mitten im Lauf ab.
        """
        Objekt.objects.create(url="https://www.idealista.com/inmueble/54321/")
        Objekt.objects.create(url="https://www.idealista.com/en/inmueble/54321/?x=1")
        self._nachtragen()
        self.assertEqual(Objekt.objects.exclude(portal="").count(), 1)

    def test_bei_zwei_altdubletten_bekommt_das_aeltere_den_schluessel(self):
        """Die Spezifikation laesst offen, welches der beiden gewinnt.

        Entschieden wie ueberall sonst in der Liste: das aeltere ist das
        Original. Bekaeme das juengere den Schluessel, leitete jeder kuenftige
        Einwurf dorthin - und die Vota und Notizen am aelteren faenden sich
        nicht mehr. Ohne `order_by` im Lauf entscheidet das die Datenbank.
        """
        aelteres = Objekt.objects.create(url="https://www.idealista.com/inmueble/54321/")
        Objekt.objects.create(url="https://www.idealista.com/en/inmueble/54321/")
        self._nachtragen()
        aelteres.refresh_from_db()
        self.assertEqual(aelteres.inserats_id, "54321")

    def test_die_reihenfolge_steht_in_der_abfrage(self):
        """Strukturtest neben dem Verhaltenstest darueber.

        Der Verhaltenstest kann blind-gruen werden: eine frisch gefuellte
        Tabelle liefert Postgres meist in Einfuegereihenfolge zurueck - also
        genau in der gewuenschten, auch ohne `order_by`. Geprueft wird deshalb
        die FOLGE der Spalten in der kompilierten Abfrage. Auf blosse
        Anwesenheit eines ORDER BY zu pruefen genuegt nicht: fremde Abfragen
        auf dieselbe Tabelle bringen ihr eigenes mit.

        Und die RICHTUNG gehoert dazu, nicht nur die Folge. Gemessen am
        2026-08-29: eine erste Fassung dieses Zeugen prueste nur, dass
        `eingestellt_am` vor `id` steht - und blieb unter BEIDEN Sabotagen
        gruen. `Objekt.Meta.ordering` ist `["-eingestellt_am", "-id"]`, also
        dieselben zwei Spalten in derselben Folge, nur absteigend. Genau die
        falsche Reihenfolge haette der Zeuge durchgewinkt.
        """
        Objekt.objects.create(url="https://www.idealista.com/inmueble/54321/")
        with CaptureQueriesContext(connection) as abfragen:
            self._nachtragen()
        sortierte = [
            q["sql"].lower()
            for q in abfragen.captured_queries
            if 'from "objekte_objekt"' in q["sql"].lower()
            and "order by" in q["sql"].lower()
        ]
        self.assertTrue(sortierte, "keine sortierte Abfrage auf objekte_objekt")
        sql = sortierte[-1]
        ab = sql.index("order by")
        self.assertLess(
            sql.index('"eingestellt_am" asc', ab), sql.index('"id" asc', ab)
        )

    def test_das_zweite_objekt_bleibt_dabei_erhalten(self):
        Objekt.objects.create(url="https://www.idealista.com/inmueble/54321/")
        Objekt.objects.create(url="https://www.idealista.com/en/inmueble/54321/?x=1")
        self._nachtragen()
        self.assertEqual(Objekt.objects.count(), 2)


class BestandsnachtragNeuePortaleTests(TestCase):
    """Der Nachtrag vom 02.09.: Bestandsobjekte an den drei neuen Portalen.

    Gerechnet wird gegen den historischen Modellzustand aus dem
    Migrations-Loader, aus demselben Grund wie bei `NachtragsmigrationTests`:
    eine Migration, die nur gegen das heutige Modell bezeugt ist, bleibt gruen,
    bis das Modell sich bewegt.

    Der Zustand ist der von 0004 - das ist der Stand, auf dem 0005 laeuft, und
    der erste, auf dem `Portal` die drei neuen Schluessel ueberhaupt kennt.
    """

    def setUp(self):
        self.alte_apps = (
            MigrationExecutor(connection)
            .loader.project_state(("objekte", "0004_alter_objekt_portal"))
            .apps
        )

    def _nachtragen(self):
        zweiter_nachtrag.Migration.operations[0].code(self.alte_apps, None)

    # --- die Verdrahtung --------------------------------------------------

    def test_die_migration_fuehrt_die_funktion_aus_0003_aus(self):
        """Ohne diesen Zeugen sind alle folgenden blind - und er sagt noch mehr.

        Er haelt fest, dass 0005 die Funktion aus 0003 AUSFUEHRT und nicht
        nachbaut. Genau daran haengt die Kollisionsregel: das aeltere Objekt
        bekommt den Schluessel. Ein Nachbau koennte davon abweichen, ohne dass
        es jemandem auffiele - und dann liefe jeder kuenftige Einwurf auf das
        juengere Objekt, waehrend Vota und Notizen am aelteren haengen.
        """
        (operation,) = zweiter_nachtrag.Migration.operations
        self.assertIs(operation.code, nachtragsmigration.nachtragen)

    def test_die_migration_ist_rueckwaerts_ein_noop(self):
        (operation,) = zweiter_nachtrag.Migration.operations
        self.assertIs(operation.reverse_code, migrations.RunPython.noop)

    def test_sie_haengt_an_der_schemamigration_der_auswahlliste(self):
        """Die Reihenfolge ist nicht beliebig.

        Liefe der Nachtrag VOR 0004, schriebe er Portalwerte in eine Spalte,
        deren Auswahlliste sie noch nicht kennt.
        """
        self.assertIn(("objekte", "0004_alter_objekt_portal"), zweiter_nachtrag.Migration.dependencies)

    # --- die drei neuen Portale im Bestand --------------------------------

    def test_traegt_fotocasa_an_einem_bestandsobjekt_nach(self):
        bestand = Objekt.objects.create(
            url="https://www.fotocasa.es/de/kaufen/wohnimmobilie/marbella/"
            "aire-acondicionado-heizung/190346632/d"
        )
        self._nachtragen()
        bestand.refresh_from_db()
        self.assertEqual(
            (bestand.portal, bestand.inserats_id), (Portal.FOTOCASA, "190346632")
        )

    def test_traegt_milanuncios_an_einem_bestandsobjekt_nach(self):
        bestand = Objekt.objects.create(
            url="https://www.milanuncios.com/venta-de-apartamentos-en-san-pedro/"
            "marbella-607639645.htm"
        )
        self._nachtragen()
        bestand.refresh_from_db()
        self.assertEqual(
            (bestand.portal, bestand.inserats_id), (Portal.MILANUNCIOS, "607639645")
        )

    def test_traegt_pisos_an_einem_bestandsobjekt_nach(self):
        bestand = Objekt.objects.create(
            url="https://www.pisos.com/promocion-los_pacos-6109286238_109700/"
        )
        self._nachtragen()
        bestand.refresh_from_db()
        self.assertEqual(
            (bestand.portal, bestand.inserats_id), (Portal.PISOS, "6109286238_109700")
        )

    def test_das_immowelt_testobjekt_bleibt_ohne_schluessel(self):
        """Gewollt: es bleibt in der Liste und bleibt "sonstiges".

        Immowelt hat kein bekanntes Muster. Ein geratener Schluessel liesse
        zwei verschiedene Inserate am Unique-Index kollidieren - ein leerer
        laesst den URL-Vergleich weiterarbeiten.
        """
        bestand = Objekt.objects.create(
            url="https://www.immowelt.de/expose/88b946d7-1f96-43d4-925d-4c7ded15b6cb"
            "?serp_view=list"
        )
        self._nachtragen()
        bestand.refresh_from_db()
        self.assertEqual((bestand.portal, bestand.inserats_id), ("", ""))

    def test_das_immowelt_testobjekt_bleibt_in_der_datenbank(self):
        Objekt.objects.create(url="https://www.immowelt.de/expose/88b946d7?x=1")
        self._nachtragen()
        self.assertEqual(Objekt.objects.count(), 1)

    # --- die Kollisionsregel aus der Migration vom 29.08. ------------------

    def test_bei_zwei_bestandsobjekten_auf_dasselbe_inserat_gewinnt_das_aeltere(self):
        """Dieselbe Regel wie am 29.08., und zwar durch DIESELBE Funktion.

        Zwei Schreibweisen desselben fotocasa-Inserats. Bekaeme das juengere
        den Schluessel, liefe jeder kuenftige Einwurf dorthin - und die Vota
        und Notizen am aelteren faenden sich nicht mehr.
        """
        aelteres = Objekt.objects.create(
            url="https://www.fotocasa.es/de/kaufen/wohnimmobilie/marbella/a/190346632/d"
        )
        juengeres = Objekt.objects.create(
            url="https://www.fotocasa.es/es/comprar/vivienda/marbella/b/190346632/d"
        )
        self._nachtragen()
        aelteres.refresh_from_db()
        juengeres.refresh_from_db()
        self.assertEqual(aelteres.inserats_id, "190346632")
        self.assertEqual(juengeres.inserats_id, "")

    def test_zwei_bestandsobjekte_auf_dasselbe_inserat_brechen_den_lauf_nicht_ab(self):
        Objekt.objects.create(
            url="https://www.pisos.com/comprar/atico-a-65035296319_108900/"
        )
        Objekt.objects.create(
            url="https://www.pisos.com/de/kaufen/atico-b-65035296319_108900/"
        )
        self._nachtragen()
        self.assertEqual(Objekt.objects.count(), 2)
        self.assertEqual(Objekt.objects.exclude(portal="").count(), 1)

    def test_ein_zweiter_lauf_aendert_nichts_mehr(self):
        Objekt.objects.create(
            url="https://www.pisos.com/promocion-los_pacos-6109286238_109700/"
        )
        self._nachtragen()
        self._nachtragen()
        self.assertEqual(Objekt.objects.get().inserats_id, "6109286238_109700")

    def test_ein_bereits_vergebener_schluessel_bleibt_stehen(self):
        """Was 0003 vergeben hat, schreibt 0005 nicht um.

        Der Fall aus der Praxis: das Objekt wurde nach dem 29.08. eingeworfen
        und hat seinen Schluessel schon vom Einwurf. Ein zweiter Nachtrag darf
        ihn nicht anfassen.
        """
        bestand = Objekt.objects.create(
            url="https://www.idealista.com/inmueble/54321/",
            portal=Portal.IDEALISTA,
            inserats_id="54321",
        )
        self._nachtragen()
        bestand.refresh_from_db()
        self.assertEqual(
            (bestand.portal, bestand.inserats_id), (Portal.IDEALISTA, "54321")
        )

    def test_der_nachtrag_ruehrt_die_url_nicht_an(self):
        url = "https://www.pisos.com/promocion-los_pacos-6109286238_109700/"
        Objekt.objects.create(url=url)
        self._nachtragen()
        self.assertEqual(Objekt.objects.get().url, url)

    def test_der_nachtrag_schreibt_zuletzt_geaendert_am_nicht_fort(self):
        Objekt.objects.create(
            url="https://www.pisos.com/promocion-los_pacos-6109286238_109700/"
        )
        vorher = Objekt.objects.values_list("zuletzt_geaendert_am", flat=True).get()
        self._nachtragen()
        self.assertEqual(
            Objekt.objects.values_list("zuletzt_geaendert_am", flat=True).get(), vorher
        )


# Der `SpaltenParser` stand hier bis zum 04.09. Er las Spaltenkoepfe und
# `data-spalte` je Zeile aus der Objektliste. Beides gibt es nicht mehr: die
# Liste ist keine Tabelle mehr, sondern eine `<ul>` aus Objektzeilen. Ein
# Parser ohne zu parsende Struktur ist kein Zeuge, sondern ein Rest.


class StylesheetTests(TestCase):
    """Zusagen 10 bis 12. Teil B ist der Teil, den kein Test wirklich abnimmt -
    diese drei sind das, was sich ueberhaupt bezeugen laesst. Wie die Seite
    aussieht, entscheidet der Blick auf den Bildschirm."""

    def setUp(self):
        self.person = Person.objects.create_user(
            "steffen", password="ein-langes-passwort", first_name="Steffen", last_name="P."
        )
        self.client.force_login(self.person)

    def _seite(self):
        return self.client.get("/")

    # --- Zusage 10 --------------------------------------------------------

    def test_das_stylesheet_ist_ueber_die_static_konfiguration_auffindbar(self):
        """Der eigentliche Zeuge fuer Zusage 10.

        Eine Pruefung auf die Zeichenkette im Template bliebe gruen, wenn
        `STATICFILES_DIRS` wieder verschwaende: `{% static %}` baut die URL
        auch dann noch zusammen. Die Datei waere nur nicht mehr auffindbar,
        die Seite bliebe stumm unformatiert - genau der Fehler, den die
        Spezifikation unter B1 beschreibt.
        """
        self.assertIsNotNone(finders.find("objektradar.css"))

    def test_die_seite_verweist_auf_das_stylesheet(self):
        self.assertContains(self._seite(), f'href="{static("objektradar.css")}"')

    def test_der_verweis_steht_auf_jeder_seite(self):
        # Er haengt in `basis.html`, nicht in einer einzelnen Vorlage. Die
        # Anmeldeseite ist die einzige ohne Anmeldung - und erbt ihn trotzdem.
        self.client.logout()
        self.assertContains(
            self.client.get(reverse("login")), f'href="{static("objektradar.css")}"'
        )

    # --- Zusage 11 --------------------------------------------------------
    #
    # Hier standen bis zum 04.09. fuenf Zeugen: dass die Liste Spaltenkoepfe
    # hat, dass sie Zeilen hat, dass jede Zelle die Bezeichnung ihres
    # Spaltenkopfs traegt, dass das Stylesheet ueberhaupt benannte Spalten
    # nennt und dass es diese Spalten in der Liste gibt.
    #
    # Alle fuenf sind mit der Tabelle gefallen. Sie bewachten EINE Sache: dass
    # der Umbau der Tabelle in Karten unter 48rem keine Zelle ohne Namen
    # zuruecklaesst - `data-spalte` war die Bezeichnung, die dort an die
    # Stelle des Spaltenkopfs trat. Es gibt keine zwei Fassungen mehr, keine
    # Zellen und keine Spaltenkoepfe; die Angaben stehen jetzt mit ihrem
    # Etikett am Wert, in jeder Breite dasselbe Markup.
    #
    # Was sie NICHT bewachten: dass die Liste irgendetwas Bestimmtes anzeigt.
    # Das haelt `ObjektlisteTests` und haelt es unveraendert.

    # --- Zusage 12 --------------------------------------------------------

    def test_der_leere_zustand_nennt_den_einwurf(self):
        # Ein leerer Bildschirm sagt, was als Naechstes zu tun ist.
        self.assertContains(self._seite(), "Wirf oben den Link zu einem Inserat ein.")


# =========================================================================
# Schritt 2, Abschnitt 4: die beiden Korrekturen aus der Sichtpruefung
# =========================================================================


class ObjektbezeichnungTests(TestCase):
    """Zusage 12: Titel, ersatzweise Portal und ID, ersatzweise die URL.

    Drei getrennte Zeugen, weil es drei getrennte Zweige sind. In einer
    Methode zusammengefasst maesse die zweite Behauptung nichts mehr, sobald
    die erste faellt.
    """

    def test_der_titel_steht_fuer_das_objekt(self):
        # Portal und ID sind gesetzt und treten trotzdem nicht an: der Titel
        # geht vor. Ohne diese beiden Zusatzwerte bewiese der Zeuge nur, dass
        # irgendetwas den Titel zurueckgibt - nicht, dass er Vorrang hat.
        o = Objekt.objects.create(
            url="https://www.immobilienscout24.de/expose/12345",
            portal=Portal.IMMOSCOUT24,
            inserats_id="12345",
            titel="Finca bei Sóller",
        )
        self.assertEqual(str(o), "Finca bei Sóller")

    def test_ohne_titel_stehen_portal_und_inserats_id(self):
        """Gemessen an ImmoScout24, nicht an idealista.

        Bei idealista sind Schluessel und Beschriftung dasselbe Wort - der
        Zeuge bliebe gruen, auch wenn hier der rohe Schluessel statt
        `get_portal_display()` stuende. "immoscout24" gegen "ImmoScout24"
        macht den Unterschied messbar.
        """
        o = Objekt.objects.create(
            url="https://www.immobilienscout24.de/expose/12345",
            portal=Portal.IMMOSCOUT24,
            inserats_id="12345",
        )
        self.assertEqual(str(o), "ImmoScout24 · 12345")

    def test_ohne_titel_und_ohne_schluessel_steht_die_url(self):
        o = Objekt.objects.create(url="https://www.beispiel.de/x/")
        self.assertEqual(str(o), "https://www.beispiel.de/x/")

    def test_ein_halb_gefuelltes_paar_traegt_die_bezeichnung_nicht(self):
        """Riegel: `Portal · ` mit leerer ID waere keine Bezeichnung.

        Das halb gefuellte Paar ist der Normalfall bei jedem Portal ohne
        bekanntes ID-Muster - siehe `portale.LEER`.
        """
        o = Objekt.objects.create(url="https://www.beispiel.de/x/", portal=Portal.SONSTIGES)
        self.assertEqual(str(o), "https://www.beispiel.de/x/")

    def test_der_ort_traegt_die_bezeichnung_nicht_mehr(self):
        """Bewusst aus der Kette gefallen - die Liste hat eine eigene Ortsspalte.

        Ohne diesen Zeugen liesse sich `ort` unbemerkt wieder einschieben, und
        die Objektspalte zeigte denselben Wert wie die Spalte daneben.
        """
        o = Objekt.objects.create(url="https://www.beispiel.de/x/", ort="Palma")
        self.assertEqual(str(o), "https://www.beispiel.de/x/")


class StylesheetKorrekturenTests(TestCase):
    """Abschnitt 4 im Stylesheet: gekappte Objektspalte, eigene Fehlerfarbe.

    Gelesen wird die CSS-Datei. Wie die Seite aussieht, entscheidet weiterhin
    der Blick auf den Bildschirm - diese Zeugen halten nur die Festlegungen
    fest, die sich still zuruecknehmen liessen.

    DURCHGESEHEN am 02.09. nach einem Kriterium: misst ein Zeuge eine Zusage
    an den Nutzer, die brechen kann, bleibt er; misst er Struktur oder
    Schreibweise des Stylesheets, faellt er. Was dabei herauskam, steht je
    Methode dort - und einer ist herausgefallen:

    `test_der_media_block_ist_ueberhaupt_auffindbar` behauptete, die Datei
    enthalte einen nicht-leeren `@media (min-width: 48rem)`-Block. Das ist
    keine Zusage an irgendjemanden, sondern eine Ansage an die Gliederung der
    Datei. Sie hat in der Layout-Runde eine Bauentscheidung diktiert: der
    Media-Block musste EIN einziger bleiben, damit `_block_ab_48rem` ihn
    greift. Das ist die falsche Reihenfolge - ein Test bewacht eine Zusage, er
    schreibt nicht vor, wie das Stylesheet gegliedert ist. Derselbe Bildschirm
    liesse sich mit zwei Bloecken, einer anderen Grenze oder Container-Queries
    bauen, und der Zeuge waere rot, ohne dass irgendwem etwas fehlte.

    Damit die Diktatur nicht ueber die Hintertuer zurueckkommt, sammelt
    `_bloecke_ab_48rem` jetzt ALLE Bloecke dieser Breite statt des ersten. Wie
    viele es sind, ist der Datei ueberlassen.
    """

    def _quelle(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8")

    def _bloecke_ab_48rem(self):
        """ALLE `@media (min-width: 48rem)`-Bloecke, aneinandergehaengt.

        Frueher war das der ERSTE Block - und genau daran hing die Ansage, es
        duerfe nur einen geben. Gezaehlt wird ueber die Klammern, weil ein
        regulaerer Ausdruck verschachtelte Bloecke nicht sauber schliesst.

        Die Eingrenzung selbst bleibt noetig: ohne sie koennte die
        Kappungsregel auf oberster Ebene stehen und die Zeugen unten blieben
        gruen - waehrend die Kartenansicht unter 48rem ihre Objektzeile auf
        eine Zeile zusammenzoege und abschnitte. Eingegrenzt wird also auf die
        BREITE, nicht mehr auf eine bestimmte Stelle in der Datei.
        """
        quelle = self._quelle()
        bloecke = []
        stelle = 0
        while True:
            start = quelle.find("@media (min-width: 48rem)", stelle)
            if start == -1:
                return bloecke
            offen = quelle.index("{", start)
            tiefe = 0
            for zeichen in range(offen, len(quelle)):
                if quelle[zeichen] == "{":
                    tiefe += 1
                elif quelle[zeichen] == "}":
                    tiefe -= 1
                    if tiefe == 0:
                        bloecke.append(quelle[offen : zeichen + 1])
                        stelle = zeichen + 1
                        break
            else:
                raise AssertionError("Der Media-Block ist nicht geschlossen.")

    # --- 4.1: den Titel kappen --------------------------------------------

    def test_der_titel_wird_in_beiden_fassungen_gekappt(self):
        """NACHGEZOGEN am 04.09. auf das neue Markup - und dabei UMGEDREHT.

        Bis dahin standen hier zwei Zeugen: der Titel wird ab 48rem gekappt,
        und darunter ausdruecklich NICHT. Der zweite hing an der `<table>`:
        eine Tabelle wird nie schmaler als ihr Inhalt, und ein einziges Objekt
        ohne Titel - also mit voller URL an dieser Stelle - zog die ganze
        Liste in die Breite. Gemessen bei 375px: 725px statt 343px. Deshalb
        durfte die Karten-Ueberschrift dort umbrechen, auch innerhalb eines
        Wortes.

        Die Liste ist keine Tabelle mehr. `minmax(0, 1fr)` an der mittleren
        Rasterspalte und `min-width: 0` an der Titelzeile deckeln die Breite,
        und die Kappung ist die ruhigere Antwort als ein dreizeilig
        umbrochener Link.

        Die ZUSAGE bleibt dieselbe und ist die eigentliche: eine Zeile darf
        die Liste nicht in die Breite ziehen. Sie kann brechen - ein
        entferntes `overflow: hidden` reicht -, und sie bricht sichtbar auf
        dem Geraet, auf dem die Liste unterwegs gelesen wird.

        Gemessen wird an der Datei und nicht am Bildschirm; das ist ein Ersatz
        und kein Beweis. Wer die Zusage wirklich pruefen will, braucht einen
        Browser.
        """
        quelle = re.sub(r"\s+", " ", self._quelle())
        regel = quelle[quelle.index(".titel {") :]
        regel = regel[: regel.index("}")]
        for eigenschaft in ("overflow: hidden", "text-overflow: ellipsis", "white-space: nowrap"):
            with self.subTest(eigenschaft=eigenschaft):
                self.assertIn(eigenschaft, regel)

    def test_die_kappung_steht_ausserhalb_jedes_media_blocks(self):
        """Die zweite Haelfte: sie gilt in BEIDEN Fassungen.

        Stuende die Regel im Block ab 48rem, zoege ein Objekt ohne Titel die
        Liste am Handy weiterhin in die Breite - genau der Zustand, den die
        alte Fassung mit `overflow-wrap: anywhere` abfangen musste.
        """
        ausserhalb = self._quelle()
        for block in self._bloecke_ab_48rem():
            ausserhalb = ausserhalb.replace(block, "")
        # Mit oeffnender Klammer gesucht: der Name allein steht auch in den
        # Kommentaren der Datei, und ein Zeuge, den ein Kommentar gruen haelt,
        # misst nichts.
        self.assertIn(".titel {", re.sub(r"\s+", " ", ausserhalb))

    def test_die_mittlere_spalte_kann_ueberhaupt_schmaler_werden(self):
        """Der Riegel unter den beiden Zeugen darueber.

        `overflow: hidden` allein kappt nichts, wenn das Element beliebig
        breit werden darf. In einem Raster ist die Mindestbreite einer Spalte
        `auto`, also ihre Inhaltsbreite - eine lange URL schoebe die Spalte
        auf, und die Kappung griffe nie. `minmax(0, 1fr)` an der Zeile und
        `min-width: 0` an der Titelzeile sind die Voraussetzung dafuer, dass
        die drei Eigenschaften oben ueberhaupt etwas tun.
        """
        quelle = re.sub(r"\s+", " ", self._quelle())
        zeile = quelle[quelle.index(".objekt {") :]
        self.assertIn("minmax(0, 1fr)", zeile[: zeile.index("}")])
        titelzeile = quelle[quelle.index(".titelzeile {") :]
        self.assertIn("min-width: 0", titelzeile[: titelzeile.index("}")])

    # --- 4.2: Fehlerfarbe von der Preissenkung trennen --------------------

    def test_es_gibt_eine_eigene_fehlerfarbe(self):
        """BLEIBT - als GRENZFALL, gemeldet.

        Fuer sich genommen misst er eine Schreibweise: dass die Zeichenkette
        `--fehler:` in der Datei steht. Er traegt aber etwas, das der Zeuge
        darunter NICHT traegt: dass die Eigenschaft ueberhaupt DEFINIERT ist.
        Faende sich nur `var(--fehler)` ohne Definition, bliebe der Zeuge
        darunter gruen, und die Fehlermeldung verloere ihre Farbe ganz stumm.

        Unsicher bleibt: eine umbenannte Eigenschaft - `--fehlerfarbe` etwa -
        machte ihn rot, ohne dass jemand etwas merkte. Im Zweifel steht er
        weiter da.
        """
        self.assertIn("--fehler:", self._quelle())

    def test_die_meldungsregel_greift_auf_die_fehlerfarbe_zu(self):
        """BLEIBT - eine Zusage an den Nutzer, kein Grenzfall.

        Ein Tippfehler im Formular darf nicht aussehen wie eine Preissenkung.
        Truege die Fehlermeldung `--signal`, stumpfte sie genau das Signal ab,
        auf das die ganze Liste hinarbeitet - und die Person, die den Preis
        sucht, saehe zwei laute Farben nebeneinander und keine Bedeutung mehr.

        Diese Zusage kann brechen, und zwar durch eine einzige geaenderte
        Zeile in der Meldungsregel. Genau das ist der Fall, den ein Zeuge
        abfangen soll.
        """
        quelle = self._quelle()
        regel = quelle[quelle.index(".meldungen li.error") :]
        regel = regel[: regel.index("}")]
        self.assertIn("var(--fehler)", regel)

    def test_die_signalfarbe_traegt_nur_die_preissenkung(self):
        """BLEIBT - in NEUER Fassung, weil die alte ihre Grundlage verloren hat.

        Bis zum 02.09. lautete die Zusage "`var(--signal)` steht nirgends" -
        richtig, solange die Preissenkung noch nicht gebaut war. Jetzt ist sie
        gebaut, und die Farbe steht an genau einer Stelle.

        Die Zusage selbst ist unveraendert und gehoert zu den Festlegungen,
        die nicht angetastet werden: `--signal` ist der Preissenkung
        vorbehalten. Nur ihre Messung musste mitwandern - von "nirgends" auf
        "nur dort". Haette man den Zeugen stattdessen entfernt, waere die
        lauteste Farbe des Werkzeugs ab sofort unbewacht gewesen.

        Gezaehlt wird ueber ALLE Vorkommen von `var(--signal)` und nicht nur
        geprueft, dass die Senkungsregel eines traegt: sonst duerfte die Farbe
        nebenher an fuenf weiteren Stellen auftauchen.
        """
        quelle = self._quelle()
        # Die Regel selbst herausschneiden - was danach noch uebrig bleibt,
        # darf die Farbe nicht mehr nennen.
        start = quelle.index(".preisaenderung.senkung {")
        ende = quelle.index("}", start) + 1
        uebrig = quelle[:start] + quelle[ende:]
        self.assertIn("var(--signal)", quelle[start:ende])
        self.assertNotIn("var(--signal)", uebrig)


class KommentarTests(TestCase):
    """Kein Template-Kommentar steht als Text auf der Seite.

    Django wertet `{# ... #}` NUR EINZEILIG aus. Ein ueber mehrere Zeilen
    umgebrochener Kommentar ist deshalb kein Kommentar, sondern Text - und
    wird vollstaendig ausgegeben, von der ersten Zeile an.

    Gemessen wird am KOMMENTARTEXT und nicht an `{#`. Ein Zeuge auf `{#`
    bliebe gruen, sobald derselbe Satz in anderer Form wieder auf der Seite
    landet - und genau der Satz ist es, den niemand dort lesen soll.

    NACHGEBESSERT am 02.09. Die erste Fassung mass an EINEM von damals vier
    Kommentaren, und zwar auf Seite 1. Sie war damit an zwei Stellen blind:

    1. Die uebrigen Kommentare wurden gar nicht angesehen. Ein Zeuge, der
       einen von vier prueft, sagt ueber die anderen drei nichts.
    2. Der Kommentar im Blaetter-Zweig steht in `{% if page_obj.has_other_pages %}`.
       Bei einem einzigen Objekt gibt es nur eine Seite, der Zweig wird nie
       betreten, und der Zeuge sah ihn auch dann nicht, wenn er ihn geprueft
       haette.

    Beides ist behoben: die Texte werden aus der Vorlage GELESEN statt hier
    abgeschrieben - eine zweite Liste driftet von der ersten weg, und ein
    spaeter ergaenzter Kommentar waere in einer abgeschriebenen Liste nicht
    enthalten -, und geprueft wird auf Seite 1 UND auf Seite 2.

    Gemessen wird je Block an dessen ERSTER Zeile. Sie ist in jedem der
    Bloecke ein langer, eindeutiger Satzanfang, und sie ist genau das, was
    beim beschriebenen Fehler zuerst auf der Seite steht. Die kuerzeren Zeilen
    weiter unten in den Bloecken ("gebaut.", "anklickbar.") taugen nicht als
    Messpunkt - sie koennten zufaellig auch anderswo stehen und den Zeugen
    grundlos rot machen.

    `assertNotContains` prueft nebenbei auf Status 200 - ein 302 auf die
    Anmeldeseite enthielte den Kommentartext ebenfalls nicht und liesse den
    Zeugen im Vakuum gruen werden.
    """

    VORLAGE = "templates/objekte/objektliste.html"

    #: So viele Kommentarbloecke stehen in der Vorlage. Ausgeschrieben und
    #: nicht mitgezaehlt: faellt ein Block heraus oder kommt einer dazu, soll
    #: das AUFFALLEN und nicht stillschweigend in die Ableitung wandern.
    #: Am 03.09. von sechs auf sieben: die Bildzelle hat ihren eigenen Block.
    #: Am 03.09. weiter auf neun - die Loesch-/Statusfarben-Runde hat der
    #: Freitextsuche und der Statuszelle je einen Block gegeben. Beide sind
    #: damit von den Zeugen unten mitbewacht, ohne dass hier ein Text
    #: abgeschrieben werden musste.
    #: Am 04.09. auf zehn: die Besuchsmarke hat ihren eigenen Block in der
    #: Bezeichnungszelle. Der Zeuge unten hat den Zuwachs gemeldet - genau
    #: dafuer steht die Zahl hier ausgeschrieben.
    #: Am 04.09. weiter auf elf: das verdeckte Votum hat der Votum-Zelle einen
    #: eigenen Block gegeben.
    #: Am 04.09. auf FUENFZEHN: die Oberflaechenrunde hat die Vorlage
    #: vollstaendig neu geschrieben. Dazugekommen sind die Kopfzeile der
    #: Liste, der aufklappbare Filterblock, die Sortierleiste und die
    #: Unterzeile; die Statuszelle und die Bezeichnungszelle sind in andere
    #: Bloecke aufgegangen. Die Zahl steht weiter AUSGESCHRIEBEN da: faellt
    #: ein Block heraus oder kommt einer dazu, soll das auffallen und nicht
    #: stillschweigend in die Ableitung wandern.
    BLOECKE = 15

    def setUp(self):
        self.person = Person.objects.create_user(
            "steffen", password="ein-langes-passwort"
        )
        self.client.force_login(self.person)

    def _quelle(self):
        return (settings.BASE_DIR / self.VORLAGE).read_text(encoding="utf-8")

    def _erste_zeilen(self):
        """Die erste Textzeile jedes `{% comment %}`-Blocks der Vorlage."""
        zeilen = []
        for block in re.findall(
            r"{% comment %}(.*?){% endcomment %}", self._quelle(), re.S
        ):
            inhalt = [z.strip() for z in block.splitlines() if z.strip()]
            if inhalt:
                zeilen.append(inhalt[0])
        return zeilen

    # --- die Riegel gegen einen Zeugen im Vakuum ---------------------------

    def test_die_vorlage_traegt_ueberhaupt_kommentarbloecke(self):
        """Ohne diesen Zeugen liefen die beiden unten ueber eine leere Liste."""
        self.assertNotEqual(self._erste_zeilen(), [])

    def test_die_ableitung_findet_jeden_block_der_vorlage(self):
        """Riegel auf die Ableitung selbst.

        Griffe der Ausdruck nur den ersten Block - etwa weil jemand `re.S`
        entfernt -, prueften die Zeugen unten weiterhin einen von sechs und
        die Runde staende wieder da, wo sie angefangen hat.
        """
        self.assertEqual(len(self._erste_zeilen()), self.BLOECKE)
        self.assertEqual(self._quelle().count("{% comment %}"), self.BLOECKE)

    def _mehrseitig(self):
        """Drei Objekte bei einer Seitengroesse von zwei - also zwei Seiten."""
        for nummer in range(3):
            Objekt.objects.create(url=f"https://x/{nummer}", titel=f"Objekt {nummer}")

    def test_seite_zwei_traegt_ueberhaupt_ein_objekt(self):
        """Eine leere Seite 2 rendert die Tabelle nicht und damit auch nicht
        die Kommentare darin - der Zeuge unten waere gruen ohne Messung."""
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self._mehrseitig()
            self.assertEqual(len(self.client.get("/?seite=2").context["objekte"]), 1)

    def test_der_blaetterzweig_wird_auf_seite_zwei_wirklich_betreten(self):
        """Der vierte Kommentar sitzt in `{% if page_obj.has_other_pages %}`.

        Wird der Zweig nicht betreten, steht sein Text selbstverstaendlich
        nicht auf der Seite - und der Zeuge unten maesse ihn nur scheinbar.
        """
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self._mehrseitig()
            self.assertContains(self.client.get("/?seite=2"), 'class="blaettern"')

    # --- die Zusage --------------------------------------------------------

    def test_kein_kommentartext_steht_auf_seite_eins(self):
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self._mehrseitig()
            antwort = self.client.get("/")
            for zeile in self._erste_zeilen():
                with self.subTest(zeile=zeile):
                    self.assertNotContains(antwort, zeile)

    def test_kein_kommentartext_steht_auf_seite_zwei(self):
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self._mehrseitig()
            antwort = self.client.get("/?seite=2")
            for zeile in self._erste_zeilen():
                with self.subTest(zeile=zeile):
                    self.assertNotContains(antwort, zeile)

    def test_kein_kommentartext_steht_in_der_leeren_liste(self):
        """Die dritte Fassung der Seite: ohne ein einziges Objekt.

        Tabelle und Blaetter-Zweig fehlen dort, der Filterblock steht aber da -
        und mit ihm vier der sechs Bloecke.
        """
        antwort = self.client.get("/")
        for zeile in self._erste_zeilen():
            with self.subTest(zeile=zeile):
                self.assertNotContains(antwort, zeile)


# =========================================================================
# Schritt 2, Abschnitt 3: Vorschau (GET) und Uebernahme (POST)
# =========================================================================


class UebernahmeTests(TestCase):
    """Zusagen 1 bis 11 des Lesezeichen-Zulaufs.

    Die drei Stationen sind bewusst geschnitten: das Lesezeichen legt nichts
    an, die Vorschau legt nichts an, erst die Uebernahme schreibt. Die Zeugen
    hier halten diesen Schnitt.
    """

    INSERAT = "https://www.idealista.com/inmueble/12345/"

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    # --- Handgriffe -------------------------------------------------------

    def _parameter(self, **abweichungen):
        """Ein vollstaendiger Satz, so wie ihn das Lesezeichen uebergibt."""
        daten = {
            "url": self.INSERAT,
            "titel": "Villa am Hang",
            "beschreibung": "Blick über die Bucht.",
            "preis": "750000",
            "wohnflaeche": "200",
            "zimmer": "4",
        }
        daten.update(abweichungen)
        return {k: v for k, v in daten.items() if v not in (None, "")}

    def _vorschau(self, **abweichungen):
        return self.client.get("/uebernehmen/", self._parameter(**abweichungen))

    def _post_rumpf(self, antwort, **abweichungen):
        """Der POST-Rumpf, so wie ihn der Browser aus der Vorschau sendet.

        Ueber `widget.format_value()` und nicht ueber die Rohwerte - genau der
        gerenderte Text geht zurueck. Ein Test, der stattdessen "200" schickt,
        wo das Formular "200,00" anzeigt, prueft den Rundlauf nicht, sondern
        umgeht ihn. Dieselbe Bauart wie in `BearbeitenTests`.
        """
        formular = antwort.context["form"]
        daten = {}
        for name, feld in formular.fields.items():
            gerendert = feld.widget.format_value(formular[name].value())
            if isinstance(gerendert, list):
                gerendert = gerendert[0] if gerendert else ""
            daten[name] = "" if gerendert is None else gerendert
        for verstecktes in ("url", "portal", "inserats_id", "bilder"):
            daten[verstecktes] = antwort.context[verstecktes]
        daten.update(abweichungen)
        return daten

    def _uebernehmen(self, abweichungen=None, **parameter):
        """Der ganze Weg: Vorschau aufrufen, dann absenden, was dort steht."""
        antwort = self._vorschau(**parameter)
        return self.client.post(
            "/uebernehmen/", self._post_rumpf(antwort, **(abweichungen or {}))
        )

    def _bestandsobjekt(self, **abweichungen):
        werte = {
            "url": self.INSERAT,
            "portal": Portal.IDEALISTA,
            "inserats_id": "12345",
            "titel": "Finca",
            "wohnflaeche": Decimal("140"),
            "aktueller_preis": Decimal("250000"),
        }
        werte.update(abweichungen)
        return Objekt.objects.create(**werte)

    # --- Zusage 1: ohne Anmeldung geht nichts -----------------------------

    def test_die_vorschau_ohne_anmeldung_fuehrt_auf_die_anmeldeseite(self):
        self.client.logout()
        antwort = self._vorschau()
        self.assertTrue(
            antwort["Location"].startswith("/anmelden/?next="), antwort["Location"]
        )

    def test_die_vorschau_ohne_anmeldung_zeigt_nichts(self):
        self.client.logout()
        self.assertEqual(self._vorschau().status_code, 302)

    def test_die_uebernahme_ohne_anmeldung_legt_nichts_an(self):
        """Der Riegel gilt auch fuer POST, nicht nur fuer die Vorschau.

        Aufgebaut wird der Rumpf angemeldet - sonst gaebe es keine Vorschau,
        aus der er stammen koennte. Abgeschickt wird abgemeldet.
        """
        rumpf = self._post_rumpf(self._vorschau())
        self.client.logout()
        self.client.post("/uebernehmen/", rumpf)
        self.assertEqual(Objekt.objects.count(), 0)

    # --- Zusage 2: GET legt nichts an -------------------------------------

    def test_die_vorschau_legt_kein_objekt_an(self):
        self._vorschau()
        self.assertEqual(Objekt.objects.count(), 0)

    def test_dieselben_werte_legen_per_post_sehr_wohl_ein_objekt_an(self):
        """Der Riegel gegen einen blinden Zeugen darueber.

        Ohne ihn koennte die Vorschau an unvollstaendigen Parametern
        scheitern, und "es wurde nichts angelegt" bezeugte nicht die Trennung
        von Lesen und Schreiben, sondern nur einen kaputten Aufruf. Hier
        laeuft derselbe Parametersatz einmal als GET und einmal als POST.
        """
        self._uebernehmen()
        self.assertEqual(Objekt.objects.count(), 1)

    def test_die_vorschau_legt_auch_beim_zweiten_aufruf_nichts_an(self):
        self._vorschau()
        self._vorschau()
        self.assertEqual(Objekt.objects.count(), 0)

    def test_die_vorschau_aendert_am_bestehenden_objekt_nichts(self):
        objekt = self._bestandsobjekt()
        self._vorschau(titel="Villa am Hang", preis="750000")
        objekt.refresh_from_db()
        self.assertEqual(objekt.titel, "Finca")

    def test_die_vorschau_schreibt_am_bestehenden_objekt_keinen_preis_fort(self):
        objekt = self._bestandsobjekt()
        self._vorschau(preis="750000")
        self.assertEqual(objekt.preise.count(), 1)

    def test_die_vorschau_legt_keine_bilder_an(self):
        objekt = self._bestandsobjekt()
        self._vorschau(bilder=["https://bild.example/1.jpg"])
        self.assertEqual(objekt.bilder.count(), 0)

    # --- Zusage 3: ohne url ------------------------------------------------

    def test_die_vorschau_ohne_link_leitet_auf_die_liste(self):
        antwort = self.client.get("/uebernehmen/")
        self.assertEqual(antwort["Location"], "/")

    def test_die_vorschau_ohne_link_meldet_was_zu_tun_ist(self):
        antwort = self.client.get("/uebernehmen/", follow=True)
        self.assertContains(
            antwort,
            "Kein Link übergeben. Öffne das Inserat und klicke das Lesezeichen erneut.",
        )

    def test_die_vorschau_mit_leerem_link_meldet_dasselbe(self):
        # Ein Lesezeichen auf einer Seite ohne brauchbare Adresse schickt
        # `url=` mit - das ist derselbe Sachverhalt wie gar kein Parameter.
        antwort = self.client.get("/uebernehmen/", {"url": "  "}, follow=True)
        self.assertContains(antwort, "Kein Link übergeben.")

    def test_ein_kaputter_link_meldet_sich_ebenfalls(self):
        antwort = self.client.get("/uebernehmen/", {"url": "kein-link"}, follow=True)
        self.assertContains(antwort, "Das ist kein gültiger Link.")

    def test_ein_zu_langer_link_meldet_sich_ebenfalls(self):
        antwort = self.client.get(
            "/uebernehmen/", {"url": "https://beispiel.de/" + "a" * 600}, follow=True
        )
        self.assertContains(antwort, "länger als")

    # --- Zusage 4: das bestehende Objekt wird erkannt ----------------------

    #: Am 02.09. von `.it` auf `.com` gezogen: `idealista.it` ist als Domain
    #: herausgefallen und liefert kein Paar mehr. Die uebrigen drei
    #: Abweichungen - Sprachpraefix, Tracking-Parameter, fehlender
    #: abschliessender Schraegstrich - tragen den Zeugen unveraendert, und
    #: `rstrip("/")` fuehrt die beiden Schreibweisen weiterhin NICHT zusammen.
    #: Der starke Vergleich bleibt also der, der hier arbeitet.
    ANDERE_SCHREIBWEISE = "https://www.idealista.com/en/inmueble/12345?utm_source=mail"

    def test_die_vorschau_erkennt_das_bestehende_objekt_ueber_den_schluessel(self):
        """Sprachpraefix, Tracking-Parameter, kein abschliessender
        Schraegstrich - und trotzdem dasselbe Inserat.

        Ueber die Roh-URL faende es keiner der beiden Vergleiche; ueber Portal
        und Inserats-ID schon.
        """
        objekt = self._bestandsobjekt()
        antwort = self._vorschau(url=self.ANDERE_SCHREIBWEISE)
        self.assertEqual(antwort.context["objekt"], objekt)

    def test_bei_einem_treffer_lautet_die_ueberschrift_ergaenzen(self):
        self._bestandsobjekt()
        self.assertContains(self._vorschau(url=self.ANDERE_SCHREIBWEISE), "Objekt ergänzen")

    def test_bei_einem_treffer_steht_der_weg_zur_objektansicht_offen(self):
        objekt = self._bestandsobjekt()
        self.assertContains(
            self._vorschau(url=self.ANDERE_SCHREIBWEISE), f'href="/objekt/{objekt.pk}/"'
        )

    def test_ohne_treffer_lautet_die_ueberschrift_uebernehmen(self):
        """Der Riegel: die Ergaenzungs-Ansicht darf nicht immer erscheinen."""
        self.assertContains(self._vorschau(), "Neues Objekt übernehmen")

    def test_eine_andere_inserats_id_ist_ein_anderes_objekt(self):
        self._bestandsobjekt()
        antwort = self._vorschau(url="https://www.idealista.com/inmueble/99999/")
        self.assertIsNone(antwort.context["objekt"])

    # --- Zusage 5: der Bestandswert gewinnt --------------------------------

    def test_das_titelfeld_traegt_den_bestandswert(self):
        self._bestandsobjekt()
        antwort = self._vorschau(titel="Villa am Hang")
        self.assertEqual(antwort.context["form"]["titel"].value(), "Finca")

    def test_das_titelfeld_traegt_NICHT_den_gelesenen_wert(self):
        """Getrennt vom Zeugen darueber, weil er etwas anderes misst.

        Der obere faellt auch, wenn das Feld leer bleibt; dieser faellt genau
        dann, wenn der gelesene Wert den Bestand verdraengt hat.
        """
        self._bestandsobjekt()
        antwort = self._vorschau(titel="Villa am Hang")
        self.assertNotEqual(antwort.context["form"]["titel"].value(), "Villa am Hang")

    def test_das_preisfeld_traegt_den_bestandswert(self):
        self._bestandsobjekt()
        antwort = self._vorschau(preis="750000")
        self.assertEqual(antwort.context["form"]["kaufpreis"].value(), Decimal("250000.00"))

    def test_der_abweichende_gelesene_wert_steht_als_hinweis_darunter(self):
        self._bestandsobjekt()
        self.assertContains(self._vorschau(preis="750000"), "gelesen: 750.000 €")

    def test_der_hinweis_nennt_auch_die_abweichende_flaeche(self):
        self._bestandsobjekt()
        self.assertContains(self._vorschau(wohnflaeche="200"), "gelesen: 200 m²")

    def test_ein_gelesener_wert_gleich_dem_bestand_erzeugt_KEINEN_hinweis(self):
        """Der zweite Riegel gegen einen blinden Zeugen.

        Verglichen wird im Typ des Feldes: die gelesene "140" und der
        gespeicherte `Decimal("140.00")` sind derselbe Wert. Als Zeichenketten
        verglichen waeren sie verschieden, und unter jedem Zahlenfeld staende
        ein Hinweis, der nichts meldet - und der Zeuge darueber bliebe
        trotzdem gruen.
        """
        self._bestandsobjekt()
        antwort = self._vorschau(wohnflaeche="140", preis="250000", titel="Finca")
        self.assertNotContains(antwort, "gelesen:")

    def test_wo_kein_bestandswert_steht_gewinnt_der_gelesene(self):
        # `beschreibung` ist am Bestandsobjekt leer - dort ist der gelesene
        # Wert die Vorbelegung, nicht ein Hinweis darunter.
        self._bestandsobjekt()
        antwort = self._vorschau(beschreibung="Blick über die Bucht.")
        self.assertEqual(
            antwort.context["form"]["beschreibung"].value(), "Blick über die Bucht."
        )

    def test_beim_neuen_objekt_stehen_die_gelesenen_werte_im_feld(self):
        antwort = self._vorschau()
        self.assertEqual(antwort.context["form"]["titel"].value(), "Villa am Hang")

    def test_beim_neuen_objekt_steht_kein_hinweis_darunter(self):
        # Es gibt keinen Bestandswert, von dem etwas abweichen koennte - ein
        # Hinweis wiederholte nur, was im Feld steht.
        self.assertNotContains(self._vorschau(), "gelesen:")

    # --- Zusage 6: die Uebernahme legt an ----------------------------------

    def test_die_uebernahme_legt_ein_objekt_an(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.count(), 1)

    def test_das_angelegte_objekt_traegt_das_portal(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().portal, Portal.IDEALISTA)

    def test_das_angelegte_objekt_traegt_die_inserats_id(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().inserats_id, "12345")

    def test_das_angelegte_objekt_traegt_die_quelle_url_eingeworfen(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().quelle, Quelle.URL_EINGEWORFEN)

    def test_das_angelegte_objekt_kennt_die_einstellende_person(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().eingestellt_von, self.person)

    def test_das_angelegte_objekt_traegt_die_gelesenen_werte(self):
        self._uebernehmen()
        objekt = Objekt.objects.get()
        self.assertEqual(objekt.titel, "Villa am Hang")

    def test_das_angelegte_objekt_traegt_die_gelesene_wohnflaeche(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().wohnflaeche, Decimal("200"))

    def test_das_angelegte_objekt_traegt_die_url_im_original(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().url, self.INSERAT)

    def test_nach_der_uebernahme_fuehrt_der_weg_auf_die_objektansicht(self):
        """Anders als der Einwurf, der auf die Liste zurueckfuehrt.

        Hier hat die Person gerade Daten geprueft und will sehen, was daraus
        wurde.
        """
        antwort = self._uebernehmen()
        self.assertEqual(antwort["Location"], f"/objekt/{Objekt.objects.get().pk}/")

    def test_die_uebernahme_meldet_sich(self):
        antwort = self._uebernehmen()
        self.assertEqual(
            [str(m) for m in antwort.wsgi_request._messages], ["Objekt übernommen."]
        )

    def test_die_uebernahme_ergaenzt_das_bestehende_objekt(self):
        objekt = self._bestandsobjekt()
        self._uebernehmen(abweichungen={"titel": "Villa am Hang"})
        objekt.refresh_from_db()
        self.assertEqual(objekt.titel, "Villa am Hang")

    def test_die_uebernahme_legt_kein_zweites_objekt_an(self):
        self._bestandsobjekt()
        self._uebernehmen(url=self.ANDERE_SCHREIBWEISE)
        self.assertEqual(Objekt.objects.count(), 1)

    def test_die_ergaenzung_ruehrt_den_dublettenschluessel_nicht_an(self):
        """Portal, ID und URL des Bestands bleiben stehen.

        Sie sind der Dublettenschluessel. Ihn nebenbei aus einer Heuristik zu
        ueberschreiben waere genau das stillschweigende Ueberschreiben, das
        dieser Weg vermeiden soll.
        """
        objekt = self._bestandsobjekt()
        self._uebernehmen(url=self.ANDERE_SCHREIBWEISE)
        objekt.refresh_from_db()
        self.assertEqual(objekt.url, self.INSERAT)

    def test_die_ergaenzung_setzt_zuletzt_gesehen(self):
        objekt = self._bestandsobjekt()
        self._uebernehmen()
        objekt.refresh_from_db()
        self.assertIsNotNone(objekt.zuletzt_gesehen)

    def test_die_ergaenzung_schreibt_die_aendernde_person_fort(self):
        anna = Person.objects.create_user("anna", password="lang-genug-123")
        objekt = self._bestandsobjekt(zuletzt_geaendert_von=anna)
        self._uebernehmen()
        objekt.refresh_from_db()
        self.assertEqual(objekt.zuletzt_geaendert_von, self.person)

    # --- Zusage 7: genau ein Preiseintrag ----------------------------------

    def test_ein_uebermittelter_preis_erzeugt_genau_einen_eintrag(self):
        """Genau einen, nicht zwei.

        `Objekt.save()` legt beim Anlegen selbst einen Eintrag an, sobald
        `aktueller_preis` gesetzt ist. Liefe der Preis nicht ausschliesslich
        ueber `preis_setzen()`, staenden hier zwei Eintraege - einer davon mit
        der falschen Quelle.
        """
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().preise.count(), 1)

    def test_der_eintrag_traegt_die_quelle_erneuter_abruf(self):
        self._uebernehmen()
        self.assertEqual(
            Objekt.objects.get().preise.get().quelle, PreisQuelle.ERNEUTER_ABRUF
        )

    def test_der_eintrag_traegt_den_uebermittelten_preis(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().preise.get().preis, Decimal("750000"))

    def test_der_preis_steht_danach_auch_am_objekt(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().aktueller_preis, Decimal("750000"))

    def test_ein_abweichender_preis_am_bestand_erzeugt_einen_zweiten_eintrag(self):
        """Der Riegel gegen einen blinden Zeugen bei Zusage 8.

        Ohne ihn koennte der Preisweg vollstaendig tot sein, und "kein zweiter
        Eintrag" bezeugte nichts.
        """
        objekt = self._bestandsobjekt()
        self._uebernehmen(abweichungen={"kaufpreis": "240.000,00"})
        self.assertEqual(objekt.preise.count(), 2)

    def test_der_zweite_eintrag_am_bestand_traegt_erneuter_abruf(self):
        objekt = self._bestandsobjekt()
        self._uebernehmen(abweichungen={"kaufpreis": "240.000,00"})
        self.assertEqual(objekt.preise.first().quelle, PreisQuelle.ERNEUTER_ABRUF)

    # --- Zusage 8: derselbe Preis erzeugt keinen zweiten Eintrag ------------

    def test_ein_unveraenderter_preis_erzeugt_keinen_zweiten_eintrag(self):
        objekt = self._bestandsobjekt()
        self._uebernehmen(preis="250000")
        self.assertEqual(objekt.preise.count(), 1)

    def test_der_bestehende_eintrag_behaelt_dabei_seine_quelle(self):
        # Ein "erneuter Abruf", der nichts Neues gemessen hat, darf den
        # urspruenglichen Eintrag nicht umschreiben.
        objekt = self._bestandsobjekt()
        self._uebernehmen(preis="250000")
        self.assertEqual(objekt.preise.get().quelle, PreisQuelle.VON_HAND)

    def test_ein_unveraenderter_preis_laesst_den_preis_am_objekt_stehen(self):
        objekt = self._bestandsobjekt()
        self._uebernehmen(preis="250000")
        objekt.refresh_from_db()
        self.assertEqual(objekt.aktueller_preis, Decimal("250000.00"))

    # --- Zusage 9: kein Preis, kein Eintrag --------------------------------

    def test_ohne_uebermittelten_preis_entsteht_kein_eintrag(self):
        self._uebernehmen(preis=None)
        self.assertEqual(Objekt.objects.get().preise.count(), 0)

    def test_ohne_uebermittelten_preis_bleibt_das_objekt_ohne_preis(self):
        self._uebernehmen(preis=None)
        self.assertIsNone(Objekt.objects.get().aktueller_preis)

    def test_ein_leeres_preisfeld_loescht_den_bestehenden_eintrag_nicht(self):
        objekt = self._bestandsobjekt()
        self._uebernehmen(preis=None, abweichungen={"kaufpreis": ""})
        self.assertEqual(objekt.preise.count(), 1)

    def test_ein_leeres_preisfeld_laesst_den_preis_am_objekt_stehen(self):
        """Leer heisst "nicht aendern", nicht "Preis loeschen".

        `Preisverlauf.preis` ist nicht nullbar, und ein Verlauf, aus dem
        Eintraege verschwinden, waere keiner.
        """
        objekt = self._bestandsobjekt()
        self._uebernehmen(preis=None, abweichungen={"kaufpreis": ""})
        objekt.refresh_from_db()
        self.assertEqual(objekt.aktueller_preis, Decimal("250000.00"))

    # --- Zusage 10: Bilder --------------------------------------------------

    BILDER = ["https://bild.example/1.jpg", "https://bild.example/2.jpg"]

    def test_uebermittelte_bilder_werden_angelegt(self):
        self._uebernehmen(bilder=self.BILDER)
        self.assertEqual(Objekt.objects.get().bilder.count(), 2)

    def test_die_bilder_tragen_die_uebermittelten_adressen(self):
        self._uebernehmen(bilder=self.BILDER)
        self.assertEqual(
            list(Objekt.objects.get().bilder.values_list("url", flat=True)), self.BILDER
        )

    def test_ein_zweiter_aufruf_mit_denselben_bildern_legt_keine_dubletten_an(self):
        self._uebernehmen(bilder=self.BILDER)
        self._uebernehmen(bilder=self.BILDER)
        self.assertEqual(Objekt.objects.get().bilder.count(), 2)

    def test_ein_neues_bild_kommt_dazu_ohne_die_alten_zu_loeschen(self):
        neu = "https://bild.example/3.jpg"
        self._uebernehmen(bilder=self.BILDER)
        self._uebernehmen(bilder=[self.BILDER[1], neu])
        self.assertEqual(
            list(Objekt.objects.get().bilder.values_list("url", flat=True)),
            self.BILDER + [neu],
        )

    def test_eine_unbrauchbare_bildadresse_bricht_die_uebernahme_nicht_ab(self):
        """Sie faellt heraus, das Objekt entsteht trotzdem.

        Die Adressen stammen aus fremdem Markup; eine davon unlesbar zu finden
        ist kein Grund, die geprueften Daten wegzuwerfen.
        """
        self._uebernehmen(bilder=["kein-bild", self.BILDER[0]])
        self.assertEqual(
            list(Objekt.objects.get().bilder.values_list("url", flat=True)),
            [self.BILDER[0]],
        )

    def test_ohne_bilder_entsteht_kein_bild(self):
        self._uebernehmen()
        self.assertEqual(Objekt.objects.get().bilder.count(), 0)

    # --- Zusage 11: CSRF ----------------------------------------------------

    def _strenger_client(self):
        from django.test import Client

        streng = Client(enforce_csrf_checks=True)
        streng.force_login(self.person)
        return streng

    def test_die_uebernahme_ohne_csrf_token_wird_abgewiesen(self):
        streng = self._strenger_client()
        rumpf = self._post_rumpf(self._vorschau())
        self.assertEqual(streng.post("/uebernehmen/", rumpf).status_code, 403)

    def test_die_uebernahme_ohne_csrf_token_legt_nichts_an(self):
        streng = self._strenger_client()
        rumpf = self._post_rumpf(self._vorschau())
        streng.post("/uebernehmen/", rumpf)
        self.assertEqual(Objekt.objects.count(), 0)

    def test_mit_csrf_token_laeuft_derselbe_aufruf_durch(self):
        """Der Riegel gegen einen blinden Zeugen darueber.

        Ohne ihn koennte der 403 aus einem ganz anderen Grund kommen - einem
        fehlenden Pflichtfeld etwa - und die Zusage waere nicht bezeugt.
        """
        streng = self._strenger_client()
        antwort = streng.get("/uebernehmen/", self._parameter())
        rumpf = self._post_rumpf(antwort)
        rumpf["csrfmiddlewaretoken"] = streng.cookies["csrftoken"].value
        streng.post("/uebernehmen/", rumpf)
        self.assertEqual(Objekt.objects.count(), 1)

    def test_die_uebernahme_traegt_kein_csrf_exempt(self):
        """Strukturell, nicht nur am Verhalten gemessen.

        `csrf_exempt` setzt ein Attribut an der Ansicht. Es zu pruefen faengt
        den Fall, in dem jemand es setzt und der Verhaltenszeuge oben aus
        einem anderen Grund gruen bleibt.
        """
        from .urls import urlpatterns

        ansicht = next(m for m in urlpatterns if m.name == "uebernehmen").callback
        self.assertFalse(getattr(ansicht, "csrf_exempt", False))

    # --- Fehlerverhalten ----------------------------------------------------

    def test_ein_ungueltiges_feld_kommt_als_formular_zurueck(self):
        antwort = self._uebernehmen(abweichungen={"baujahr": "vorgestern"})
        self.assertEqual(antwort.status_code, 200)

    def test_ein_ungueltiges_feld_legt_nichts_an(self):
        self._uebernehmen(abweichungen={"baujahr": "vorgestern"})
        self.assertEqual(Objekt.objects.count(), 0)

    def test_ein_ungueltiges_feld_sagt_was_zu_tun_ist(self):
        antwort = self._uebernehmen(abweichungen={"baujahr": "vorgestern"})
        self.assertContains(antwort, "Bitte die markierten Felder prüfen.")

    def test_die_uebernahme_ohne_link_leitet_um_und_meldet_sich(self):
        antwort = self.client.post("/uebernehmen/", {}, follow=True)
        self.assertContains(antwort, "Kein Link übergeben.")

    # --- Zuschnitt der Vorschau ---------------------------------------------

    def test_die_versteckten_felder_tragen_die_url(self):
        self.assertContains(
            self._vorschau(), f'<input type="hidden" name="url" value="{self.INSERAT}">'
        )

    def test_die_versteckten_felder_tragen_das_portal(self):
        self.assertContains(
            self._vorschau(), '<input type="hidden" name="portal" value="idealista">'
        )

    def test_die_versteckten_felder_tragen_die_inserats_id(self):
        self.assertContains(
            self._vorschau(), '<input type="hidden" name="inserats_id" value="12345">'
        )

    def test_die_versteckten_felder_tragen_die_bilder(self):
        self.assertContains(
            self._vorschau(bilder=self.BILDER),
            f'<input type="hidden" name="bilder" value="{self.BILDER[0]}">',
        )

    def test_die_url_ist_kein_von_hand_pflegbares_feld(self):
        # Sie steht versteckt im Formular, nicht als Eingabefeld: wer sie hier
        # aendert, aendert den Dublettenschluessel im Vorbeigehen.
        self.assertNotIn("url", self._vorschau().context["form"].fields)

    def test_der_status_ist_auch_hier_nicht_im_formular(self):
        # Er laeuft ueber `status_setzen()`, das die Aenderung protokolliert.
        self.assertNotIn("status", self._vorschau().context["form"].fields)

    def test_der_aktuelle_preis_ist_auch_hier_kein_formularfeld(self):
        self.assertNotIn("aktueller_preis", self._vorschau().context["form"].fields)

    def test_die_vorschau_nimmt_nur_die_vereinbarten_parameter_an(self):
        """Was nicht in `GELESENE_FELDER` steht, wird nicht uebernommen.

        Sonst waere die Vorschau-Adresse ein offenes Formular: wer sie baut,
        setzte Status, Quelle oder die einstellende Person gleich mit.
        """
        antwort = self._vorschau(ort="Palma", status=Status.HEISSE_SPUR)
        self.assertNotEqual(antwort.context["form"]["ort"].value(), "Palma")


# =========================================================================
# Schritt 2, Abschnitt 2: die Lesezeichen-Seite und das Skript
# =========================================================================


class LesezeichenSkriptTests(SimpleTestCase):
    """Das Skript selbst - ohne Datenbank, ohne Anfrage.

    Was hier steht, laesst sich nicht am Verhalten messen: das Skript laeuft
    in einem fremden Browser auf einer fremden Seite. Diese Zeugen halten die
    drei Festlegungen, an denen es sonst still zerbricht.
    """

    def test_das_skript_ist_eine_zeile(self):
        """Ein Zeilenumbruch im `href` beendet das Lesezeichen an dieser Stelle."""
        self.assertNotIn("\n", lesezeichen.SKRIPT)

    def test_das_skript_enthaelt_keine_doppelten_anfuehrungszeichen(self):
        """Ein `"` spraenge das `href`-Attribut, in dem das Skript steht."""
        self.assertNotIn('"', lesezeichen.SKRIPT)

    def test_das_skript_oeffnet_ein_neues_fenster(self):
        self.assertIn("window.open(", lesezeichen.SKRIPT)

    def test_das_skript_ruft_nichts_per_fetch_ab(self):
        """`fetch`, `XMLHttpRequest` und ein nachgeladenes Skript scheitern alle
        drei an der Content-Security-Policy der Portalseiten oder an CORS.

        `window.open` ist eine Navigation und von beidem nicht betroffen. Faellt
        dieser Zeuge, hat jemand den einen Weg verlassen, der funktioniert.
        """
        for verboten in ("fetch(", "XMLHttpRequest", "createElement"):
            with self.subTest(verboten=verboten):
                self.assertNotIn(verboten, lesezeichen.SKRIPT)

    def test_das_skript_begrenzt_den_query_string(self):
        """Ein abgeschnittener Aufruf waere ein stiller Fehler."""
        self.assertIn(str(lesezeichen.QUERY_MAXLAENGE), lesezeichen.SKRIPT)

    def test_die_zieladresse_hat_genau_eine_stelle_im_skript(self):
        self.assertEqual(lesezeichen.SKRIPT.count(lesezeichen.PLATZHALTER), 1)

    def test_der_platzhalter_verschwindet_beim_einsetzen(self):
        fertig = lesezeichen.skript_fuer("https://ziel.example/uebernehmen/")
        self.assertNotIn(lesezeichen.PLATZHALTER, fertig)

    def test_die_eingesetzte_adresse_steht_im_skript(self):
        fertig = lesezeichen.skript_fuer("https://ziel.example/uebernehmen/")
        self.assertIn("https://ziel.example/uebernehmen/", fertig)

    def test_das_skript_uebergibt_genau_die_felder_die_die_vorschau_annimmt(self):
        """Beide Seiten aus derselben Liste gelesen, nicht zweimal geschrieben.

        Eine Umbenennung auf einer Seite fiele sonst STILL aus: das Lesezeichen
        schickte weiter den alten Namen, die Vorschau liesse ihn fallen, und
        das Feld bliebe ohne jede Meldung leer.

        `url` und `bilder` stehen nicht in `GELESENE_FELDER` - die erste ist
        Pflicht und laeuft durch die URL-Pruefung, die zweiten laufen am
        Formular vorbei.
        """
        gesendet = set(re.findall(r"setze\('([a-z_]+)'", lesezeichen.SKRIPT))
        self.assertEqual(gesendet, set(views.GELESENE_FELDER) | {"url", "bilder"})

    def test_das_skript_liest_die_grundstuecksgroesse_nicht(self):
        """Sie ist von der Wohnflaeche im Fliesstext nicht sicher zu
        unterscheiden; ein verwechselter Wert waere schlimmer als ein leerer."""
        self.assertNotIn("grundstuecksgroesse", lesezeichen.SKRIPT)

    def test_das_skript_kuerzt_die_beschreibung(self):
        self.assertIn(str(lesezeichen.BESCHREIBUNG_MAXLAENGE), lesezeichen.SKRIPT)

    def test_das_skript_begrenzt_die_zahl_der_bilder(self):
        self.assertIn(f"slice(0,{lesezeichen.BILDER_MAX})", lesezeichen.SKRIPT)


@override_settings(ALLOWED_HOSTS=["localhost", "objektradar.example", "testserver"])
class LesezeichenSeiteTests(TestCase):
    """Zusage 13: die Zieladresse ist aus der Anfrage abgeleitet.

    Zu bezeugen ueber zwei Aufrufe unter verschiedenem `HTTP_HOST` - ein
    einzelner Aufruf saehe bei einer hartkodierten Adresse genauso aus.
    """

    HOST_A = "localhost:8347"
    HOST_B = "objektradar.example"

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _seite(self, host):
        return self.client.get("/lesezeichen/", HTTP_HOST=host)

    def _ziel(self, host):
        # Aus dem Urlconf abgeleitet und nicht als Pfad abgeschrieben: eine
        # Umbenennung der Adresse zoege den Zeugen sonst nicht mit.
        return f"http://{host}{reverse('uebernehmen')}"

    # --- Zusage 13 ---------------------------------------------------------

    def test_die_seite_nennt_die_absolute_zieladresse(self):
        self.assertContains(self._seite(self.HOST_A), self._ziel(self.HOST_A))

    def test_unter_einem_anderen_host_steht_die_andere_adresse(self):
        self.assertContains(self._seite(self.HOST_B), self._ziel(self.HOST_B))

    def test_unter_dem_anderen_host_steht_der_erste_NICHT_mehr(self):
        """Der eigentliche Zeuge gegen eine hartkodierte Adresse.

        Die beiden darueber blieben gruen, wenn die Seite beide Adressen
        naenne - oder wenn eine feste Adresse zufaellig zu einem der beiden
        Hosts passte. Erst die Abwesenheit des anderen Hosts misst, dass die
        Adresse aus der ANFRAGE stammt.
        """
        self.assertNotContains(self._seite(self.HOST_B), self.HOST_A)

    def test_der_ziehbare_link_traegt_das_ganze_skript(self):
        ziel = self._ziel(self.HOST_A)
        self.assertContains(
            self._seite(self.HOST_A),
            f'href="{escape(lesezeichen.skript_fuer(ziel))}"',
        )

    def test_derselbe_text_steht_im_textfeld_zum_kopieren(self):
        """Fuer Browser, in denen das Ziehen nicht geht."""
        ziel = self._ziel(self.HOST_A)
        self.assertContains(
            self._seite(self.HOST_A),
            f">{escape(lesezeichen.skript_fuer(ziel))}</textarea>",
        )

    # --- die Seite ---------------------------------------------------------

    def test_die_seite_verlangt_eine_anmeldung(self):
        self.client.logout()
        self.assertEqual(self._seite(self.HOST_A).status_code, 302)

    def test_die_seite_erklaert_das_hineinziehen(self):
        self.assertContains(self._seite(self.HOST_A), "Lesezeichenleiste")

    def test_die_seite_nennt_die_beschraenkung_auf_den_desktop(self):
        self.assertContains(self._seite(self.HOST_A), "Desktop-Browser")

    def test_der_kopf_verweist_auf_die_lesezeichenseite(self):
        self.assertContains(self.client.get("/"), 'href="/lesezeichen/"')

    def test_der_verweis_steht_auf_jeder_seite(self):
        # Er haengt in `basis.html`, nicht in einer einzelnen Vorlage.
        objekt = Objekt.objects.create(url="https://beispiel.de/1")
        self.assertContains(
            self.client.get(f"/objekt/{objekt.pk}/"), 'href="/lesezeichen/"'
        )

    def test_der_verweis_fehlt_ohne_anmeldung(self):
        # Er fuehrt auf eine Seite, die ohne Anmeldung nichts zeigt.
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("login")), 'href="/lesezeichen/"')


# =========================================================================
# Punkt 5: Filter, Sortierung, Blaettern, Votum-Uebersicht
# =========================================================================


class ListenTestBasis(TestCase):
    """Gemeinsamer Unterbau der Zeugen zu Punkt 5.

    Die Liste verlangt eine Anmeldung; ohne sie messen alle Zeugen unten
    dieselbe Umleitung und niemand merkt es.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="ein-langes-passwort")
        self.client.force_login(self.person)

    def _seite(self, adresse="/"):
        return self.client.get(adresse)

    def _pks(self, adresse="/"):
        """Die IDs der Objekte auf der Seite, in ihrer Reihenfolge."""
        return [o.pk for o in self._seite(adresse).context["objekte"]]

    def _menge(self, adresse="/"):
        return set(self._pks(adresse))

    def _objekt(self, **felder):
        felder.setdefault("url", f"https://x/{Objekt.objects.count() + 1}")
        return Objekt.objects.create(**felder)


class StatusfilterVorbelegungTests(SimpleTestCase):
    """Die Vorbelegung ist abgeleitet, nicht abgeschrieben - und trotzdem gepruefte Zusage.

    Die Ableitung aus `STATUS_AUSGEBLENDET` haelt Liste und `sichtbar()`
    zusammen. Sie koennte aber still etwas anderes ergeben als die vier Werte,
    die die Spezifikation nennt - etwa wenn jemand einen Status ergaenzt.
    Deshalb stehen die vier hier ausgeschrieben.
    """

    def test_die_vorbelegung_traegt_genau_die_vier_werte(self):
        self.assertEqual(
            list(STATUS_VORBELEGUNG),
            [Status.NEU, Status.IN_PRUEFUNG, Status.BESICHTIGUNG, Status.HEISSE_SPUR],
        )

    def test_die_vorbelegung_traegt_keinen_ausgeblendeten_status(self):
        # Der eigentliche Riegel: waeren RAUS oder VOM_MARKT vorbelegt, waere
        # die Zusage aus `02_Datenmodell.md` gebrochen.
        self.assertEqual(set(STATUS_VORBELEGUNG) & set(STATUS_AUSGEBLENDET), set())


class StatusfilterTests(ListenTestBasis):
    """Abschnitt 1: der Statusfilter ist die einzige Stelle, die Status ausblendet."""

    def setUp(self):
        super().setUp()
        self.nach_status = {
            status: self._objekt(url=f"https://x/{status}", status=status, titel=str(status))
            for status in Status
        }

    def _pk(self, status):
        return self.nach_status[status].pk

    # --- ohne Parameter: die Vorbelegung ----------------------------------

    def test_ohne_parameter_erscheint_raus_nicht(self):
        self.assertNotIn(self._pk(Status.RAUS), self._menge())

    def test_ohne_parameter_erscheint_vom_markt_nicht(self):
        self.assertNotIn(self._pk(Status.VOM_MARKT), self._menge())

    def test_ohne_parameter_erscheinen_alle_vier_uebrigen(self):
        self.assertEqual(
            self._menge(),
            {self._pk(s) for s in STATUS_VORBELEGUNG},
        )

    # --- mit Parameter ----------------------------------------------------

    def test_ein_gewaehlter_status_zeigt_ausschliesslich_diesen(self):
        self.assertEqual(self._menge("/?status=raus"), {self._pk(Status.RAUS)})

    def test_zwei_gewaehlte_status_zeigen_beide(self):
        self.assertEqual(
            self._menge("/?status=raus&status=vom_markt"),
            {self._pk(Status.RAUS), self._pk(Status.VOM_MARKT)},
        )

    def test_der_leere_statusfilter_liefert_null_treffer(self):
        """`?status=` heisst "keiner der Werte" - und das ist GEWOLLT.

        Es wird ausdruecklich nicht auf die Vorbelegung zurueckgefallen. Sonst
        liesse sich eine leere Auswahl gar nicht ausdruecken: wer alle
        Kaestchen abwaehlt, saehe wieder die Vorbelegung und haette den
        Eindruck, der Filter sei kaputt.

        Faellt dieser Zeuge, ist das kein Fehler im Zeugen. Dann hat jemand
        den Rueckfall auf die Vorbelegung eingebaut.
        """
        self.assertEqual(self._menge("/?status="), set())

    def test_ein_unbekannter_status_wirft_keinen_fehler(self):
        self.assertEqual(self._seite("/?status=gibtsnicht").status_code, 200)

    def test_ein_unbekannter_status_wird_abgewiesen(self):
        # Abgewiesen heisst: das Feld faellt heraus und es gilt die
        # Vorbelegung. Ein Durchreichen an `filter()` waere ein 500er, ein
        # stilles Uebernehmen eine Auswahl, die niemand getroffen hat.
        self.assertEqual(
            self._menge("/?status=gibtsnicht"),
            {self._pk(s) for s in STATUS_VORBELEGUNG},
        )

    def test_ein_unbekannter_status_zieht_den_gueltigen_daneben_mit_heraus(self):
        """Kein halbes Uebernehmen: die Auswahl gilt als Ganzes oder gar nicht.

        Sonst laege zwischen "raus und Unfug" und "raus" kein Unterschied -
        und ein vertippter Wert veraenderte die Auswahl still.
        """
        self.assertEqual(
            self._menge("/?status=raus&status=gibtsnicht"),
            {self._pk(s) for s in STATUS_VORBELEGUNG},
        )

    # --- der Riegel gegen den zweiten Mechanismus -------------------------

    def _aufrufe_im_abfragepfad(self):
        """Die Namen aller Methodenaufrufe in Liste und Filterformular.

        Ueber den Syntaxbaum, nicht ueber die Zeichenkette: die Begruendung im
        Docstring nennt `sichtbar()` selbst, und eine Textsuche faende sie.
        """
        namen = []
        for gegenstand in (views.ObjektlisteView, forms.ObjektFilterForm):
            baum = ast.parse(textwrap.dedent(inspect.getsource(gegenstand)))
            namen += [
                knoten.func.attr
                for knoten in ast.walk(baum)
                if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute)
            ]
        return namen

    def test_die_liste_ruft_sichtbar_nicht_mehr_auf(self):
        """Strukturtest. Der Verhaltenszeuge daneben kann blind-gruen werden.

        Solange `sichtbar()` noch im Abfragepfad steht, faengt es RAUS und
        VOM_MARKT auch dann ab, wenn die Vorbelegung des Statusfilters
        ausgefallen ist - und `?status=raus` liefe ins Leere, ohne dass ein
        Zeuge sich meldet. Zwei Mechanismen fuer dieselbe Entscheidung
        verdecken sich gegenseitig; einer davon muss weg sein.
        """
        self.assertNotIn("sichtbar", self._aufrufe_im_abfragepfad())

    def test_der_strukturtest_sieht_ueberhaupt_aufrufe(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darueber.

        Faende der Syntaxbaum gar keine Aufrufe - weil `getsource` etwas
        anderes liefert oder die Klasse umbenannt wurde -, waere der Zeuge
        darueber immer gruen und bewachte nichts.
        """
        self.assertIn("mit_qm_preis", self._aufrufe_im_abfragepfad())

    def test_sichtbar_steht_unveraendert_im_modell(self):
        # Entfernt wird die Methode NICHT - andere Aufrufer haengen daran.
        o = self._objekt(url="https://x/probe", status=Status.RAUS)
        self.assertFalse(Objekt.objects.sichtbar().filter(pk=o.pk).exists())


class ListenfilterTests(ListenTestBasis):
    """Abschnitt 2: jeder Filter greift nur bei gesetztem Wert."""

    def test_ein_leerer_filterwert_schraenkt_nicht_ein(self):
        o = self._objekt(titel="Bleibt stehen")
        self.assertIn(o.pk, self._menge("/?land=&objekttyp=&suche=&region="))

    def test_ein_leerer_filterwert_verbirgt_den_bestand_nicht(self):
        """Der eigentliche Schaden: `land=""` traefe jedes Objekt OHNE Land.

        `land`, `portal` und `objekttyp` sind `blank=True, default=""`. Ein
        Filter, der den leeren Wert mitprueft, ist kein leerer Filter - er ist
        ein sehr wirksamer.
        """
        mit = self._objekt(titel="Mit Land", land=Land.ES)
        ohne = self._objekt(titel="Ohne Land")
        self.assertEqual(self._menge("/?land="), {mit.pk, ohne.pk})

    def test_ein_gesetzter_landfilter_verbirgt_objekte_ohne_land(self):
        """GEWOLLT, kein Fehler.

        Wer nach Spanien filtert, will keine Objekte sehen, von denen niemand
        weiss, wo sie stehen. Dieser Zeuge steht hier, damit die Entscheidung
        nicht spaeter fuer einen Fehler gehalten und "repariert" wird.
        """
        mit = self._objekt(titel="Mit Land", land=Land.ES)
        self._objekt(titel="Ohne Land")
        self.assertEqual(self._menge("/?land=ES"), {mit.pk})

    # --- Freitext ---------------------------------------------------------

    def test_der_freitext_trifft_im_titel(self):
        o = self._objekt(titel="Finca bei Ronda")
        self.assertEqual(self._menge("/?suche=finca"), {o.pk})

    def test_der_freitext_trifft_im_ort(self):
        o = self._objekt(ort="Ronda")
        self.assertEqual(self._menge("/?suche=ronda"), {o.pk})

    def test_der_freitext_trifft_in_der_region(self):
        o = self._objekt(region="Serranía de Ronda")
        self.assertEqual(self._menge("/?suche=serran"), {o.pk})

    def test_der_freitext_trifft_in_der_beschreibung(self):
        o = self._objekt(beschreibung="Alter Olivenhain am Hang")
        self.assertEqual(self._menge("/?suche=olivenhain"), {o.pk})

    def test_der_freitext_verbindet_die_vier_spalten_mit_ODER(self):
        """Sonst muesste ein Wort in allen vier Spalten stehen - und traefe nie."""
        im_titel = self._objekt(titel="Ronda")
        im_ort = self._objekt(ort="Ronda")
        self.assertEqual(self._menge("/?suche=Ronda"), {im_titel.pk, im_ort.pk})

    def test_der_freitext_unterscheidet_keine_gross_und_kleinschreibung(self):
        o = self._objekt(titel="Finca bei Ronda")
        self.assertEqual(self._menge("/?suche=FINCA"), {o.pk})

    # --- Grenzen ----------------------------------------------------------

    def test_die_preisuntergrenze_wirkt(self):
        guenstig = self._objekt(aktueller_preis=Decimal("100000"))
        teuer = self._objekt(aktueller_preis=Decimal("300000"))
        self.assertEqual(self._menge("/?preis_von=200000"), {teuer.pk})
        self.assertNotIn(guenstig.pk, self._menge("/?preis_von=200000"))

    def test_die_preisobergrenze_wirkt(self):
        guenstig = self._objekt(aktueller_preis=Decimal("100000"))
        self._objekt(aktueller_preis=Decimal("300000"))
        self.assertEqual(self._menge("/?preis_bis=200000"), {guenstig.pk})

    def test_beide_preisgrenzen_wirken_gemeinsam(self):
        self._objekt(aktueller_preis=Decimal("100000"))
        mitte = self._objekt(aktueller_preis=Decimal("200000"))
        self._objekt(aktueller_preis=Decimal("300000"))
        self.assertEqual(
            self._menge("/?preis_von=150000&preis_bis=250000"), {mitte.pk}
        )

    def test_die_flaechenuntergrenze_wirkt(self):
        klein = self._objekt(wohnflaeche=Decimal("80"))
        gross = self._objekt(wohnflaeche=Decimal("200"))
        self.assertEqual(self._menge("/?flaeche_von=150"), {gross.pk})
        self.assertNotIn(klein.pk, self._menge("/?flaeche_von=150"))

    def test_die_flaechenobergrenze_wirkt(self):
        klein = self._objekt(wohnflaeche=Decimal("80"))
        self._objekt(wohnflaeche=Decimal("200"))
        self.assertEqual(self._menge("/?flaeche_bis=150"), {klein.pk})

    def test_beide_flaechengrenzen_wirken_gemeinsam(self):
        self._objekt(wohnflaeche=Decimal("80"))
        mitte = self._objekt(wohnflaeche=Decimal("140"))
        self._objekt(wohnflaeche=Decimal("200"))
        self.assertEqual(self._menge("/?flaeche_von=100&flaeche_bis=180"), {mitte.pk})

    # --- die uebrigen Einfachfilter ---------------------------------------

    def test_der_portalfilter_trifft(self):
        """`portal` ist neu gegenueber der alten Spezifikation.

        Vorher stand in der Spalte nichts; seit Schritt 2 wird sie aus der URL
        abgeleitet und ein Filter darauf hat erstmals einen Gegenstand.
        """
        idealista = self._objekt(portal=Portal.IDEALISTA)
        self._objekt(portal=Portal.IMMOSCOUT24)
        self.assertEqual(self._menge("/?portal=idealista"), {idealista.pk})

    def test_der_portalfilter_kennt_auch_die_neuen_portale(self):
        """Die drei vom 02.09. sind nicht nur erkennbar, sondern auch filterbar.

        Die Auswahl des Filters wird aus `Portal.choices` abgeleitet - ein
        neues Portal steht dort also von selbst. "Von selbst" ist aber genau
        die Sorte Annahme, die still ausfaellt: waere der Filter irgendwann
        auf eine eigene, abgeschriebene Liste umgestellt, fiele fotocasa
        heraus, ohne dass irgendwo etwas rot wuerde. Die Objekte waeren in der
        Liste, aber nicht mehr zu finden.
        """
        fotocasa = self._objekt(portal=Portal.FOTOCASA)
        self._objekt(portal=Portal.MILANUNCIOS)
        self._objekt(portal=Portal.PISOS)
        self.assertEqual(self._menge("/?portal=fotocasa"), {fotocasa.pk})

    def test_die_portalauswahl_des_filters_steht_vollstaendig_in_der_seite(self):
        """Was sich filtern laesst, muss auch anzuklicken sein."""
        antwort = self._seite()
        for portal in (Portal.FOTOCASA, Portal.MILANUNCIOS, Portal.PISOS):
            with self.subTest(portal=portal):
                self.assertContains(antwort, f'value="{portal.value}"')

    def test_der_objekttypfilter_trifft(self):
        finca = self._objekt(objekttyp=Objekttyp.FINCA)
        self._objekt(objekttyp=Objekttyp.WOHNUNG)
        self.assertEqual(self._menge("/?objekttyp=finca"), {finca.pk})

    def test_der_zustandsfilter_trifft(self):
        kern = self._objekt(zustand=Zustand.KERNSANIERUNG)
        self._objekt(zustand=Zustand.KOSMETISCH)
        self.assertEqual(self._menge("/?zustand=kernsanierung"), {kern.pk})

    def test_der_regionsfilter_trifft_als_teilzeichenkette(self):
        # Freitext, kein `choices`: der Suchraum ist offen und wird nicht
        # vorab festgelegt.
        o = self._objekt(region="Serranía de Ronda")
        self.assertEqual(self._menge("/?region=ronda"), {o.pk})

    def test_zwei_filter_wirken_gemeinsam(self):
        treffer = self._objekt(land=Land.ES, objekttyp=Objekttyp.FINCA)
        self._objekt(land=Land.ES, objekttyp=Objekttyp.WOHNUNG)
        self._objekt(land=Land.DE, objekttyp=Objekttyp.FINCA)
        self.assertEqual(self._menge("/?land=ES&objekttyp=finca"), {treffer.pk})

    def test_ein_ungueltiger_zahlenwert_wirft_keinen_fehler(self):
        self._objekt(aktueller_preis=Decimal("100000"))
        self.assertEqual(self._seite("/?preis_von=viel").status_code, 200)


class TrefferanzeigeTests(ListenTestBasis):
    """Abschnitt 2, letzter Teil: Trefferzahl, Gesamtzahl, Zuruecksetzen."""

    def test_ohne_filter_steht_keine_trefferanzeige(self):
        self._objekt(titel="Eins")
        self.assertFalse(self._seite().context["ist_gefiltert"])

    def test_ein_gesetzter_filter_loest_die_anzeige_aus(self):
        self.assertTrue(self._seite("/?land=ES").context["ist_gefiltert"])

    def test_die_sortierung_allein_loest_die_anzeige_nicht_aus(self):
        self.assertFalse(self._seite("/?sortierung=qm_preis").context["ist_gefiltert"])

    def test_die_seitenzahl_allein_loest_die_anzeige_nicht_aus(self):
        self.assertFalse(self._seite("/?seite=1").context["ist_gefiltert"])

    def test_der_leere_statusfilter_loest_die_anzeige_aus(self):
        """Sonst stuende die leere Liste ohne jede Erklaerung da."""
        self.assertTrue(self._seite("/?status=").context["ist_gefiltert"])

    def test_die_trefferzahl_ist_die_zahl_der_gefilterten(self):
        self._objekt(land=Land.ES)
        self._objekt(land=Land.ES)
        self._objekt(land=Land.DE)
        self.assertEqual(self._seite("/?land=ES").context["trefferzahl"], 2)

    def test_die_gesamtzahl_zaehlt_alle_objekte_ohne_jeden_filter(self):
        """Nicht die Zahl der sichtbaren.

        "2 von 4" beantwortet die Frage "wie viel blende ich gerade aus".
        "2 von 3" - unter Auslassung des verworfenen - beantwortete sie nicht.
        """
        self._objekt(land=Land.ES)
        self._objekt(land=Land.ES)
        self._objekt(land=Land.DE)
        self._objekt(status=Status.RAUS)
        self.assertEqual(self._seite("/?land=ES").context["gesamtzahl"], 4)

    def test_der_zuruecksetzen_verweis_zeigt_auf_die_nackte_adresse(self):
        antwort = self._seite("/?land=ES&status=raus")
        self.assertContains(antwort, 'href="/">Filter zurücksetzen</a>')


class SortierungTests(ListenTestBasis):
    """Abschnitt 3: Positivliste, `nulls_last` in beide Richtungen, `-id` am Ende."""

    def _mit_qm_preis(self):
        """Zwei Objekte mit €/m², eines ohne Wohnflaeche - also mit NULL."""
        teuer = self._objekt(
            url="https://x/teuer", aktueller_preis=Decimal("200000"),
            wohnflaeche=Decimal("100"),
        )
        guenstig = self._objekt(
            url="https://x/guenstig", aktueller_preis=Decimal("100000"),
            wohnflaeche=Decimal("100"),
        )
        ohne = self._objekt(url="https://x/ohne", aktueller_preis=Decimal("150000"))
        return teuer, guenstig, ohne

    # --- die Positivliste -------------------------------------------------

    def test_der_standard_ist_das_zuletzt_eingeworfene_zuerst(self):
        self.assertEqual(views.geprueft_sortierung(""), "-eingestellt_am")

    def test_jeder_schluessel_wird_in_beide_richtungen_angenommen(self):
        for schluessel in views.SORTIERSCHLUESSEL:
            for wert in (schluessel, f"-{schluessel}"):
                with self.subTest(wert=wert):
                    self.assertEqual(views.geprueft_sortierung(wert), wert)

    def test_ein_unbekannter_wert_faellt_auf_den_standard(self):
        self.assertEqual(views.geprueft_sortierung("gibtsnicht"), "-eingestellt_am")

    def test_ein_unbekannter_wert_wirft_keinen_fehler(self):
        self._objekt()
        self.assertEqual(self._seite("/?sortierung=gibtsnicht").status_code, 200)

    def test_ein_feldaehnlicher_wert_wird_nicht_durchgereicht(self):
        """`passwort` sieht aus wie ein Feldname und ist keiner.

        Durchgereicht an `order_by()` waere das ein `FieldError` und damit ein
        500er auf der meistbesuchten Seite des Werkzeugs.
        """
        self._objekt()
        self.assertEqual(self._seite("/?sortierung=passwort").status_code, 200)
        self.assertEqual(views.geprueft_sortierung("passwort"), "-eingestellt_am")

    def test_ein_echter_feldname_ausserhalb_der_liste_wird_nicht_uebernommen(self):
        """Der schaerfere Fall: `titel` IST ein Feld - `order_by("titel")` liefe.

        Ohne Positivliste faende hier niemand einen Fehler: die Seite kaeme
        mit 200 zurueck, nur eben nach einer Spalte sortiert, die nicht zur
        Auswahl steht. Deshalb wird gegen die REIHENFOLGE geprueft und nicht
        gegen den Statuscode.
        """
        zuerst = self._objekt(url="https://x/1", titel="AAA zuerst eingeworfen")
        zuletzt = self._objekt(url="https://x/2", titel="ZZZ zuletzt eingeworfen")
        # Verschiedene Zeitpunkte, nicht gleiche: sonst haengt der Zeuge am
        # zweiten Sortierkriterium mit und faellt auch, wenn `-id` fehlt -
        # dann misst er zwei Zusagen auf einmal und keine davon genau.
        jetzt = timezone.now()
        Objekt.objects.filter(pk=zuerst.pk).update(eingestellt_am=jetzt - timedelta(days=1))
        Objekt.objects.filter(pk=zuletzt.pk).update(eingestellt_am=jetzt)
        # Nach `titel` aufsteigend stuende "AAA" oben; der Standard stellt das
        # zuletzt Eingeworfene nach oben.
        self.assertEqual(self._pks("/?sortierung=titel")[0], zuletzt.pk)

    def test_ein_doppeltes_minus_wird_nicht_durchgereicht(self):
        # Ein blosses `lstrip("-")` haette hier "eingestellt_am" gefunden und
        # den Wert durchgelassen.
        self.assertEqual(views.geprueft_sortierung("--eingestellt_am"), "-eingestellt_am")

    # --- nulls_last, beide Richtungen -------------------------------------

    def test_absteigend_stehen_objekte_ohne_wohnflaeche_am_ende(self):
        teuer, guenstig, ohne = self._mit_qm_preis()
        self.assertEqual(self._pks("/?sortierung=-qm_preis"), [teuer.pk, guenstig.pk, ohne.pk])

    def test_aufsteigend_stehen_objekte_ohne_wohnflaeche_ebenfalls_am_ende(self):
        """Die Richtung, die man vergisst.

        Aufsteigend schiebt PostgreSQL NULL von sich aus ans Ende - ein Zeuge
        nur fuer diese Richtung bliebe auch ohne `nulls_last` gruen. Er steht
        hier trotzdem: faellt spaeter jemand auf die Idee, `nulls_last` nur
        absteigend zu setzen, meldet sich sonst niemand, wenn die Voreinstellung
        der Datenbank einmal eine andere ist.
        """
        teuer, guenstig, ohne = self._mit_qm_preis()
        self.assertEqual(self._pks("/?sortierung=qm_preis"), [guenstig.pk, teuer.pk, ohne.pk])

    def test_absteigend_stehen_objekte_ohne_preis_am_ende(self):
        mit = self._objekt(url="https://x/mit", aktueller_preis=Decimal("100000"))
        ohne = self._objekt(url="https://x/ohne")
        self.assertEqual(self._pks("/?sortierung=-aktueller_preis"), [mit.pk, ohne.pk])

    def test_aufsteigend_stehen_objekte_ohne_preis_am_ende(self):
        mit = self._objekt(url="https://x/mit", aktueller_preis=Decimal("100000"))
        ohne = self._objekt(url="https://x/ohne")
        self.assertEqual(self._pks("/?sortierung=aktueller_preis"), [mit.pk, ohne.pk])

    def test_absteigend_stehen_objekte_ohne_wohnflaechenangabe_am_ende(self):
        mit = self._objekt(url="https://x/mit", wohnflaeche=Decimal("120"))
        ohne = self._objekt(url="https://x/ohne")
        self.assertEqual(self._pks("/?sortierung=-wohnflaeche"), [mit.pk, ohne.pk])

    def test_aufsteigend_stehen_objekte_ohne_wohnflaechenangabe_am_ende(self):
        mit = self._objekt(url="https://x/mit", wohnflaeche=Decimal("120"))
        ohne = self._objekt(url="https://x/ohne")
        self.assertEqual(self._pks("/?sortierung=wohnflaeche"), [mit.pk, ohne.pk])

    # --- die kompilierte Abfrage ------------------------------------------

    def _order_by(self, adresse):
        """Die `ORDER BY`-Klausel der sortierten Abfrage auf `objekte_objekt`.

        Strukturtest neben den Verhaltenstests darueber: liefert die Datenbank
        die gewuenschte Reihenfolge zufaellig geschenkt - etwa ueber einen
        Index oder die Einfuegereihenfolge -, bleiben jene gruen, obwohl das
        Kriterium fehlt.
        """
        with CaptureQueriesContext(connection) as abfragen:
            self._seite(adresse)
        sortierte = [
            q["sql"].lower()
            for q in abfragen.captured_queries
            if "objekte_objekt" in q["sql"] and "order by" in q["sql"].lower()
        ]
        self.assertTrue(sortierte, "keine sortierte Abfrage auf objekte_objekt")
        klausel = sortierte[-1][sortierte[-1].rindex("order by") :]
        return klausel.split(" limit ")[0]

    def test_jede_sortierung_endet_auf_die_id(self):
        self._objekt()
        for wert in ("", "qm_preis", "-qm_preis", "aktueller_preis", "-wohnflaeche"):
            with self.subTest(sortierung=wert):
                self.assertTrue(
                    self._order_by(f"/?sortierung={wert}").rstrip().endswith('"id" desc'),
                    self._order_by(f"/?sortierung={wert}"),
                )

    def test_nulls_last_steht_absteigend_in_der_abfrage(self):
        self._objekt()
        self.assertIn("nulls last", self._order_by("/?sortierung=-qm_preis"))

    def test_nulls_last_steht_aufsteigend_in_der_abfrage(self):
        self._objekt()
        self.assertIn("nulls last", self._order_by("/?sortierung=qm_preis"))

    # --- die Leiste ueber der Liste ---------------------------------------

    def test_jeder_sortierschluessel_hat_eine_beschriftung(self):
        # Sonst wirft der Aufbau der Leiste einen `KeyError` - und zwar erst
        # beim Rendern, nicht beim Ergaenzen des Schluessels.
        for schluessel in views.SORTIERSCHLUESSEL:
            with self.subTest(schluessel=schluessel):
                self.assertIn(schluessel, views.SORTIERBESCHRIFTUNG)

    def _leiste(self, adresse="/"):
        """Die Eintraege der Sortierleiste als (Text, Zieladresse)-Paare."""
        inhalt = self._seite(adresse).content.decode()
        block = inhalt[inhalt.index('<p class="sortieren">') :]
        block = block[: block.index("</p>")]
        return re.findall(r'<a href="([^"]*)"[^>]*>([^<]*)</a>', block)

    def test_die_leiste_traegt_jeden_schluessel_genau_einmal(self):
        """NACHGEZOGEN am 04.09. - hier stand
        `test_die_leiste_traegt_jeden_schluessel_in_beide_richtungen`.

        Er verlangte, dass BEIDE Richtungen je Schluessel als Adresse in der
        Leiste stehen. Genau das ist der Zustand, den diese Runde abschafft:
        acht gleich laute Pfeile, an denen nicht abzulesen war, welche
        Sortierung gerade gilt.

        Die ZUSAGE dahinter - beide Richtungen bleiben erreichbar - ist echt
        und faellt nicht. Sie wird nur anders gehalten und deshalb anders
        gemessen; siehe die beiden Zeugen darunter.
        """
        eintraege = self._leiste()
        self.assertEqual(len(eintraege), len(views.SORTIERSCHLUESSEL))
        for schluessel in views.SORTIERSCHLUESSEL:
            with self.subTest(schluessel=schluessel):
                # Ueber den GEPARSTEN Wert und nicht als Teilzeichenkette:
                # `sortierung=-wohnflaeche` enthaelt "sortierung=wohnflaeche"
                # nicht, und `sortierung=qm_preis` enthielte umgekehrt einen
                # kuerzeren Schluessel mit, gaebe es einen.
                treffer = [
                    z
                    for z, _ in eintraege
                    if [
                        w
                        for w in parse_qs(urlparse(unquote(z)).query).get("sortierung", [])
                        if w.lstrip("-") == schluessel
                    ]
                ]
                self.assertEqual(len(treffer), 1)

    def test_beide_richtungen_bleiben_ueber_zwei_klicks_erreichbar(self):
        """DIE Zusage. Gemessen, indem der Leiste wirklich gefolgt wird.

        Ein Zeuge, der nur die Adressen ANSIEHT, sagt nichts darueber, wohin
        sie fuehren. Hier wird der Eintrag geklickt und danach derselbe
        Eintrag noch einmal: das zweite Ziel muss die Gegenrichtung des ersten
        sein. Damit ist jede Richtung jedes Schluessels in hoechstens zwei
        Klicks erreichbar - und das war die Zusage, nicht "zwei Pfeile".
        """
        for schluessel in views.SORTIERSCHLUESSEL:
            with self.subTest(schluessel=schluessel):
                erste = self._ziel(self._leiste(), schluessel)
                zweite = self._ziel(self._leiste(f"/?sortierung={erste}"), schluessel)
                self.assertEqual({erste, zweite}, {schluessel, f"-{schluessel}"})

    def _ziel(self, eintraege, schluessel):
        """Der Sortierwert, auf den der Eintrag dieses Schluessels zeigt."""
        for adresse, _ in eintraege:
            werte = parse_qs(urlparse(unquote(adresse)).query).get("sortierung", [])
            if werte and werte[0].lstrip("-") == schluessel:
                return werte[0]
        self.fail(f"kein Eintrag fuer {schluessel} in der Leiste")

    def test_nur_die_geltende_sortierung_ist_abgesetzt(self):
        """Genau ein Eintrag traegt `aria-current` - der, der gilt.

        Stuenden alle vier gleich laut da, saehe man nie, was gerade gilt:
        der Zustand, den diese Runde behebt. Stuende `aria-current` an
        keinem, saehe man es auch nicht.
        """
        inhalt = self._seite("/?sortierung=-qm_preis").content.decode()
        block = inhalt[inhalt.index('<p class="sortieren">') :]
        block = block[: block.index("</p>")]
        aktive = re.findall(r'<a [^>]*aria-current="true"[^>]*>([^<]*)</a>', block)
        self.assertEqual(len(aktive), 1)
        self.assertIn(views.SORTIERBESCHRIFTUNG["qm_preis"], aktive[0])

    def test_der_geltende_eintrag_traegt_seine_richtung_als_pfeil(self):
        """Und nur er. Ein Pfeil an einem Eintrag, der nicht gilt, waere eine
        Ansage ueber eine Sortierung, die gerade nicht in Kraft ist."""
        for wert, pfeil in (("qm_preis", views.PFEIL_AUF), ("-qm_preis", views.PFEIL_AB)):
            with self.subTest(sortierung=wert):
                eintraege = self._leiste(f"/?sortierung={wert}")
                mit_pfeil = [t for _, t in eintraege if views.PFEIL_AUF in t or views.PFEIL_AB in t]
                self.assertEqual(len(mit_pfeil), 1)
                self.assertIn(pfeil, mit_pfeil[0])

    def test_die_erstrichtung_ist_je_schluessel_festgelegt(self):
        """Ein Eintrag je Schluessel braucht eine erste Richtung.

        Sie ist nicht fuer alle dieselbe: beim Datum will man das Neueste
        zuerst, beim Preis das Guenstigste. Ein einheitliches "aufsteigend"
        verlangte bei drei von vier Schluesseln einen zweiten Klick, bevor
        ueberhaupt etwas Brauchbares dasteht.

        ABGELEITET aus `SORTIERSCHLUESSEL`: ein spaeter ergaenzter Schluessel
        ohne Erstrichtung liefe beim Bauen der Leiste in einen `KeyError`, und
        zwar erst beim Rendern.
        """
        for schluessel in views.SORTIERSCHLUESSEL:
            with self.subTest(schluessel=schluessel):
                self.assertIn(schluessel, views.SORTIERRICHTUNG)
                self.assertIn(views.SORTIERRICHTUNG[schluessel], ("", "-"))


class BlaetternTests(ListenTestBasis):
    """Abschnitt 4: Seitengroesse als Konstante, Parametererhalt, stiller Rueckfall."""

    def _verweise(self, adresse="/"):
        """Alle Ziele der Seite als geparste Parameterverzeichnisse.

        Ueber `parse_qs` und nicht ueber einen Textvergleich: `{% querystring %}`
        legt die Parameter in der Reihenfolge ab, in der sie ankamen, und ein
        Zeuge auf eine bestimmte Zeichenkette waere von dieser Reihenfolge
        abhaengig statt vom Inhalt.
        """
        inhalt = self._seite(adresse).content.decode()
        return [
            parse_qs(urlparse(htmlwerkzeug.unescape(ziel)).query)
            for ziel in re.findall(r'href="([^"]*)"', inhalt)
        ]

    def _blaetterlink(self, adresse):
        for parameter in self._verweise(adresse):
            if parameter.get("seite") == ["2"]:
                return parameter
        self.fail("kein Blaetterlink auf Seite 2 gefunden")

    def _sortierlink(self, adresse, wert):
        for parameter in self._verweise(adresse):
            if parameter.get("sortierung") == [wert]:
                return parameter
        self.fail(f"kein Sortierlink auf {wert} gefunden")

    def _viele(self, anzahl, **felder):
        return [self._objekt(url=f"https://x/{n}", **felder) for n in range(anzahl)]

    # --- die Seitengroesse ------------------------------------------------

    def test_die_seitengroesse_steht_als_modulkonstante(self):
        self.assertEqual(views.OBJEKTE_JE_SEITE, 50)

    def test_die_seitengroesse_laesst_sich_heruntersetzen(self):
        """Ohne das waere der Stabilitaetszeuge unten unbaubar.

        Ein Klassenattribut `paginate_by = OBJEKTE_JE_SEITE` waere zur
        Importzeit festgeschrieben; ein `mock.patch` auf die Modulkonstante
        liefe dann ins Leere und der Zeuge bliebe still gruen.
        """
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertEqual(len(self._pks()), 2)

    def test_die_liste_blaettert_ueberhaupt(self):
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertTrue(self._seite().context["page_obj"].has_other_pages())

    # --- stiller Rueckfall ------------------------------------------------

    def test_eine_ungueltige_seitenzahl_wirft_keinen_fehler(self):
        self._viele(3)
        self.assertEqual(self._seite("/?seite=abc").status_code, 200)

    def test_eine_nicht_lesbare_seitenzahl_faellt_auf_seite_eins(self):
        """`abc` ist keine Zahl - es gibt keine Stelle, die gemeint sein koennte.

        Anders als eine zu hohe Zahl: die zeigt auf eine Stelle jenseits des
        Endes, und das Ende ist die naechstgelegene Antwort darauf.
        """
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertEqual(self._seite("/?seite=abc").context["page_obj"].number, 1)

    def test_eine_zu_hohe_seitenzahl_faellt_auf_die_letzte_seite(self):
        """Auf die LETZTE Seite, nicht auf die erste.

        Wer auf Seite 8 steht und einen Filter setzt, der auf drei Seiten
        kuerzt, landet auf 3 und sieht dort Treffer - statt sich von Seite 1
        aus erneut vorzublaettern.

        Faellt dieser Zeuge, ist vermutlich wieder `page()` mit einem
        Rueckfall auf 1 an die Stelle von `get_page()` getreten.
        """
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertEqual(self._seite("/?seite=900").context["page_obj"].number, 2)

    def test_eine_negative_seitenzahl_faellt_auf_die_letzte_seite(self):
        """Auch eine negative Zahl liegt ausserhalb des Bereichs.

        Sie ist lesbar - nur eben keine Seite. Damit gilt dieselbe Regel wie
        fuer die zu hohe Zahl, nicht die fuer `abc`.
        """
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertEqual(self._seite("/?seite=-4").context["page_obj"].number, 2)

    def test_die_leere_liste_blaettert_ohne_fehler(self):
        self.assertEqual(self._seite("/?seite=2").status_code, 200)

    # --- Parametererhalt --------------------------------------------------

    def test_ein_blaetterlink_traegt_den_filter_mit(self):
        self._viele(3, land=Land.ES)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertEqual(self._blaetterlink("/?land=ES").get("land"), ["ES"])

    def test_ein_blaetterlink_traegt_die_sortierung_mit(self):
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            parameter = self._blaetterlink("/?sortierung=qm_preis")
        self.assertEqual(parameter.get("sortierung"), ["qm_preis"])

    def test_ein_blaetterlink_traegt_mehrfach_gesetzte_status_vollstaendig_mit(self):
        self._viele(3, status=Status.RAUS)
        self._viele(1, status=Status.VOM_MARKT)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            parameter = self._blaetterlink("/?status=raus&status=vom_markt")
        self.assertEqual(sorted(parameter.get("status", [])), ["raus", "vom_markt"])

    def test_ein_sortierlink_traegt_mehrfach_gesetzte_status_vollstaendig_mit(self):
        """Der Fall, an dem eine handgebaute Hilfsfunktion scheitert.

        Faellt `status` beim Sortieren aus der Adresse, greift die
        Vorbelegung - und die Auswahl der Person ist stillschweigend weg,
        ohne dass irgendetwas danach aussieht.
        """
        self._viele(2, status=Status.RAUS)
        parameter = self._sortierlink("/?status=raus&status=vom_markt", "qm_preis")
        self.assertEqual(sorted(parameter.get("status", [])), ["raus", "vom_markt"])

    def test_ein_sortierlink_traegt_den_filter_mit(self):
        self._viele(2, land=Land.ES)
        # Aufsteigend: das ist die Erstrichtung des Kaufpreises seit dem
        # 04.09. - beim Preis will man das Guenstigste zuerst. Welche
        # Richtung, ist fuer diesen Zeugen gleichgueltig; er misst, dass der
        # Filter mitgeht.
        parameter = self._sortierlink("/?land=ES", "aktueller_preis")
        self.assertEqual(parameter.get("land"), ["ES"])

    def test_ein_sortierlink_setzt_die_seitenzahl_zurueck(self):
        """Eine neue Sortierung faengt vorn an.

        Sonst stuende man nach dem Sortieren auf Seite 4 einer voellig anderen
        Reihenfolge - und haelt sie fuer leer, wenn die neue Liste kuerzer ist.
        """
        self._viele(5)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            parameter = self._sortierlink("/?seite=2", "qm_preis")
        self.assertNotIn("seite", parameter)

    # --- der wichtigste neue Zeuge ----------------------------------------

    def test_beim_blaettern_erscheint_jedes_objekt_genau_einmal(self):
        """Sortierstabilitaet ueber Seitengrenzen hinweg.

        Fuenf Objekte OHNE Wohnflaeche haben alle `qm_preis = NULL` - der
        Sortierwert ist bei allen fuenf derselbe. Ohne zweites Kriterium darf
        PostgreSQL bei jeder Abfrage anders sortieren, und dann erscheint ein
        Objekt auf Seite 1 UND auf Seite 2 - oder auf keiner.

        Gezaehlt werden deshalb nicht die Treffer je Seite, sondern die IDs
        ueber alle Seiten: genau fuenf verschiedene, keine doppelt, keine
        fehlend.

        ACHTUNG - dieser Zeuge bewacht die Zusage NICHT allein. Die Gegenprobe
        hat gezeigt: `-id` aus `reihenfolge()` zu entfernen laesst ihn gruen.
        Der Grund ist genau die Sorte Verdeckung, gegen die die Gegenprobe
        laeuft - die drei `Count`-Annotationen ueber `vota` erzwingen ein
        `GROUP BY objekte_objekt.id`, und PostgreSQL waehlt dafuer hier einen
        Plan, der die Zeilen ohnehin in ID-Folge liefert. Das ist eine
        Eigenschaft des Ausfuehrungsplans, keine Zusage: bei groesseren
        Tabellen darf der Planer eine Hash-Aggregation waehlen, und dann ist
        die Folge wieder unbestimmt.

        Der Zeuge, der `-id` wirklich bewacht, ist deshalb
        `SortierungTests.test_jede_sortierung_endet_auf_die_id` - er liest die
        kompilierte Abfrage und faellt bei jeder der fuenf geprueften
        Sortierungen. Dieser hier bleibt daneben stehen, weil er den
        Sachverhalt beschreibt, um den es geht: kein Objekt doppelt, keines
        fehlend.
        """
        erwartet = {o.pk for o in self._viele(5)}
        gesehen = []
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            for nummer in (1, 2, 3):
                gesehen += self._pks(f"/?sortierung=-qm_preis&seite={nummer}")
        self.assertEqual(len(gesehen), 5)
        self.assertEqual(set(gesehen), erwartet)

    def test_beim_blaettern_nach_eingangsdatum_erscheint_jedes_objekt_genau_einmal(self):
        """Derselbe Zeuge fuer den Standardfall.

        Beim Einwerfen mehrerer Objekte in einer Minute ist `eingestellt_am`
        gleich - ab Schritt 3 der Normalfall, weil der Mail-Parser mehrere
        Objekte am Stueck anlegt.
        """
        erwartet = {o.pk for o in self._viele(5)}
        Objekt.objects.filter(pk__in=erwartet).update(eingestellt_am=timezone.now())
        gesehen = []
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            for nummer in (1, 2, 3):
                gesehen += self._pks(f"/?seite={nummer}")
        self.assertEqual(len(gesehen), 5)
        self.assertEqual(set(gesehen), erwartet)


class VotumUebersichtTests(ListenTestBasis):
    """Abschnitt 5: die Votum-Spalte der Liste."""

    def setUp(self):
        super().setUp()
        # Vier weitere Personen: mit nur einer waere "offen" immer 0 und der
        # interessante Teil der Spalte nie zu sehen.
        self.weitere = [
            Person.objects.create_user(name)
            for name in ("anna", "bernd", "clara", "doris")
        ]
        self.objekt = self._objekt(url="https://x/votum", titel="Zur Abstimmung")

    def _uebersicht(self, adresse="/"):
        return self._seite(adresse).context["objekte"][0].votum_uebersicht

    def _stimmen(self, wertungen):
        for person, wertung in zip([self.person, *self.weitere], wertungen):
            Votum.objects.create(objekt=self.objekt, person=person, wertung=wertung)

    # --- die Zaehlung -----------------------------------------------------

    def test_gemischte_vota_werden_richtig_gezaehlt(self):
        self._stimmen([Wertung.DAFUER, Wertung.DAFUER, Wertung.DAFUER, Wertung.RAUS])
        self.assertEqual(self._uebersicht(), "3 dafür · 1 raus · 1 offen")

    def test_alle_drei_kategorien_erscheinen(self):
        self._stimmen(
            [Wertung.DAFUER, Wertung.ANSCHAUEN, Wertung.RAUS, Wertung.RAUS, Wertung.DAFUER]
        )
        self.assertEqual(self._uebersicht(), "2 dafür · 1 anschauen · 2 raus")

    def test_die_zaehlung_bleibt_am_richtigen_objekt(self):
        """Riegel gegen eine Zaehlung, die ueber alle Objekte laeuft."""
        zweites = self._objekt(url="https://x/zweites", titel="Ohne Stimmen")
        self._stimmen([Wertung.DAFUER])
        nach_pk = {
            o.pk: o.votum_uebersicht for o in self._seite().context["objekte"]
        }
        self.assertEqual(nach_pk[zweites.pk], "noch kein Votum")
        self.assertEqual(nach_pk[self.objekt.pk], "1 dafür · 4 offen")

    # --- "offen" ----------------------------------------------------------

    def test_offen_zaehlt_die_personen_ohne_stimme(self):
        self._stimmen([Wertung.DAFUER])
        self.assertEqual(self._uebersicht(), "1 dafür · 4 offen")

    def test_offen_faellt_weg_wenn_alle_abgestimmt_haben(self):
        self._stimmen([Wertung.DAFUER] * 5)
        self.assertEqual(self._uebersicht(), "5 dafür")

    def test_eine_stillgelegte_person_zaehlt_nicht_mehr_in_offen(self):
        """`is_active=False` heisst: die Person gehoert nicht mehr dazu.

        Sonst stuende in jeder Zeile fuer immer ein "offen", auf das niemand
        mehr antworten kann - und die Spalte behauptete eine Abstimmung, die
        nicht mehr laeuft.
        """
        self._stimmen([Wertung.DAFUER])
        self.assertEqual(self._uebersicht(), "1 dafür · 4 offen")
        Person.objects.filter(pk=self.weitere[0].pk).update(is_active=False)
        self.assertEqual(self._uebersicht(), "1 dafür · 3 offen")

    def test_das_votum_einer_stillgelegten_person_zaehlt_weiter(self):
        """Die Stimme bleibt stehen - abgezogen wird nur bei "offen".

        Andernfalls verschwaende eine abgegebene Wertung mit dem Konto, und
        die Liste zeigte eine Zahl, die nie jemand so gesehen hat.
        """
        self._stimmen([Wertung.DAFUER, Wertung.RAUS])
        Person.objects.filter(pk=self.weitere[0].pk).update(is_active=False)
        self.assertEqual(self._uebersicht(), "1 dafür · 1 raus · 2 offen")

    def test_mehr_stimmen_als_aktive_personen_zeigen_kein_negatives_offen(self):
        self._stimmen([Wertung.DAFUER] * 5)
        Person.objects.filter(pk__in=[p.pk for p in self.weitere]).update(is_active=False)
        self.assertEqual(self._uebersicht(), "5 dafür")

    # --- die Darstellung --------------------------------------------------

    def test_eine_kategorie_mit_null_erscheint_nicht(self):
        """Sonst stuende in jeder Zeile "0 anschauen · 0 raus"."""
        self._stimmen([Wertung.DAFUER])
        self.assertNotIn("0 ", self._uebersicht())

    def test_ohne_jedes_votum_steht_ein_eigener_satz(self):
        self.assertEqual(self._uebersicht(), "noch kein Votum")

    def test_ohne_jedes_votum_steht_dort_nicht_die_zahl_der_offenen(self):
        # "5 offen" saehe aus wie ein Zwischenstand einer laufenden Abstimmung.
        self.assertNotIn("offen", self._uebersicht())

    def test_der_votum_block_steht_in_jeder_zeile(self):
        """NACHGEZOGEN am 04.09.: hier stand `test_die_spalte_steht_in_der_liste`
        und mass `data-spalte="Votum"`. Spalten gibt es nicht mehr.

        Die Zusage ist dieselbe: das Votum steht in JEDER Zeile, nicht nur in
        denen, an denen jemand gestimmt hat. Gemessen an Elementen mit der
        Klasse `votum`, nicht an einer Zeichenkette.
        """
        self._objekt(titel="Ohne Votum")
        self._objekt(titel="Auch ohne")
        antwort = self._seite()
        # Gegen die Zahl der Zeilen gemessen und nicht gegen eine
        # hingeschriebene: der Aufbau der Klasse legt selbst schon Objekte an,
        # und eine feste Zahl hier waere ab dem naechsten davon falsch.
        zeilen = len(antwort.context["objekte"])
        self.assertGreater(zeilen, 1)
        self.assertEqual(len(_klassen_von(antwort, "votum")), zeilen)

    def test_notizen_verfaelschen_die_zaehlung_nicht(self):
        """Zwei Aggregate ueber verschiedene Relationen erzeugten ein Kreuzprodukt.

        Hier wird nur ueber `vota` aggregiert - Notizen liegen daneben und
        duerfen die Zahlen nicht anfassen. Der Zeuge steht da, damit ein
        spaeter ergaenztes `Count("notizen")` sofort auffaellt, statt die
        Vota still zu vervielfachen.
        """
        self._stimmen([Wertung.DAFUER, Wertung.RAUS])
        for text in ("erste Notiz", "zweite Notiz", "dritte Notiz"):
            Notiz.objects.create(objekt=self.objekt, person=self.person, text=text)
        self.assertEqual(self._uebersicht(), "1 dafür · 1 raus · 3 offen")

    def test_die_zaehlung_ueberlebt_filter_und_sortierung(self):
        self._stimmen([Wertung.DAFUER, Wertung.RAUS])
        self.assertEqual(
            self._uebersicht("/?status=neu&sortierung=-qm_preis"),
            "1 dafür · 1 raus · 3 offen",
        )


# =========================================================================
# Portale/Preissenkung, Abschnitt 2: die Markierung in der Liste
# =========================================================================


class PreisaenderungTests(ListenTestBasis):
    """Zusagen aus Abschnitt 2.4: was unter dem Preis steht und was nicht.

    Gemessen wird an der gerenderten Zelle und nicht nur am Kontext: die
    Markierung ist eine Zusage an das Auge, und ein Wert im Kontext, den kein
    Template ausgibt, haelt sie nicht.
    """

    def _mit_verlauf(self, erster, zweiter, **felder):
        """Ein Objekt mit genau zwei Verlaufseintraegen.

        Der erste Preis entsteht beim Anlegen, der zweite ueber
        `preis_setzen()` - das ist der einzige Weg, auf dem sich der Preis
        aendert, und damit derselbe Weg, den die Oberflaeche geht.
        """
        objekt = self._objekt(aktueller_preis=Decimal(erster), **felder)
        objekt.preis_setzen(self.person, Decimal(zweiter))
        return objekt

    def _preiszelle(self):
        """Die Kaufpreisangabe der EINZIGEN Zeile der Liste.

        NACHGEZOGEN am 04.09.: die Liste ist keine Tabelle mehr, und die
        Kaufpreis-Zelle ist der `<div class="zahl">`, dessen Etikett
        "Kaufpreis" lautet. Gesucht wird ueber das ETIKETT und nicht ueber die
        Stellung im Block: eine dazwischengeschobene Angabe verschoebe jede
        Positionszahl, das Wort nicht.

        Die Laengenpruefung ist der Riegel gegen einen vakuum-gruenen Zeugen:
        faende der Ausdruck nichts, verglichen die Zeugen unten leere
        Zeichenketten und blieben gruen - auch wenn die Angabe gar nicht mehr
        da ist.
        """
        inhalt = self._seite().content.decode()
        zellen = [
            block
            for block in re.findall(r'<div class="zahl[^"]*">(.*?)</div>', inhalt, re.S)
            if "Kaufpreis" in block
        ]
        self.assertEqual(len(zellen), 1, "erwartet wird genau eine Kaufpreisangabe")
        return zellen[0]

    def _aenderung_der_objektansicht(self, objekt):
        """Die Preisaenderung, wie die OBJEKTANSICHT sie zeigt.

        Die Liste kuerzt seit dem 04.09. auf Betrag und Tag; der vorherige
        Preis und der Prozentwert stehen in der Objektansicht. Beide kommen
        aus DEMSELBEN `preisaenderung()` - deshalb werden sie hier gemessen
        und nicht doppelt gerechnet.
        """
        inhalt = self.client.get(reverse("objekt", args=[objekt.pk])).content.decode()
        treffer = re.findall(r'<p class="preisaenderung[^"]*">(.*?)</p>', inhalt, re.S)
        self.assertEqual(len(treffer), 1, "erwartet wird genau eine Preisaenderung")
        return " ".join(treffer[0].split())

    # --- Zusage: Senkung ---------------------------------------------------

    def test_bei_einer_senkung_erscheint_die_markierung(self):
        self._mit_verlauf("200000", "180000")
        self.assertIn("preisaenderung", self._preiszelle())

    def test_die_markierung_zeigt_den_betrag_der_senkung(self):
        """Zeuge 11 dieser Runde: eine Senkung erscheint MIT BETRAG.

        Die Liste zeigt seit dem 04.09. den Betrag und nicht mehr den
        durchgestrichenen alten Preis. In der Zahlenspalte steht der aktuelle
        Preis schon darueber - "von 200.000 auf 180.000" waere dieselbe
        Auskunft ein zweites Mal, und die Spalte hat dafuer keine Breite.
        """
        self._mit_verlauf("200000", "180000")
        self.assertIn("20.000 €", self._preiszelle())

    def test_die_markierung_zeigt_die_richtung_als_pfeil(self):
        """Ohne ihn stuende dort "20.000 €" und niemand saehe, wohin.

        Der Pfeil und nicht ein Vorzeichen: er ist auch dann noch zu lesen,
        wenn die Farbe nicht ankommt - und Farbe allein darf keine Bedeutung
        tragen.
        """
        self._mit_verlauf("200000", "180000")
        self.assertIn("↓", self._preiszelle())

    def test_der_vorherige_preis_steht_in_der_objektansicht(self):
        """NACHGEZOGEN: die Zusage ist umgezogen, nicht gefallen.

        Der durchgestrichene alte Preis stand bis zum 04.09. in der Liste. Er
        ist dort weggefallen, weil der Entwurf die Zeile auf Betrag und Tag
        kuerzt - und er ist nicht verschwunden, sondern steht in der
        Objektansicht, die Platz hat. Waere er ganz weg, verloere man die
        Bezugsgroesse: ein Betrag ohne Ausgangspreis sagt nicht, ob 20.000 €
        viel waren.
        """
        objekt = self._mit_verlauf("200000", "180000")
        self.assertIn("<s>200.000 €</s>", self._aenderung_der_objektansicht(objekt))

    def test_die_markierung_zeigt_den_prozentwert(self):
        # 200.000 auf 180.000 sind genau minus zehn Prozent. Auch der
        # Prozentwert steht seit dem 04.09. in der Objektansicht statt in der
        # Liste - aus demselben Grund wie der vorherige Preis.
        objekt = self._mit_verlauf("200000", "180000")
        self.assertIn("-10 %", self._aenderung_der_objektansicht(objekt))

    def test_der_prozentwert_stimmt_auch_wenn_er_nicht_glatt_aufgeht(self):
        """Sonst bliebe der Zeuge darueber gruen, auch wenn gerundet wird.

        249.000 auf 219.000 sind 12,048… Prozent. Eine Anzeige auf ganze
        Prozent zeigte hier "-12 %" und waere um denselben Betrag daneben wie
        eine, die richtig rechnet und falsch rundet.
        """
        objekt = self._mit_verlauf("249000", "219000")
        self.assertIn("-12,0 %", self._aenderung_der_objektansicht(objekt))

    def test_der_betrag_stimmt_auch_wenn_er_nicht_glatt_aufgeht(self):
        """Derselbe Riegel fuer den Betrag, den die Liste zeigt."""
        self._mit_verlauf("249000", "219000")
        self.assertIn("30.000 €", self._preiszelle())

    def test_der_prozentwert_steht_auch_im_kontext(self):
        """Der genaue Wert, ungerundet - das Template zeigt nur eine Fassung davon."""
        self._mit_verlauf("200000", "180000")
        aenderung = self._seite().context["objekte"][0].preisaenderung
        self.assertEqual(aenderung["prozent"], Decimal("-10"))

    def test_die_markierung_zeigt_das_datum_der_aenderung(self):
        """Das Datum des JUENGSTEN Eintrags - der hat die Aenderung gebracht.

        Das Datum des vorletzten waere der Tag, an dem der ALTE Preis erfasst
        wurde. Beide Eintraege heute anzulegen wuerde den Unterschied
        verdecken, deshalb wird der erste zurueckdatiert.
        """
        objekt = self._mit_verlauf("200000", "180000")
        aeltester = objekt.preise.order_by("datum", "id").first()
        Preisverlauf.objects.filter(pk=aeltester.pk).update(
            datum=date(2026, 1, 5)
        )
        zelle = self._preiszelle()
        # Die Liste kuerzt auf Tag und Monat - so steht es im Entwurf.
        self.assertIn(timezone.localdate().strftime("%d.%m."), zelle)
        self.assertNotIn("05.01.", zelle)

    def test_das_volle_datum_bleibt_am_element_erreichbar(self):
        """Der Riegel unter der Kuerzung.

        "seit 03.09." ohne Jahr sagt bei einer Senkung von vor einem Jahr
        etwas Falsches - sie saehe aus wie eine von gestern. Die Kuerzung
        kommt aus dem Entwurf und bleibt; das volle Datum steht im `title` am
        Element und ausserdem in der Objektansicht.
        """
        self._mit_verlauf("200000", "180000")
        zelle = self._preiszelle()
        self.assertIn(timezone.localdate().strftime("%d.%m.%Y"), zelle)
        self.assertIn("title=", zelle)

    def test_die_senkung_traegt_die_signalklasse(self):
        self._mit_verlauf("200000", "180000")
        self.assertIn('class="preisaenderung senkung"', self._preiszelle())

    # --- Zusage: Erhoehung -------------------------------------------------

    def test_bei_einer_erhoehung_erscheint_die_markierung_ebenfalls(self):
        """Eine Erhoehung zu verschweigen waere eine Luecke - sie ist Information."""
        self._mit_verlauf("200000", "216000")
        self.assertIn("preisaenderung", self._preiszelle())

    def test_die_erhoehung_traegt_die_signalklasse_NICHT(self):
        """`--signal` ist der Preissenkung vorbehalten.

        Eine Erhoehung ist Information, aber kein Kaufsignal. Truege sie
        dieselbe laute Farbe, stumpfte sie genau das Signal ab, auf das die
        Liste hinarbeitet.
        """
        self._mit_verlauf("200000", "216000")
        self.assertNotIn("senkung", self._preiszelle())

    def test_die_erhoehung_zeigt_den_vorherigen_preis(self):
        objekt = self._mit_verlauf("200000", "216000")
        self.assertIn("<s>200.000 €</s>", self._aenderung_der_objektansicht(objekt))

    def test_die_erhoehung_traegt_ein_vorzeichen(self):
        """Ohne das stuende dort "8 %" und niemand saehe, in welche Richtung."""
        objekt = self._mit_verlauf("200000", "216000")
        self.assertIn("+8 %", self._aenderung_der_objektansicht(objekt))

    def test_die_erhoehung_traegt_den_pfeil_nach_oben(self):
        """Die Richtung in der Liste - dieselbe Auskunft wie das Vorzeichen in
        der Objektansicht, in der Form, die in eine Zahlenspalte passt."""
        self._mit_verlauf("200000", "216000")
        zelle = self._preiszelle()
        self.assertIn("↑", zelle)
        self.assertNotIn("↓", zelle)

    # --- Zusage: wo NICHTS steht -------------------------------------------

    def test_bei_genau_einem_eintrag_steht_keine_markierung(self):
        """Kein Platzhalter, keine leere Zeile - der Normalfall nach dem Einwurf."""
        self._objekt(aktueller_preis=Decimal("200000"))
        self.assertNotIn("preisaenderung", self._preiszelle())

    def test_bei_genau_einem_eintrag_steht_der_preis_trotzdem_da(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darueber.

        Faellt die ganze Zelle weg, findet `assertNotIn` ebenfalls nichts.
        """
        self._objekt(aktueller_preis=Decimal("200000"))
        self.assertIn("200.000 €", self._preiszelle())

    def test_ohne_preis_steht_keine_markierung(self):
        self._objekt()
        self.assertNotIn("preisaenderung", self._preiszelle())

    def test_ohne_preis_antwortet_die_liste_trotzdem(self):
        """"Kein Fehler" ist die zweite Haelfte der Zusage und ein eigener Zeuge."""
        self._objekt()
        self.assertEqual(self._seite().status_code, 200)

    def test_ein_vorheriger_preis_von_null_erzeugt_keine_markierung(self):
        """Durch Null laesst sich nicht teilen - und ein 500er auf der ganzen
        Liste, ausgeloest von einem einzelnen Datensatz, waere der teuerste
        denkbare Ausgang.

        Ein Kaufpreis von 0 EUR ist ohnehin keine Bezugsgroesse, an der sich
        eine Senkung messen liesse.
        """
        objekt = self._objekt(aktueller_preis=Decimal("0"))
        objekt.preis_setzen(self.person, Decimal("199000"))
        antwort = self._seite()
        self.assertEqual(antwort.status_code, 200)
        self.assertNotIn("preisaenderung", self._preiszelle())

    # --- Zusage: beide Fassungen ------------------------------------------

    def _stylesheet(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(
            encoding="utf-8"
        )

    def _ab_48rem(self):
        """Der Inhalt des Media-Blocks, ueber Klammerzaehlung."""
        quelle = self._stylesheet()
        start = quelle.index("@media (min-width: 48rem)")
        offen = quelle.index("{", start)
        tiefe = 0
        for stelle in range(offen, len(quelle)):
            if quelle[stelle] == "{":
                tiefe += 1
            elif quelle[stelle] == "}":
                tiefe -= 1
                if tiefe == 0:
                    return quelle[offen : stelle + 1]
        raise AssertionError("Der Media-Block ist nicht geschlossen.")

    def test_die_markierung_gilt_fuer_BEIDE_fassungen(self):
        """Ab 48rem UND darunter.

        Das Markup ist seit dem 04.09. ohnehin nur noch eines. Die Zusage ist
        damit genau die: die Regeln der Markierung stehen NICHT im
        Media-Block, sondern darueber, und gelten deshalb in jeder Breite.
        Stuenden sie drin, waere die Senkung am Handy unmarkiert - und das
        Handy ist das Geraet, an dem die Liste unterwegs gelesen wird.
        """
        ausserhalb = self._stylesheet().replace(self._ab_48rem(), "")
        # Mit oeffnender Klammer gesucht: der Name allein steht auch in den
        # Kommentaren der Datei, und ein Zeuge, den ein Kommentar gruen haelt,
        # misst nichts.
        self.assertIn(".preisaenderung {", ausserhalb)
        self.assertIn(".preisaenderung.senkung {", ausserhalb)

    def test_die_senkungsklasse_traegt_die_signalfarbe(self):
        """Die Verbindung zwischen Markup und Farbe.

        Der Zeuge auf `class="… senkung"` weiter oben sagt nur, dass die
        Klasse gesetzt wird. Ohne diesen hier koennte sie auf nichts zeigen
        und die Senkung saehe aus wie jede Nebenangabe.
        """
        quelle = self._stylesheet()
        regel = quelle[quelle.index(".preisaenderung.senkung {") :]
        regel = regel[: regel.index("}")]
        self.assertIn("var(--signal)", regel)

    def test_die_unmarkierte_aenderung_bleibt_gedaempft(self):
        quelle = self._stylesheet()
        regel = quelle[quelle.index(".preisaenderung {") :]
        regel = regel[: regel.index("}")]
        self.assertIn("var(--gedaempft)", regel)



# =========================================================================
# Objektansicht, restliche Seiten, Bilder - Abschnitt 5 der Bauspezifikation
# vom 03.09.
# =========================================================================


class BildParser(HTMLParser):
    """Sammelt jedes `<img>` einer Seite mit allen seinen Attributen.

    Von Hand statt mit einer Bibliothek: das Projekt haengt an Django, psycopg
    und dotenv, und eine vierte Abhaengigkeit fuer einen Zeugen waere ein
    schlechter Tausch.
    """

    def __init__(self):
        super().__init__()
        self.bilder = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.bilder.append(dict(attrs))


def bilder_auf(antwort):
    parser = BildParser()
    parser.feed(antwort.content.decode())
    return parser.bilder


class KlassenParser(HTMLParser):
    """Der vollstaendige `class`-Wert jedes Elements, das eine gesuchte Klasse traegt.

    Nachgetragen am 04.09. In der Votum-Runde war ein Zeuge blind, weil er
    eine ZEICHENKETTE (`class="…"`) suchte statt eines Elements: ein
    erweiterter Klassenname lief an ihm vorbei. Diese Fehlerart ist beim Bau
    der Zeugen dieser Runde ausdruecklich zu vermeiden - also wird auf
    Elemente geprueft.
    """

    def __init__(self, gesucht):
        super().__init__()
        self.gesucht = gesucht
        self.gefunden = []

    def handle_starttag(self, tag, attrs):
        klassen = (dict(attrs).get("class") or "").split()
        if self.gesucht in klassen:
            self.gefunden.append(klassen)


def _klassen_von(antwort, gesucht):
    """Die Klassenlisten aller Elemente, die `gesucht` unter ihren Klassen fuehren."""
    parser = KlassenParser(gesucht)
    parser.feed(antwort.content.decode())
    return parser.gefunden


class ObjektansichtBezeichnungTests(TestCase):
    """Die Bezeichnung der Objektansicht folgt der Rueckfall-Regel.

    Drei getrennte Zeugen, weil es drei getrennte Zweige sind - in einer
    Methode zusammengefasst maesse die zweite nichts mehr, sobald die erste
    faellt. Dieselbe Aufteilung wie bei `ObjektbezeichnungTests`, nur eine
    Ebene hoeher: dort wird `__str__` gemessen, hier die gerenderte Seite.

    Gemessen wird am INHALT der Ueberschrift und nicht an der ganzen Antwort:
    die URL steht auf der Seite ohnehin ein zweites Mal, naemlich im Verweis
    zum Inserat. Ein `assertContains` auf die URL waere damit auch dann gruen,
    wenn in der Ueberschrift etwas voellig anderes stuende.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _ueberschrift(self, objekt):
        antwort = self.client.get(f"/objekt/{objekt.pk}/")
        treffer = re.search(
            r"<h1[^>]*>(.*?)</h1>", antwort.content.decode(), re.S
        )
        self.assertIsNotNone(treffer, "Die Seite hat keine Ueberschrift.")
        return htmlwerkzeug.unescape(treffer.group(1)).strip()

    def test_mit_titel_steht_der_titel(self):
        objekt = Objekt.objects.create(
            url="https://www.idealista.com/inmueble/12345/",
            portal=Portal.IDEALISTA,
            inserats_id="12345",
            titel="Finca bei Ronda",
        )
        self.assertEqual(self._ueberschrift(objekt), "Finca bei Ronda")

    def test_ohne_titel_stehen_portal_und_inserats_id(self):
        objekt = Objekt.objects.create(
            url="https://www.idealista.com/inmueble/12345/",
            portal=Portal.IDEALISTA,
            inserats_id="12345",
        )
        self.assertEqual(self._ueberschrift(objekt), "idealista · 12345")

    def test_ohne_titel_und_ohne_schluessel_steht_die_url(self):
        url = "https://beispiel.de/ein/sehr/langes/inserat/ohne/erkanntes/muster"
        objekt = Objekt.objects.create(url=url)
        self.assertEqual(self._ueberschrift(objekt), url)


#: Die Felder aus Abschnitt 2.4: was am Objekt hinterlegt ist und in der
#: Liste nicht vorkommt. Die Beschriftung, nicht der Feldname - gemessen wird,
#: was auf der Seite steht.
FELDER_DES_DATENBLOCKS = (
    "Objekttyp",
    "Zimmer",
    "Baujahr",
    "Zustand",
    "Region",
    "Portal",
    "Inserats-ID",
    "Quelle",
    "Beschreibung",
)


class ObjektansichtDatenblockTests(TestCase):
    """Der Datenblock zeigt ALLE Felder - auch die leeren.

    Das Objekt dieser Klasse traegt ausser der URL nichts. Damit ist jedes
    Feld des Blocks leer, und der Zeuge misst genau den Fall, um den es geht:
    ein leeres Feld ist die Aufforderung, es zu fuellen. Was gar nicht
    dasteht, faellt niemandem auf und wird nie nachgetragen.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(url="https://beispiel.de/1")

    def _seite(self):
        return self.client.get(f"/objekt/{self.objekt.pk}/")

    def test_das_objekt_dieser_klasse_ist_wirklich_leer(self):
        """Riegel gegen einen Zeugen im Vakuum.

        Truege das Objekt Werte, maesse der Zeuge unten nicht mehr, dass LEERE
        Felder angezeigt werden - sondern nur, dass gefuellte es tun.
        """
        for name in ("objekttyp", "region", "portal", "inserats_id", "beschreibung"):
            with self.subTest(feld=name):
                self.assertEqual(getattr(self.objekt, name), "")
        self.assertIsNone(self.objekt.zimmer)
        self.assertIsNone(self.objekt.baujahr)

    def test_jedes_feld_des_datenblocks_steht_auf_der_seite(self):
        antwort = self._seite()
        for beschriftung in FELDER_DES_DATENBLOCKS:
            with self.subTest(feld=beschriftung):
                self.assertContains(antwort, beschriftung)

    def test_die_zahlen_des_kopfblocks_stehen_ebenfalls_da(self):
        """Abschnitt 2.2: die Vergleichsgrundlage steht oben, nicht in einer
        Tabellenzeile weiter unten - und auch dann, wenn sie fehlt."""
        antwort = self._seite()
        for beschriftung in ("Preis je m²", "Wohnfläche", "Grundstücksgröße",
                             "Wert nach Renovierung"):
            with self.subTest(feld=beschriftung):
                self.assertContains(antwort, beschriftung)


class ObjektansichtVotaTests(TestCase):
    """Wer selbst gestimmt hat, sieht die Vota ALLER anderen - nicht nur eines.

    UMGESCHRIEBEN am 04.09. Bis dahin trug diese Klasse die Zusage "alle sehen
    alle Vota" aus `02_Datenmodell.md`. Die ist gefallen; was bleibt, ist die
    zweite Haelfte: ist einmal freigeschaltet, wird nichts mehr weggelassen.
    Die angemeldete Person stimmt im Aufbau deshalb mit ab - ohne ihr Votum
    maesse jeder Zeuge unten die Verdeckung.

    Gemessen wird mit DREI Personen und nicht mit zweien: bei zwei Vota liesse
    sich "alle ausser meinem" nicht von "eines der anderen" unterscheiden.
    """

    def setUp(self):
        self.person = Person.objects.create_user(
            "steffen", password="lang-genug-123", first_name="Steffen", last_name="P."
        )
        self.anna = Person.objects.create_user("anna", first_name="Anna", last_name="B.")
        self.nico = Person.objects.create_user("nico", first_name="Nico", last_name="C.")
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(url="https://beispiel.de/1", titel="Finca")
        Votum.objects.create(
            objekt=self.objekt, person=self.person, wertung=Wertung.DAFUER
        )
        Votum.objects.create(
            objekt=self.objekt, person=self.anna, wertung=Wertung.ANSCHAUEN,
            begruendung="Dach ansehen",
        )
        Votum.objects.create(
            objekt=self.objekt, person=self.nico, wertung=Wertung.RAUS
        )

    def _seite(self):
        return self.client.get(f"/objekt/{self.objekt.pk}/")

    def test_das_votum_der_zweiten_person_steht_da(self):
        self.assertContains(self._seite(), "Anna B.")

    def test_das_votum_der_dritten_person_steht_ebenfalls_da(self):
        self.assertContains(self._seite(), "Nico C.")

    def test_die_wertung_der_anderen_steht_dabei(self):
        # Ohne sie waere der Name eine Zeile ohne Aussage.
        self.assertContains(self._seite(), "anschauen")

    def test_die_begruendung_der_anderen_steht_dabei(self):
        self.assertContains(self._seite(), "Dach ansehen")

    def test_das_eigene_votum_bleibt_erkennbar(self):
        # Der Riegel dagegen, dass "alle Vota" die eigene Markierung frisst.
        self.assertContains(self._seite(), 'value="dafuer" aria-pressed="true"')


class BilderInDerObjektansichtTests(TestCase):
    """Alle Bilder als einfaches Raster - und ohne Bilder kein `<img>`."""

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(url="https://beispiel.de/1", titel="Finca")

    def _seite(self):
        return self.client.get(f"/objekt/{self.objekt.pk}/")

    def _bilder_anlegen(self, anzahl):
        for nummer in range(anzahl):
            Bild.objects.create(
                objekt=self.objekt,
                url=f"https://bilder.example/{nummer}.jpg",
                reihenfolge=nummer,
            )

    # --- ohne Bilder -------------------------------------------------------

    def test_ohne_bilder_antwortet_die_seite(self):
        self.assertEqual(self._seite().status_code, 200)

    def test_ohne_bilder_steht_kein_einziges_img_auf_der_seite(self):
        """Kein `<img>` mit leerer Adresse - der Browser holte sonst die Seite
        selbst ein zweites Mal ab, und im Layout stuende ein kaputtes Symbol."""
        self.assertEqual(bilder_auf(self._seite()), [])

    # --- mit Bildern -------------------------------------------------------

    def test_die_ansicht_zeigt_ALLE_bilder(self):
        self._bilder_anlegen(4)
        self.assertEqual(len(bilder_auf(self._seite())), 4)

    def test_jedes_bild_traegt_seine_eigene_adresse(self):
        self._bilder_anlegen(3)
        adressen = [bild.get("src") for bild in bilder_auf(self._seite())]
        self.assertEqual(
            adressen,
            [f"https://bilder.example/{n}.jpg" for n in range(3)],
        )

    def test_jedes_bild_wird_verzoegert_geladen(self):
        """`loading="lazy"`: geladen wird, was sichtbar ist. Die Bilder liegen
        beim Portal, und ein Aufruf holt sonst zwanzig fremde Adressen auf
        einmal ab."""
        self._bilder_anlegen(3)
        for bild in bilder_auf(self._seite()):
            with self.subTest(src=bild.get("src")):
                self.assertEqual(bild.get("loading"), "lazy")

    def test_jedes_bild_traegt_die_referrer_einstellung(self):
        """Portale binden Bildadressen an den Verweis. Ohne
        `referrerpolicy="no-referrer"` bleibt die Flaeche leer, und niemand
        koennte sagen warum."""
        self._bilder_anlegen(3)
        for bild in bilder_auf(self._seite()):
            with self.subTest(src=bild.get("src")):
                self.assertEqual(bild.get("referrerpolicy"), "no-referrer")

    def test_keine_galerie_und_kein_vergroessern(self):
        """Kein JavaScript auf dieser Seite - die Ausnahme ist allein das
        Lesezeichen, und das steht auf einer anderen Adresse."""
        self._bilder_anlegen(3)
        inhalt = self._seite().content.decode()
        for verboten in ("<script", "onclick", "onerror"):
            with self.subTest(verboten=verboten):
                self.assertNotIn(verboten, inhalt)


class BilderInDerListeTests(ListenTestBasis):
    """Die Liste zeigt GENAU EIN Bild je Zeile - und ohne Bild eine Flaeche."""

    def _objekt_mit_bildern(self, anzahl, **felder):
        objekt = Objekt.objects.create(url="https://beispiel.de/1", **felder)
        for nummer in range(anzahl):
            Bild.objects.create(
                objekt=objekt,
                url=f"https://bilder.example/{nummer}.jpg",
                reihenfolge=nummer,
            )
        return objekt

    def test_die_liste_zeigt_genau_ein_bild_je_objekt(self):
        self._objekt_mit_bildern(5)
        self.assertEqual(len(bilder_auf(self.client.get("/"))), 1)

    def test_die_liste_zeigt_das_ERSTE_bild(self):
        """Nach `reihenfolge`, nicht nach Zufall. Das erste Bild eines Inserats
        ist die Aussenansicht - das dritte ist das Bad."""
        self._objekt_mit_bildern(5)
        self.assertEqual(
            bilder_auf(self.client.get("/"))[0].get("src"),
            "https://bilder.example/0.jpg",
        )

    def test_das_bild_der_liste_wird_verzoegert_geladen(self):
        self._objekt_mit_bildern(2)
        self.assertEqual(bilder_auf(self.client.get("/"))[0].get("loading"), "lazy")

    def test_das_bild_der_liste_traegt_die_referrer_einstellung(self):
        self._objekt_mit_bildern(2)
        self.assertEqual(
            bilder_auf(self.client.get("/"))[0].get("referrerpolicy"), "no-referrer"
        )

    def test_ohne_bild_steht_kein_img_in_der_zeile(self):
        Objekt.objects.create(url="https://beispiel.de/ohne")
        self.assertEqual(bilder_auf(self.client.get("/")), [])

    def test_ohne_bild_steht_stattdessen_die_ruhige_flaeche(self):
        """Sie haelt die Zeilenhoehe gleich. Eine Liste, in der jede zweite
        Zeile eine andere Hoehe hat, ist unlesbar - und der Zahlenvergleich,
        um den es in der Liste geht, ist dahin.

        NACHGEZOGEN am 04.09.: die Flaeche traegt jetzt `bild platzhalter`.
        Gemessen am ELEMENT und an seinen Klassen, nicht an der Zeichenkette
        `class="platzhalter"` - genau daran ist in der Votum-Runde schon
        einmal ein Zeuge blind vorbeigelaufen, weil ein erweiterter
        Klassenname ihn nicht mehr traf.

        `bild` muss mit dabei sein: daran haengen die Masse, und eine Flaeche
        ohne Masse haelt keine Zeilenhoehe.
        """
        Objekt.objects.create(url="https://beispiel.de/ohne")
        klassen = _klassen_von(self.client.get("/"), "platzhalter")
        self.assertEqual(len(klassen), 1)
        self.assertIn("bild", klassen[0])

    def test_die_flaeche_ohne_bild_wird_nicht_vorgelesen(self):
        """Sie ist Platz, kein Bild und erst recht kein Hinweis.

        Frueher war sie eine Tabellenzelle mit Spaltenbezeichnung; ein
        Screenreader las dort "Bild" und danach nichts. Jetzt steht sie als
        leeres Element in der Zeile, und `aria-hidden` sagt, dass sie
        uebergangen gehoert.
        """
        Objekt.objects.create(url="https://beispiel.de/ohne")
        inhalt = self.client.get("/").content.decode()
        stelle = inhalt.index("platzhalter")
        self.assertIn("aria-hidden", inhalt[stelle - 60 : stelle + 60])


class AbfragezahlMitBildernTests(TestCase):
    """Abschnitt 4.3 - der kritische Punkt dieser Runde.

    Die Bild-URLs liegen in einer eigenen Tabelle. Ein Zugriff auf
    `objekt.bilder` im Template waere bei fuenfzig Zeilen einundfuenfzig
    Abfragen - dasselbe N+1-Muster wie beim Preisverlauf, und in genau der
    Ansicht, die den ganzen Bestand zeigt.

    Aufbau wie bei `test_mehr_preisverlauf_kostet_nicht_mehr_abfragen`: mit
    gesetztem Filter und gesetzter Sortierung (beides veraendert den
    Abfragepfad), die erwartete Zahl beim ersten Durchgang ERMITTELT statt
    hingeschrieben, und beide Messungen auf einer Seite.

    Gemessen wird an Objekten mit Bildern UND Preisverlauf - so, wie sie nach
    einer Uebernahme ueber das Lesezeichen wirklich aussehen. Ein Zeuge, der
    nur eines von beiden aufbaut, laesst offen, ob die beiden Subqueries
    nebeneinander noch halten.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    ADRESSE = "/?status=neu&sortierung=-qm_preis"

    def _anlegen(self, von, bis, bilder=3):
        for nummer in range(von, bis):
            objekt = Objekt.objects.create(
                url=f"https://x/{nummer}", aktueller_preis=Decimal("200000")
            )
            objekt.preis_setzen(self.person, Decimal("180000"))
            for lauf in range(bilder):
                Bild.objects.create(
                    objekt=objekt,
                    url=f"https://bilder.example/{nummer}-{lauf}.jpg",
                    reihenfolge=lauf,
                )

    def test_mehr_bilder_kosten_nicht_mehr_abfragen(self):
        self.client.get(self.ADRESSE)  # Aufwaermen, damit der Verbindungsaufbau
        self._anlegen(0, 5)            # nicht mitzaehlt.
        with CaptureQueriesContext(connection) as mit_fuenf:
            self.client.get(self.ADRESSE)
        self._anlegen(5, views.OBJEKTE_JE_SEITE)
        with self.assertNumQueries(len(mit_fuenf)):
            self.client.get(self.ADRESSE)

    def test_das_bild_ist_bei_dieser_messung_ueberhaupt_da(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darueber.

        Zeigte die Liste gar kein Bild an - weil die Annotation fehlt, das
        Template den Zweig nicht betritt oder der Filter die Objekte
        ausblendet -, waere die Abfragezahl selbstverstaendlich konstant und
        der Zeuge darueber gruen, ohne irgendetwas zu messen.
        """
        self._anlegen(0, 1)
        self.assertEqual(len(bilder_auf(self.client.get(self.ADRESSE))), 1)

    def test_die_messung_laeuft_ueber_alle_fuenfzig_zeilen(self):
        """Zweiter Riegel: blaetterte die Liste nach fuenf Zeilen um, maesse
        der Zeuge oben zweimal dieselbe Seitengroesse."""
        self._anlegen(0, views.OBJEKTE_JE_SEITE)
        self.assertEqual(
            len(self.client.get(self.ADRESSE).context["objekte"]),
            views.OBJEKTE_JE_SEITE,
        )

    def test_die_annotation_kostet_ueberhaupt_keine_eigene_abfrage(self):
        """Die Adresse des ersten Bildes steht in DERSELBEN Anweisung wie die
        Liste. Ein `prefetch_related` waere konstant, aber eine Abfrage mehr -
        und ein zweites Aggregat neben den drei Votumzaehlungen erzeugte ein
        Kreuzprodukt und machte die Votumzahlen still falsch.
        """
        with CaptureQueriesContext(connection) as abfragen:
            list(Objekt.objects.mit_erstem_bild())
        self.assertEqual(len(abfragen.captured_queries), 1)
        # Und die eine Anweisung traegt die Bildtabelle wirklich in sich -
        # sonst waere die Zaehlung oben auch dann gruen, wenn die Annotation
        # gar nicht mehr gezogen wuerde.
        self.assertIn(
            Bild._meta.db_table, abfragen.captured_queries[0]["sql"].lower()
        )

    def test_die_votumzaehlung_bleibt_neben_den_bildern_richtig(self):
        """Der eigentliche Grund fuer die Subquery statt eines JOINs.

        Drei Bilder an einem Objekt mit EINEM Votum: haenge die Bildspalte als
        JOIN an dieselbe Abfrage, vervielfachte jedes Bild jedes Votum, und in
        der Spalte stuende "3 dafür" - still falsch und von aussen nicht als
        Fehler erkennbar.
        """
        objekt = Objekt.objects.create(url="https://x/1")
        Votum.objects.create(objekt=objekt, person=self.person, wertung=Wertung.DAFUER)
        for lauf in range(3):
            Bild.objects.create(
                objekt=objekt, url=f"https://bilder.example/{lauf}.jpg", reihenfolge=lauf
            )
        gelesen = self.client.get("/").context["objekte"][0]
        self.assertEqual(gelesen.votum_dafuer, 1)


class BearbeitenFormularTests(TestCase):
    """Die Felder stehen im Template namentlich in Gruppen. Der Preis dafuer
    ist, dass ein spaeter ergaenztes Feld der Formklasse stumm von der Seite
    fiele - dieser Zeuge ist der Riegel dagegen.

    Gelaufen wird ueber `visible_fields` der Formklasse und NICHT ueber eine
    hier abgeschriebene Liste: eine zweite Liste driftet von der ersten weg,
    und genau das soll der Zeuge ja bemerken.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)
        self.objekt = Objekt.objects.create(url="https://beispiel.de/1", titel="Finca")

    def _seite(self):
        return self.client.get(f"/objekt/{self.objekt.pk}/bearbeiten/")

    def test_das_formular_hat_ueberhaupt_sichtbare_felder(self):
        """Riegel gegen einen Zeugen im Vakuum: ueber eine leere Liste laeuft
        auch der gruendlichste Vergleich gruen durch."""
        self.assertNotEqual(list(ObjektForm().visible_fields()), [])

    def test_jedes_feld_der_formklasse_steht_auf_der_seite(self):
        antwort = self._seite()
        for feld in ObjektForm(instance=self.objekt).visible_fields():
            with self.subTest(feld=feld.name):
                self.assertContains(antwort, f'id="{feld.auto_id}"')

    def test_der_hinweis_am_preisfeld_steht_sichtbar_auf_der_seite(self):
        """Ein leeres Preisfeld heisst "nicht ändern". Diese Regel ist sonst
        nirgends auffindbar - sie steht als `help_text` an der Formklasse und
        muss auch gerendert werden."""
        self.assertContains(self._seite(), "Leer heißt: nicht ändern.")


# =========================================================================
# 03.09.: Loeschen, Statusfarben, Filterblock, Lesezeichen-Hinweis
# =========================================================================


class LoeschbeziehungenTests(SimpleTestCase):
    """Was beim Loeschen eines Objekts mitgeht - und was das Loeschen sperrt.

    Ein STRUKTURZEUGE, kein Verhaltenszeuge: er liest `_meta` und faellt
    deshalb schon beim Schreiben einer Migration, nicht erst, wenn jemand auf
    der Bestaetigungsseite in einen 500er laeuft.

    Die Trennung, um die es geht, ist leicht zu verwechseln: Am Objekt haengen
    PROTECT-Fremdschluessel - `eingestellt_von` und `zuletzt_geaendert_von`.
    Sie zeigen aber VOM Objekt WEG, auf die Person, und schuetzen damit die
    Person vor dem Loeschen, nicht das Objekt. Was das Loeschen eines Objekts
    sperren koennte, sind allein die Beziehungen, die AUF das Objekt zeigen -
    und die stehen alle auf CASCADE.
    """

    def test_keine_beziehung_auf_das_objekt_traegt_PROTECT(self):
        """Die Zusage aus Abschnitt 1: nichts sperrt das Loeschen.

        Faellt dieser Zeuge, ist das Loeschen gebaut, aber nicht mehr
        moeglich - und der Grund ist zu melden, nicht die Beziehung
        umzustellen.
        """
        geschuetzt = [
            f"{b.related_model.__name__}.{b.field.name}"
            for b in Objekt._meta.related_objects
            if b.on_delete is models.PROTECT
        ]
        self.assertEqual(geschuetzt, [])

    def test_jede_beziehung_auf_das_objekt_traegt_CASCADE(self):
        """Umgekehrt gemessen, damit `SET_NULL` nicht durchrutscht.

        Der Zeuge darueber allein bliebe gruen, wenn jemand `PROTECT` durch
        `SET_NULL` ersaetze - und dann bliebe ein verwaistes Votum stehen,
        das zu keinem Objekt mehr gehoert.
        """
        abweichend = [
            f"{b.related_model.__name__}.{b.field.name}={b.on_delete.__name__}"
            for b in Objekt._meta.related_objects
            if b.on_delete is not models.CASCADE
        ]
        self.assertEqual(abweichend, [])

    def test_der_zeuge_sieht_ueberhaupt_beziehungen(self):
        """Riegel gegen einen Zeugen im Vakuum.

        Ohne ihn waeren die beiden darueber auch dann gruen, wenn
        `related_objects` leer zurueckkaeme - etwa nach einer Umbenennung des
        Modells. Fuenf Beziehungen sind es: Bild, Preisverlauf,
        Statusaenderung, Votum, Notiz.
        """
        self.assertEqual(len(Objekt._meta.related_objects), 5)


class LoeschenTests(TestCase):
    """Abschnitt 1: Objekt loeschen, mit Bestaetigungsseite.

    Zwei Stationen auf einer Adresse - GET zeigt, POST loescht. Die Zeugen
    hier halten diesen Schnitt und die Zahlen auf der Bestaetigungsseite.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.andere = Person.objects.create_user("nico", password="lang-genug-123")
        self.client.force_login(self.person)

        self.objekt = Objekt.objects.create(
            url="https://www.idealista.com/inmueble/12345/",
            portal=Portal.IDEALISTA,
            inserats_id="12345",
            titel="Villa am Hang",
            aktueller_preis=Decimal("250000"),
            eingestellt_von=self.person,
        )
        # Ein Objekt mit Anhang an JEDER der fuenf Beziehungen. Ohne das
        # maessen die Zeugen unten das Verschwinden von nichts.
        Votum.objects.create(
            objekt=self.objekt, person=self.person, wertung=Wertung.DAFUER
        )
        Votum.objects.create(
            objekt=self.objekt, person=self.andere, wertung=Wertung.ANSCHAUEN
        )
        Notiz.objects.create(objekt=self.objekt, person=self.andere, text="Dach prüfen.")
        Bild.objects.create(objekt=self.objekt, url="https://bild.example/1.jpg")
        self.objekt.preis_setzen(self.person, Decimal("239000"))
        self.objekt.status_setzen(self.person, Status.IN_PRUEFUNG)

        # Ein zweites Objekt mit eigenem Anhang. Es darf nichts abbekommen.
        self.fremd = Objekt.objects.create(
            url="https://www.idealista.com/inmueble/99999/",
            portal=Portal.IDEALISTA,
            inserats_id="99999",
            titel="Finca",
            aktueller_preis=Decimal("180000"),
        )
        Votum.objects.create(
            objekt=self.fremd, person=self.person, wertung=Wertung.DAFUER
        )
        Notiz.objects.create(objekt=self.fremd, person=self.person, text="Bleibt.")
        Bild.objects.create(objekt=self.fremd, url="https://bild.example/9.jpg")

    def _adresse(self, objekt=None):
        return reverse("objekt_loeschen", args=[(objekt or self.objekt).pk])

    def _hauptteil(self, antwort):
        """Nur der Inhalt aus `<main>`, ohne Kopf, Meldungen und `<title>`.

        NACHGETRAGEN am 03.09. nach der Sabotage-Gegenprobe. Zwei Zeugen
        dieser Klasse massen die GANZE Seite und blieben deshalb gruen,
        obwohl ihre Zusage gebrochen war:

        - `test_die_bestaetigungsseite_nennt_das_objekt` fand die Bezeichnung
          im `<title>`, den `basis.html` aus demselben `{{ objekt }}` baut.
          Die Bezeichnung konnte aus dem Seiteninhalt verschwinden, ohne dass
          sich etwas meldete.
        - `test_die_bestaetigungsseite_traegt_ein_absendendes_formular` fand
          `method="post"` am Abmeldeformular im Kopf. Das Loeschformular
          konnte ganz wegfallen, und der Zeuge blieb gruen.

        Das ist dieselbe Falle in zwei Fassungen: eine Zusage ueber DIESE
        Seite, gemessen an einer Seite, die zur Haelfte aus der
        Basisvorlage besteht. Wer hier einen Zeugen ergaenzt, misst gegen
        diesen Ausschnitt und nicht gegen `antwort`.
        """
        inhalt = antwort.content.decode()
        return inhalt[inhalt.index("<main>") : inhalt.index("</main>")]

    # --- Zeuge: GET loescht nichts und zeigt die Bestaetigung -------------

    def test_die_bestaetigungsseite_antwortet(self):
        self.assertEqual(self.client.get(self._adresse()).status_code, 200)

    def test_der_aufruf_der_loeschadresse_loescht_nichts(self):
        self.client.get(self._adresse())
        self.assertTrue(Objekt.objects.filter(pk=self.objekt.pk).exists())

    def test_auch_der_zweite_aufruf_loescht_nichts(self):
        """Ein Vorauslader ruft dieselbe Adresse mehrfach ab."""
        self.client.get(self._adresse())
        self.client.get(self._adresse())
        self.assertTrue(Objekt.objects.filter(pk=self.objekt.pk).exists())

    def test_der_aufruf_ruehrt_auch_den_anhang_nicht_an(self):
        self.client.get(self._adresse())
        self.assertEqual(self.objekt.vota.count(), 2)

    def test_die_bestaetigungsseite_nennt_das_objekt(self):
        """Im SEITENINHALT, nicht irgendwo in der Antwort.

        Gegen `antwort` gemessen fand dieser Zeuge den Titel im `<title>` und
        blieb gruen, waehrend die Bezeichnung aus der Seite verschwand.
        """
        self.assertIn("Villa am Hang", self._hauptteil(self.client.get(self._adresse())))

    def test_die_bestaetigungsseite_traegt_ein_absendendes_formular(self):
        """Ein POST-Formular AUF DIE LOESCHADRESSE.

        Gegen `antwort` auf `method="post"` gemessen fand dieser Zeuge das
        Abmeldeformular im Seitenkopf - das Loeschformular durfte ganz
        fehlen. Gemessen wird deshalb im Seiteninhalt und am Ziel: ein
        Formular, das woandershin sendet, loescht nichts.
        """
        inhalt = self._hauptteil(self.client.get(self._adresse()))
        self.assertIn('method="post"', inhalt)
        self.assertIn(f'action="{self._adresse()}"', inhalt)

    def test_die_bestaetigungsseite_bietet_einen_weg_zurueck(self):
        """Ein Weg zurueck, der nichts tut - auf die Objektansicht."""
        self.assertContains(
            self.client.get(self._adresse()),
            f'href="{reverse("objekt", args=[self.objekt.pk])}"',
        )

    # --- Zeuge: die Abgrenzung gegen "raus" steht auf der Seite -----------

    def test_die_seite_grenzt_das_loeschen_gegen_raus_ab(self):
        """Ohne diesen Satz wird geloescht, was ausgeblendet gehoert.

        Gemessen am Kernsatz und nicht am ganzen Absatz: die Formulierung darf
        sich aendern, die Ansage nicht.
        """
        self.assertContains(self.client.get(self._adresse()), "nicht „raus“")

    def test_die_seite_nennt_den_zweck_des_loeschens(self):
        self.assertContains(self.client.get(self._adresse()), "nie ein Objekt war")

    # --- Zeuge: die Bestaetigungsseite nennt die tatsaechlichen Zahlen ----

    def _anhangzahlen(self):
        """Die vier Zahlen, wie die Ansicht sie an die Vorlage gibt."""
        antwort = self.client.get(self._adresse())
        return dict(antwort.context["anhang"])

    # `test_die_seite_nennt_die_zahl_der_vota` stand hier bis zum 04.09. Er ist
    # nicht gefallen, weil er stoerte, sondern weil die Zusage, die er mass,
    # ZURUECKGENOMMEN worden ist: `05` dieser Runde nennt die Zahl auf dieser
    # Seite ein Leck. Der Zaehlstand ist verdeckt, solange man nicht selbst
    # abgestimmt hat - und diese Seite ist von der Liste zwei Klicks entfernt
    # und verlangt kein Votum. An seine Stelle treten die drei Zeugen unter
    # "Zeuge 9".

    def test_die_seite_nennt_die_zahl_der_notizen(self):
        self.assertEqual(self._anhangzahlen()["Notizen"], 1)

    def test_die_seite_nennt_die_zahl_der_preiseintraege(self):
        """Zwei: der beim Anlegen und der aus `preis_setzen()`."""
        self.assertEqual(self._anhangzahlen()["Einträge im Preisverlauf"], 2)

    def test_die_seite_nennt_die_zahl_der_statusaenderungen(self):
        self.assertEqual(self._anhangzahlen()["Statusänderungen"], 1)

    def test_keine_der_drei_zahlen_ist_null(self):
        """Die Zusage aus Abschnitt 1.5: die TATSAECHLICHEN Zahlen, nicht null.

        Der Riegel gegen eine Seite, die Nullen ausweist, weil die Zaehlung am
        falschen Objekt oder gar nicht laeuft. Er misst das Gegenteil der
        Zeugen darueber und faellt auch dann, wenn jemand die Zeilen durch
        feste Werte ersetzt.

        DREI seit dem 04.09., nicht mehr vier: die Vota sind aus der Liste
        heraus.
        """
        self.assertNotIn(0, dict(self._anhangzahlen()).values())

    # --- Zeuge 9: die Seite nennt keine Votum-Zahl ------------------------

    def _sichtbarer_text(self, antwort):
        """Der Text aus `<main>`, ohne Markup.

        Attributwerte zaehlen nicht: die Adresse des Loeschformulars traegt
        die Nummer des Objekts, und eine Zahl in einer Adresse ist keine
        Angabe an den Leser. Wer den Quelltext ansieht, findet sie - und
        erfaehrt daraus nichts ueber die Vota.
        """
        return " ".join(re.sub(r"<[^>]*>", " ", self._hauptteil(antwort)).split())

    def _viele_vota(self, anzahl):
        """`anzahl` Vota an diesem Objekt, jedes von einer eigenen Person.

        Eine Zahl, die auf der Seite sonst nirgends vorkommen kann: die
        uebrigen Zahlen sind einstellig, die Objekt-ID ebenfalls. Ein Zeuge,
        der auf "2" prueft, faende die zwei Preiseintraege und waere rot, ohne
        dass etwas verraten waere.
        """
        Votum.objects.filter(objekt=self.objekt).delete()
        for nummer in range(anzahl):
            Votum.objects.create(
                objekt=self.objekt,
                person=Person.objects.create_user(f"stimme{nummer}"),
                wertung=Wertung.DAFUER,
            )

    def test_die_seite_nennt_keine_votum_zahl(self):
        """Zeuge 9 - und die Zusage, die diese Runde herstellt.

        Der Zaehlstand ist verdeckt, solange man an diesem Objekt nicht selbst
        abgestimmt hat. Diese Seite ist von der Liste zwei Klicks entfernt und
        verlangt kein Votum; stuende die Zahl hier, waere die Verdeckung
        umgehbar, und es genuegte, die Seite einmal aufzurufen.

        Gemessen am SEITENINHALT und nicht am Kontext: die Ansicht koennte die
        Zahl weiterhin berechnen, ohne sie auszugeben - das waere zwar
        ueberfluessig, aber keine Verletzung. Umgekehrt gilt das nicht.

        Siebzehn ist im sichtbaren Text sonst nirgends zu finden; die drei
        uebrigen Zahlen sind einstellig.

        Gemessen am SICHTBAREN Text und nicht am Markup: die Adresse des
        Loeschformulars traegt die Nummer des Objekts, und die kann jede sein.
        Genau daran ist dieser Zeuge im vollstaendigen Testlauf einmal rot
        geworden, waehrend er allein gruen blieb - das Objekt hatte dort die
        Nummer 17.
        """
        self._viele_vota(17)
        self.assertNotIn("17", self._sichtbarer_text(self.client.get(self._adresse())))

    def test_die_votumzahl_steht_auch_nicht_im_kontext(self):
        """Zweite Haelfte, an der anderen Stelle gemessen.

        Der Zeuge darueber faende eine Zahl nicht, die im Kontext steht und
        von der Vorlage gerade nicht ausgegeben wird - bis jemand die Vorlage
        anfasst. Der Anhang fuehrt die Vota deshalb gar nicht mehr.
        """
        self._viele_vota(17)
        self.assertNotIn("Vota", dict(self._anhangzahlen()))

    def test_die_seite_sagt_trotzdem_dass_die_vota_mitgehen(self):
        """Der Riegel gegen die naheliegende Uebertreibung.

        Die Vota ganz zu verschweigen waere kein Datenschutz, sondern eine
        Seite, die den schwersten Verlust nicht nennt: sie sind die Arbeit
        von fuenf Leuten an genau diesem Objekt. Nur die ZAHL faellt weg.
        """
        inhalt = self._hauptteil(self.client.get(self._adresse()))
        self.assertIn("Vota", inhalt)

    def test_die_uebrigen_zahlen_stehen_weiterhin_da(self):
        """Notizen, Preiseintraege und Statusaenderungen sind keine Wertung.

        Wer sie zaehlt, erfaehrt nichts ueber die Meinung der anderen. Sie
        pauschal mit wegzunehmen waere die bequeme Loesung und naehme der
        Seite genau das, wofuer sie da ist.
        """
        zahlen = self._anhangzahlen()
        self.assertEqual(
            sorted(zahlen), ["Einträge im Preisverlauf", "Notizen", "Statusänderungen"]
        )

    def test_die_zahlen_stehen_auch_wirklich_auf_der_seite(self):
        """Der Kontext allein sagt nichts - die Vorlage muss ihn ausgeben."""
        antwort = self.client.get(self._adresse())
        self.assertContains(antwort, "Vota")
        self.assertContains(antwort, "Statusänderungen")

    def test_die_zahlen_zaehlen_nur_das_eigene_objekt(self):
        """Ein Kreuzprodukt ueber vier Beziehungen zaehlte zu hoch.

        Genau der Fehler, den vier `Count`-Aggregate in einer Abfrage
        erzeugten: jede Notiz vervielfachte jedes Votum. Bei zwei Vota und
        einer Notiz waeren es dann immer noch 2 - deshalb bekommt das Objekt
        hier eine zweite Notiz, und erst damit trennen 2 und 4 die beiden
        Bauarten.
        """
        Notiz.objects.create(objekt=self.objekt, person=self.person, text="Zweite.")
        # Zwei Notizen und zwei Preiseintraege: ein Kreuzprodukt ueber beide
        # Beziehungen ergaebe vier statt zwei, und erst damit trennen die
        # Zahlen die beiden Bauarten. (Bis zum 04.09. lief derselbe Nachweis
        # ueber Vota und Notizen; die Vota stehen nicht mehr auf der Seite.)
        self.assertEqual(self._anhangzahlen()["Notizen"], 2)
        self.assertEqual(self._anhangzahlen()["Einträge im Preisverlauf"], 2)

    # --- Zeuge: POST loescht das Objekt -----------------------------------

    def test_der_post_loescht_das_objekt(self):
        self.client.post(self._adresse())
        self.assertFalse(Objekt.objects.filter(pk=self.objekt.pk).exists())

    def test_nach_dem_loeschen_wird_umgeleitet(self):
        self.assertEqual(self.client.post(self._adresse()).status_code, 302)

    def test_die_umleitung_fuehrt_auf_die_liste(self):
        antwort = self.client.post(self._adresse())
        self.assertEqual(antwort["Location"], reverse("objektliste"))

    def test_das_loeschen_meldet_sich(self):
        antwort = self.client.post(self._adresse(), follow=True)
        self.assertContains(antwort, "gelöscht")

    def test_die_meldung_nennt_das_geloeschte_objekt(self):
        """Die Bezeichnung wird VOR dem Loeschen gelesen."""
        antwort = self.client.post(self._adresse(), follow=True)
        self.assertContains(antwort, "Villa am Hang")

    def test_das_objekt_ist_danach_in_der_liste_nicht_mehr_auffindbar(self):
        """Gemessen an der ADRESSE, nicht an der Bezeichnung.

        Die Erfolgsmeldung nennt das geloeschte Objekt beim Namen und steht
        auf genau dieser Seite - ein Zeuge auf den Titel waere deshalb rot,
        obwohl alles stimmt. Die Adresse der Objektansicht steht nur dort, wo
        die Liste eine Zeile dafuer hat.
        """
        adresse = reverse("objekt", args=[self.objekt.pk])
        self.client.post(self._adresse())
        antwort = self.client.get(reverse("objektliste"))
        self.assertNotContains(antwort, f'href="{adresse}"')

    def test_die_objektansicht_des_geloeschten_ist_weg(self):
        pk = self.objekt.pk
        self.client.post(self._adresse())
        self.assertEqual(self.client.get(reverse("objekt", args=[pk])).status_code, 404)

    # --- Zeuge: der Anhang verschwindet mit -------------------------------

    def test_die_vota_verschwinden_mit(self):
        self.client.post(self._adresse())
        self.assertEqual(Votum.objects.filter(objekt_id=self.objekt.pk).count(), 0)

    def test_die_notizen_verschwinden_mit(self):
        self.client.post(self._adresse())
        self.assertEqual(Notiz.objects.filter(objekt_id=self.objekt.pk).count(), 0)

    def test_der_preisverlauf_verschwindet_mit(self):
        self.client.post(self._adresse())
        self.assertEqual(Preisverlauf.objects.filter(objekt_id=self.objekt.pk).count(), 0)

    def test_die_statusaenderungen_verschwinden_mit(self):
        self.client.post(self._adresse())
        self.assertEqual(
            self.objekt.statusaenderungen.model.objects.filter(
                objekt_id=self.objekt.pk
            ).count(),
            0,
        )

    def test_die_bilder_verschwinden_mit(self):
        self.client.post(self._adresse())
        self.assertEqual(Bild.objects.filter(objekt_id=self.objekt.pk).count(), 0)

    def test_die_personen_bleiben_erhalten(self):
        """Kein CASCADE in die falsche Richtung.

        Am Votum haengt die Person ueber PROTECT. Loeschte das Objekt die
        Person mit, waere genau die Zusage gebrochen, die `LoeschschutzTests`
        an der anderen Seite haelt.
        """
        self.client.post(self._adresse())
        self.assertEqual(Person.objects.filter(pk=self.andere.pk).count(), 1)

    # --- Zeuge: andere Objekte bleiben unberuehrt -------------------------

    def test_das_andere_objekt_bleibt(self):
        self.client.post(self._adresse())
        self.assertTrue(Objekt.objects.filter(pk=self.fremd.pk).exists())

    def test_das_votum_am_anderen_objekt_bleibt(self):
        self.client.post(self._adresse())
        self.assertEqual(self.fremd.vota.count(), 1)

    def test_die_notiz_am_anderen_objekt_bleibt(self):
        self.client.post(self._adresse())
        self.assertEqual(self.fremd.notizen.count(), 1)

    def test_das_bild_am_anderen_objekt_bleibt(self):
        self.client.post(self._adresse())
        self.assertEqual(self.fremd.bilder.count(), 1)

    def test_der_preisverlauf_des_anderen_objekts_bleibt(self):
        self.client.post(self._adresse())
        self.assertEqual(self.fremd.preise.count(), 1)

    def test_das_andere_objekt_steht_danach_in_der_liste(self):
        self.client.post(self._adresse())
        self.assertContains(self.client.get(reverse("objektliste")), "Finca")

    # --- Zeuge: Zugang ----------------------------------------------------

    def test_ein_unbekanntes_objekt_ergibt_404_statt_serverfehler(self):
        self.assertEqual(self.client.get(reverse("objekt_loeschen", args=[9999])).status_code, 404)

    def test_die_loeschadresse_verlangt_eine_anmeldung(self):
        self.client.logout()
        self.assertEqual(self.client.get(self._adresse()).status_code, 302)

    def test_ohne_anmeldung_loescht_auch_der_post_nichts(self):
        self.client.logout()
        self.client.post(self._adresse())
        self.assertTrue(Objekt.objects.filter(pk=self.objekt.pk).exists())

    def test_jeder_angemeldete_darf_loeschen(self):
        """Kein Rollenkonzept: auch wer das Objekt nicht eingeworfen hat."""
        self.client.force_login(self.andere)
        self.client.post(self._adresse())
        self.assertFalse(Objekt.objects.filter(pk=self.objekt.pk).exists())

    # --- Zeuge: der Einstieg liegt in der Objektansicht, nicht in der Liste

    def test_die_objektansicht_verweist_auf_das_loeschen(self):
        antwort = self.client.get(reverse("objekt", args=[self.objekt.pk]))
        self.assertContains(antwort, f'href="{self._adresse()}"')

    def test_die_liste_verweist_NICHT_auf_das_loeschen(self):
        """In der Liste waere der Einstieg ein Fehlklick neben dem Oeffnen."""
        antwort = self.client.get(reverse("objektliste"))
        self.assertNotContains(antwort, "/loeschen/")

    def test_der_einstieg_ist_kein_knopf(self):
        """Deutlich schwaecher gewichtet als "Bearbeiten".

        `.knopf` traegt die gefuellte Flaeche. Traegt der Loeschverweis sie
        ebenfalls, stehen die haeufigste und die seltenste Handlung der Seite
        gleich laut da - genau der Fehlstand, gegen den diese Runde baut.
        """
        quelle = (settings.BASE_DIR / "templates" / "objekte" / "objekt.html").read_text(
            encoding="utf-8"
        )
        zeile = next(z for z in quelle.splitlines() if "objekt_loeschen" in z)
        self.assertNotIn("knopf", zeile)

    def test_bearbeiten_ist_weiterhin_der_knopf(self):
        """Riegel gegen den Zeugen darueber im Vakuum: er misst nur dann
        einen Unterschied, wenn "Bearbeiten" die gefuellte Form behaelt."""
        antwort = self.client.get(reverse("objekt", args=[self.objekt.pk]))
        self.assertContains(antwort, 'class="knopf"')


class StatusfarbenTests(TestCase):
    """Abschnitt 2: jeder der sechs Status traegt eine eigene Auszeichnung.

    Gemessen wird an zwei Seiten: am Stylesheet, dass es je Status eine eigene
    Farbe gibt und dass keine davon `--signal` oder `--fehler` ist, und an den
    gerenderten Seiten, dass die Klasse dort ueberhaupt ankommt. Eine Farbe im
    Stylesheet, die keine Vorlage traegt, faerbte nichts.

    Wie die Toene AUSSEHEN, entscheidet weiterhin der Blick auf den Bildschirm.
    Diese Zeugen halten nur fest, was sich still zuruecknehmen liesse.
    """

    def _quelle(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8")

    def _variablen(self):
        """Die definierten `--status-…`-Eigenschaften als {Name: Wert}."""
        return dict(
            re.findall(r"(--status-[a-z_]+):\s*([^;]+);", self._quelle())
        )

    # --- Riegel gegen einen Zeugen im Vakuum ------------------------------

    def test_es_gibt_ueberhaupt_statusfarben(self):
        """Ohne ihn liefen die Zeugen unten ueber ein leeres Verzeichnis."""
        self.assertNotEqual(self._variablen(), {})

    # --- Zeuge: eine eigene Auszeichnung je Status ------------------------

    def test_jeder_status_hat_eine_eigene_variable(self):
        """ABGELEITET aus `Status`, nicht abgeschrieben.

        Eine zweite Liste hier driftete von den Auswahllisten weg, und ein
        siebter Status bekaeme still keine Farbe.
        """
        variablen = self._variablen()
        for status in Status:
            with self.subTest(status=status.value):
                self.assertIn(f"--status-{status.value}", variablen)

    def test_jeder_status_hat_eine_eigene_regel(self):
        """Die Variable allein faerbt nichts - es braucht die Regel dazu."""
        quelle = self._quelle()
        for status in Status:
            with self.subTest(status=status.value):
                self.assertIn(
                    f".status-{status.value} ",
                    re.sub(r"\s+", " ", quelle),
                )

    def test_jede_regel_greift_auf_ihre_eigene_variable_zu(self):
        """Sechs Regeln auf dieselbe Variable waeren sechs gleiche Flaechen."""
        quelle = re.sub(r"\s+", " ", self._quelle())
        for status in Status:
            with self.subTest(status=status.value):
                regel = quelle[quelle.index(f".status-{status.value} {{") :]
                regel = regel[: regel.index("}")]
                self.assertIn(f"var(--status-{status.value})", regel)

    def test_die_sechs_farbwerte_sind_paarweise_verschieden(self):
        """Sechs Namen auf denselben Wert waeren keine Unterscheidung.

        Der Zeuge darueber bliebe gruen, wenn alle sechs Variablen auf
        dasselbe Grau stuenden.
        """
        werte = [w.split("/*")[0].strip().lower() for w in self._variablen().values()]
        self.assertEqual(len(set(werte)), len(werte))

    def test_es_sind_genau_sechs(self):
        """Keine siebte Statusfarbe ohne Status dazu."""
        self.assertEqual(len(self._variablen()), len(Status.choices))

    # --- Zeuge: keine davon ist --signal oder --fehler --------------------

    def test_keine_statusregel_traegt_die_signalfarbe(self):
        """`--signal` bleibt der Preissenkung vorbehalten.

        Ein rotes "raus" konkurrierte mit dem wichtigsten Kaufsignal der
        Liste. Gemessen wird an den Regeln UND an den Variablenwerten - eine
        Variable, die den Hexwert von `--signal` wiederholt, umginge eine
        Pruefung, die nur auf `var(--signal)` sieht.
        """
        quelle = re.sub(r"\s+", " ", self._quelle())
        signal = re.search(r"--signal:\s*([^;]+);", quelle).group(1).strip().lower()
        for status in Status:
            with self.subTest(status=status.value):
                regel = quelle[quelle.index(f".status-{status.value} {{") :]
                regel = regel[: regel.index("}")]
                self.assertNotIn("var(--signal)", regel)
                self.assertNotIn(signal, regel.lower())

    def test_keine_statusregel_traegt_die_fehlerfarbe(self):
        quelle = re.sub(r"\s+", " ", self._quelle())
        fehler = re.search(r"--fehler:\s*([^;]+);", quelle).group(1).strip().lower()
        for status in Status:
            with self.subTest(status=status.value):
                regel = quelle[quelle.index(f".status-{status.value} {{") :]
                regel = regel[: regel.index("}")]
                self.assertNotIn("var(--fehler)", regel)
                self.assertNotIn(fehler, regel.lower())

    def test_kein_statuswert_wiederholt_signal_oder_fehler(self):
        """Auch die Variablenwerte selbst nicht."""
        quelle = self._quelle()
        verboten = {
            re.search(r"--signal:\s*([^;]+);", quelle).group(1).strip().lower(),
            re.search(r"--fehler:\s*([^;]+);", quelle).group(1).strip().lower(),
        }
        werte = {w.split("/*")[0].strip().lower() for w in self._variablen().values()}
        self.assertEqual(werte & verboten, set())


class MarkenParser(HTMLParser):
    """Sammelt den VOLLSTAENDIGEN `class`-Wert jedes Elements einer Klasse.

    Nachgetragen am 04.09. Die Zeugen dieser Runde massen die Farbklasse
    vorher als Teilzeichenkette IRGENDWO in der Antwort - und einer von ihnen
    nur fuer einen einzigen der sechs Status. Beides misst nicht, was
    zugesagt ist: dass am Element genau `statusmarke status-<wert>` steht.

    Gemessen wird deshalb am geparsten Element und am ganzen Attribut. Ein
    fehlendes, ein leeres und ein falsch geschriebenes Suffix sind damit alle
    drei rot - `class="statusmarke "` faellt genauso auf wie `class="statusmarke"`.
    """

    def __init__(self, gesucht):
        super().__init__()
        self.gesucht = gesucht
        self.gefunden = []  # [klassenwert, text]
        self._tiefe = None

    def handle_starttag(self, tag, attrs):
        klasse = dict(attrs).get("class") or ""
        if self.gesucht in klasse.split():
            self.gefunden.append([klasse, ""])
            self._tiefe = 0
        elif self._tiefe is not None:
            self._tiefe += 1

    def handle_data(self, daten):
        if self._tiefe is not None and self.gefunden:
            self.gefunden[-1][1] += daten

    def handle_endtag(self, tag):
        if self._tiefe is not None:
            if self._tiefe == 0:
                self._tiefe = None
            else:
                self._tiefe -= 1

    @classmethod
    def lesen(cls, antwort, gesucht):
        parser = cls(gesucht)
        parser.feed(antwort.content.decode())
        return [(k, t.strip()) for k, t in parser.gefunden]


class StatusfarbenInDenSeitenTests(TestCase):
    """Die Farbklasse kommt an den drei Stellen an, an denen ein Status steht.

    Liste (beide Fassungen tragen dasselbe Markup), Objektansicht und die
    Anzeige des aktuellen Status am Statusformular.

    NACHGEZOGEN am 04.09. Die vorigen Zeugen waren blind: der eine suchte
    `status-<wert>` als Teilzeichenkette in der ganzen Antwort - der Treffer
    haette auch aus einem Kommentar oder einem Formularfeld stammen koennen
    und sagte nichts darueber, ob die Klasse AM ELEMENT steht -, der andere
    prueft die Objektansicht nur fuer `heisse_spur` und liess fuenf von sechs
    Status ungemessen. Jetzt wird je Status das vollstaendige `class`-Attribut
    am geparsten Element geprueft, in beiden Ansichten.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _objekt(self, status):
        return Objekt.objects.create(
            url=f"https://x.example/{status}", titel=f"Objekt {status}", status=status
        )

    def _alle_sichtbar(self):
        """Auch `raus` und `vom Markt` - sonst faellt die Haelfte aus der Liste."""
        return self.client.get(
            reverse("objektliste"), {"status": [s.value for s in Status]}
        )

    # --- Riegel gegen einen Zeugen im Vakuum ------------------------------

    def test_der_parser_findet_ueberhaupt_eine_marke(self):
        """Ohne ihn waeren die Zeugen unten auch dann gruen, wenn der Parser
        nie etwas findet - und genau das war der alte Fehlstand."""
        objekt = self._objekt(Status.NEU)
        marken = MarkenParser.lesen(
            self.client.get(reverse("objekt", args=[objekt.pk])), "statusmarke"
        )
        self.assertEqual(len(marken), 1)

    def test_der_parser_meldet_eine_fehlende_klasse(self):
        """Der Riegel auf den Parser selbst: an einem Element OHNE die zweite
        Klasse muss er den nackten Wert liefern, nicht stillschweigend etwas
        ergaenzen."""
        parser = MarkenParser("statusmarke")
        parser.feed('<span class="statusmarke">heiße Spur</span>')
        self.assertEqual([(k, t.strip()) for k, t in parser.gefunden],
                         [("statusmarke", "heiße Spur")])

    # --- Objektansicht: alle sechs ---------------------------------------

    def test_die_objektansicht_traegt_je_status_die_volle_klasse(self):
        """ABGELEITET aus `Status`, und fuer JEDEN der sechs.

        Gemessen wird das ganze Attribut. `class="statusmarke"` ohne Suffix -
        der gemeldete Fehlstand - faellt damit auf, und ein leer gerendertes
        `status-` ebenfalls.
        """
        for status in Status:
            with self.subTest(status=status.value):
                objekt = self._objekt(status)
                marken = MarkenParser.lesen(
                    self.client.get(reverse("objekt", args=[objekt.pk])), "statusmarke"
                )
                self.assertEqual(len(marken), 1)
                klasse, text = marken[0]
                self.assertEqual(klasse, f"statusmarke status-{status.value}")
                self.assertEqual(text, status.label)

    def test_das_statusformular_traegt_je_status_die_volle_klasse(self):
        """Die Auswahl IST die Anzeige des aktuellen Status an diesem Formular."""
        for status in Status:
            with self.subTest(status=status.value):
                objekt = self._objekt(status)
                wahl = MarkenParser.lesen(
                    self.client.get(reverse("objekt", args=[objekt.pk])), "statuswahl"
                )
                self.assertEqual(len(wahl), 1)
                self.assertEqual(wahl[0][0], f"statuswahl status-{status.value}")

    # --- Liste: alle sechs, in einem Durchgang ---------------------------

    def test_die_liste_traegt_je_zeile_die_volle_klasse(self):
        """Alle sechs auf einer Seite, jede Marke mit ihrem eigenen Suffix.

        Ueber die MENGE der Paare gemessen und nicht je Zeile einzeln: so
        faellt auch auf, wenn alle Zeilen dieselbe Klasse tragen - der Fall,
        den eine fest hineingeschriebene Klasse erzeugte.
        """
        for status in Status:
            self._objekt(status)
        marken = MarkenParser.lesen(self._alle_sichtbar(), "statusmarke")
        self.assertEqual(
            sorted(marken),
            sorted((f"statusmarke status-{s.value}", s.label) for s in Status),
        )

    def test_die_liste_zeigt_genau_eine_marke_je_objekt(self):
        for status in Status:
            self._objekt(status)
        self.assertEqual(len(MarkenParser.lesen(self._alle_sichtbar(), "statusmarke")),
                         len(Status.choices))

    def test_die_liste_zeigt_den_status_weiterhin_ausgeschrieben(self):
        """Die Marke ersetzt die Beschriftung nicht, sie umgibt sie."""
        self._objekt(Status.BESICHTIGUNG)
        marken = MarkenParser.lesen(self.client.get(reverse("objektliste")), "statusmarke")
        self.assertEqual([t for _, t in marken], ["Besichtigung"])

    # --- Die Klasse folgt dem gespeicherten Wert -------------------------

    def test_die_marke_folgt_dem_gespeicherten_status(self):
        """Riegel gegen eine fest hineingeschriebene Klasse."""
        objekt = self._objekt(Status.NEU)
        objekt.status_setzen(self.person, Status.VOM_MARKT)
        marken = MarkenParser.lesen(
            self.client.get(reverse("objekt", args=[objekt.pk])), "statusmarke"
        )
        self.assertEqual(marken[0][0], "statusmarke status-vom_markt")

    def test_die_klasse_am_formular_folgt_dem_gespeicherten_status(self):
        objekt = self._objekt(Status.NEU)
        objekt.status_setzen(self.person, Status.BESICHTIGUNG)
        wahl = MarkenParser.lesen(
            self.client.get(reverse("objekt", args=[objekt.pk])), "statuswahl"
        )
        self.assertEqual(wahl[0][0], "statuswahl status-besichtigung")

    # --- Das Feld heisst `status` und liefert den Schluessel -------------

    def test_das_feld_heisst_status_und_liefert_den_gespeicherten_schluessel(self):
        """Die Klasse wird aus `objekt.status` gebaut - nicht aus der
        Beschriftung und nicht aus einem zweiten Feld.

        Gemessen am Modell, damit eine Umbenennung des Feldes hier auffaellt
        und nicht erst als leer gerenderte Klasse im Browser.
        """
        objekt = self._objekt(Status.IN_PRUEFUNG)
        self.assertEqual(objekt.status, "in_pruefung")
        self.assertEqual(objekt.get_status_display(), "in Prüfung")
        self.assertEqual(
            [f.name for f in Objekt._meta.get_fields() if f.name == "status"], ["status"]
        )


class FilterblockNachbesserungTests(TestCase):
    """Abschnitt 3: drei Punkte aus der Sichtpruefung.

    Feldnamen, Reihenfolge und die Klasse des Formulars bleiben unveraendert -
    die letzten drei Zeugen halten genau das fest.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _seite(self):
        return self.client.get(reverse("objektliste"))

    def _stylesheet(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8")

    # --- Punkt 1: die Doppelpunkte fallen weg -----------------------------

    def test_keine_beschriftung_des_filterblocks_traegt_einen_doppelpunkt(self):
        """ABGELEITET aus dem Formular, nicht abgeschrieben.

        Ueber dem Feld ist der Doppelpunkt falsch - er stammt aus der Zeit,
        als die Beschriftung daneben stand. Gemessen an `label_tag`, weil
        genau der ihn setzt.
        """
        formular = self._seite().context["filterform"]
        for name in formular.fields:
            with self.subTest(feld=name):
                # `label_tag` ist eine METHODE. Ohne Klammern misst der Zeuge
                # die Repraesentation des gebundenen Methodenobjekts - darin
                # steht nie ein Doppelpunkt, und er waere immer gruen.
                self.assertNotIn(":", formular[name].label_tag())

    def test_die_beschriftungen_stehen_trotzdem_noch_da(self):
        """Riegel: der Doppelpunkt faellt, die Beschriftung nicht."""
        antwort = self._seite()
        self.assertContains(antwort, ">Suche<")
        self.assertContains(antwort, ">Preis ab (€)<")

    def test_die_formklasse_selbst_bleibt_unangetastet(self):
        """`label_suffix` wird beim BAUEN gesetzt, nicht in der Klasse.

        Ein frisch gebautes Formular ohne Argument traegt den Doppelpunkt
        weiter - genau daran haengt, dass die Klasse unveraendert ist.
        """
        self.assertIn(":", forms.ObjektFilterForm()["suche"].label_tag())

    # --- Punkt 2: das Suchfeld wird begrenzt ------------------------------

    # `test_das_suchfeld_hat_dieselbe_rasterbreite_wie_die_anderen` stand hier
    # bis zum 04.09. Er las die Vorlagenzeile mit `filterform.suche` und
    # pruefte, dass darin nicht "breit" vorkommt. Das ist die Schreibweise
    # EINER Zeile einer Datei und nicht die Zusage; sein Geschwister darunter
    # misst dieselbe Sache am gerenderten HTML und ueberlebt jede Umstellung
    # der Vorlage. Zwei Zeugen auf eine Zusage, von denen einer schlechter
    # misst - der schlechtere faellt.
    #
    # `test_das_stylesheet_fuehrt_keine_sonderbreite_mehr` stand hier
    # ebenfalls und pruefte, dass `.filter .breit` NICHT im Stylesheet steht.
    # Er bewachte keine Zusage an irgendjemanden, sondern die Abwesenheit
    # einer Regel - Aufraeumen von gestern, festgehalten fuer immer. Die
    # Regel ist seit dem 03.09. weg und der ganze Filterblock seit heute neu
    # geschrieben.

    def test_die_gerenderte_feldeinheit_der_suche_traegt_nur_die_grundklasse(self):
        """Am gerenderten HTML gemessen, nicht an der Vorlage.

        Die Suche war einmal doppelt so breit wie jedes andere Feld des
        Blocks; bei drei Spalten nahm sie zwei Drittel der Zeile.
        """
        self.assertNotContains(self._seite(), 'class="feld breit"')

    def test_die_feldeinheit_gibt_es_ueberhaupt_noch(self):
        """Riegel gegen die drei Zeugen darueber im Vakuum."""
        self.assertContains(self._seite(), 'class="feld"')

    # --- Punkt 3: der Statusblock wird sichtbar abgesetzt -----------------

    def _statusfilterregel(self):
        quelle = re.sub(r"\s+", " ", self._stylesheet())
        regel = quelle[quelle.index(".statusfilter {") :]
        return regel[: regel.index("}")]

    def _markenregel(self):
        quelle = re.sub(r"\s+", " ", self._stylesheet())
        regel = quelle[quelle.index(".kaestchen label {") :]
        return regel[: regel.index("}")]

    def test_der_statusblock_ist_sichtbar_abgesetzt(self):
        """Eine erkennbare Abgrenzung - Linie oder eigener Flaechenton.

        Ohne sie steht der Block als volle Zeile zwischen Suche und Land und
        sieht aus wie alles andere; der Filterblock liest sich dann als eine
        Kette gleicher Dinge.
        """
        regel = self._statusfilterregel()
        hat_linie = "border-top" in regel or "border-bottom" in regel
        hat_flaeche = "background" in regel
        self.assertTrue(hat_linie or hat_flaeche, regel)

    def test_die_abgrenzung_bekommt_auch_luft(self):
        """Eine Linie ohne Innenabstand klebt am Inhalt und trennt nichts."""
        self.assertIn("padding:", self._statusfilterregel())

    def test_der_statusblock_bleibt_eine_volle_zeile(self):
        """Sechs Marken passen in keine Rasterspalte - das bleibt so."""
        self.assertIn("grid-column: 1 / -1", self._statusfilterregel())

    # --- Neu am 04.09.: der Status ist eine Reihe von Marken --------------

    def test_der_status_ist_eine_reihe_von_marken(self):
        """`02` von heute: eine Reihe von Marken, keine Kaestchenliste.

        Gemessen an dem, was eine Marke ausmacht und eine Kaestchenzeile
        nicht hat: ein eigener Rand um jedes Wort. Ohne ihn stehen sechs
        Kaestchen mit Woertern nebeneinander, und das ist genau der Zustand
        davor.
        """
        regel = self._markenregel()
        self.assertIn("border:", regel)
        self.assertIn("border-radius:", regel)

    def test_die_angehakte_marke_ist_farblich_abgesetzt(self):
        """Und zwar am ANGEHAKTEN Zustand, nicht an einer zweiten Klasse.

        Eine Klasse muesste die Vorlage setzen, und sie kaeme aus derselben
        Auswahl, die das Kaestchen ohnehin traegt - zwei Traeger fuer
        denselben Zustand. `:has(input:checked)` liest ihn dort, wo er steht.
        """
        quelle = re.sub(r"\s+", " ", self._stylesheet())
        self.assertIn(".kaestchen label:has(input:checked) {", quelle)
        regel = quelle[quelle.index(".kaestchen label:has(input:checked) {") :]
        regel = regel[: regel.index("}")]
        self.assertIn("background:", regel)

    def test_das_kaestchen_bleibt_in_der_marke_sichtbar(self):
        """Der Riegel unter dem Zeugen darueber.

        Die farbliche Absetzung haengt an `:has()`. Faellt das aus - ein
        aelterer Browser, eine strengere Umgebung -, ist der Stand nur noch am
        Kaestchen abzulesen. Ein `display: none` darauf machte den Filter zu
        einem, dessen Stand man raten muss.
        """
        quelle = re.sub(r"\s+", " ", self._stylesheet())
        for treffer in re.finditer(r"([^{}]*input\[type=\"checkbox\"\][^{}]*)\{([^{}]*)\}", quelle):
            with self.subTest(selektor=treffer.group(1).strip()):
                gedraengt = treffer.group(2).replace(" ", "")
                self.assertNotIn("display:none", gedraengt)
                self.assertNotIn("visibility:hidden", gedraengt)

    # --- Was unveraendert bleibt ------------------------------------------

    def test_die_feldnamen_sind_unveraendert(self):
        """Der Schutz sind die bestehenden Tests - und dieser hier."""
        self.assertEqual(
            list(self._seite().context["filterform"].fields),
            [
                "suche",
                "status",
                "land",
                "portal",
                "objekttyp",
                "zustand",
                "preis_von",
                "preis_bis",
                "flaeche_von",
                "flaeche_bis",
                "region",
            ],
        )

    def test_jedes_feld_des_formulars_steht_auch_auf_der_seite(self):
        """NACHGEZOGEN am 04.09. und dabei besser gemacht.

        Hier stand `test_die_reihenfolge_in_der_vorlage_ist_unveraendert`: er
        las die Vorkommen von `filterform.<name>` aus der Vorlage und
        verglich sie mit einer abgeschriebenen Liste, Reihenfolge inklusive.
        Der Entwurf stellt die Reihenfolge um - der Status steht jetzt vorn,
        die Region vor den Zahlenpaaren -, und der Zeuge waere allein deshalb
        rot gewesen, ohne dass jemandem etwas fehlte.

        Die Zusage dahinter ist eine echte und bleibt: JEDES Feld der
        Formklasse ist auf der Seite auch bedienbar. Ein Feld, das aus der
        Vorlage faellt, aber in der Klasse stehen bleibt, filtert nur noch
        ueber die Adresse - `test_die_feldnamen_sind_unveraendert` faende das
        nicht, er misst die Klasse.

        Gemessen am gerenderten HTML und am Namen des Eingabefeldes, nicht an
        der Vorlage: welche Datei die Zeile enthaelt, ist keine Zusage. Die
        Reihenfolge wird ausdruecklich NICHT mehr gemessen - sie ist eine
        Gestaltungsfrage, und ein Zeuge schreibt nicht vor, wie ein Block
        gegliedert ist.
        """
        antwort = self._seite()
        for name in antwort.context["filterform"].fields:
            with self.subTest(feld=name):
                self.assertContains(antwort, f'name="{name}"')

    def test_der_filter_bleibt_ein_GET_formular(self):
        """NACHGEZOGEN: die Klasse hat gewechselt, die Zusage nicht.

        Hier stand `test_die_klasse_des_formulars_ist_unveraendert` und mass
        `<form method="get" class="filter">` als ganze Zeichenkette. Die
        Klasse `filter` sitzt jetzt am `<details>`, das Formular darin heisst
        `filterfelder`. Ein Klassenname ist keine Zusage.

        Die Methode ist eine: `method="get"` ist der Grund, warum ein
        gefilterter Stand eine Adresse hat und sich weitergeben laesst. Ein
        POST-Formular filterte genauso und waere nicht teilbar - und niemand
        merkte es, bis jemand einen Link schicken will.
        """
        inhalt = self._seite().content.decode()
        block = inhalt[inhalt.index('<details class="filter"') :]
        block = block[: block.index("</details>")]
        self.assertIn('method="get"', block)
        self.assertNotIn('method="post"', block)

    def test_der_filterblock_klappt_ohne_skript(self):
        """Kein JavaScript in der Oberflaeche - der Block ist ein `<details>`.

        Ein nachgebauter Umschalter braeuchte ein Skript, und mit ihm die
        Tastaturbedienung und die Ansage fuer Screenreader, die `<details>`
        mitbringt. Gemessen an der ganzen Seite: ein `<script>` irgendwo
        darauf ist der Anfang.
        """
        inhalt = self._seite().content.decode()
        self.assertIn("<details", inhalt)
        self.assertNotIn("<script", inhalt)


class BekannteDomainTests(SimpleTestCase):
    """Die reine Funktion hinter dem Vorschau-Hinweis.

    Sie beantwortet eine ANDERE Frage als `portal_und_id()`: gehoert die
    Domain zu einem bekannten Portal? Eine Suchseite bei idealista liefert
    dort `("", "")` und ist hier trotzdem bekannt. Genau daran haengt, dass
    die Warnung nicht bei jedem unbekannten Pfadmuster mitspringt.
    """

    def test_eine_inseratsadresse_gilt_als_bekannt(self):
        self.assertTrue(
            portale.ist_bekannte_domain("https://www.idealista.com/inmueble/12345/")
        )

    def test_eine_suchseite_desselben_portals_gilt_ebenfalls_als_bekannt(self):
        """DER Fall, an dem sich die Funktion von `portal_und_id()` trennt.

        Der Pfad liefert kein Paar - die Domain ist trotzdem bekannt, und eine
        Warnung waere hier falsch.
        """
        adresse = "https://www.idealista.com/venta-viviendas/alicante/"
        self.assertEqual(portal_und_id(adresse), portale.LEER)
        self.assertTrue(portale.ist_bekannte_domain(adresse))

    def test_jedes_portal_der_tabelle_gilt_als_bekannt(self):
        """ABGELEITET aus `PORTALE`, nicht abgeschrieben."""
        for portal, domains, _ in portale.PORTALE:
            for domain in domains:
                with self.subTest(portal=portal, domain=domain):
                    self.assertTrue(portale.ist_bekannte_domain(f"https://{domain}/x"))

    def test_eine_subdomain_gilt_als_bekannt(self):
        self.assertTrue(portale.ist_bekannte_domain("https://m.idealista.com/x"))

    def test_www_stoert_nicht(self):
        self.assertTrue(portale.ist_bekannte_domain("https://www.fotocasa.es/x"))

    def test_grossschreibung_stoert_nicht(self):
        self.assertTrue(portale.ist_bekannte_domain("https://WWW.PISOS.COM/x"))

    def test_eine_fremde_domain_gilt_als_unbekannt(self):
        self.assertFalse(portale.ist_bekannte_domain("https://www.immowelt.de/expose/x"))

    def test_die_eigene_seite_gilt_als_unbekannt(self):
        """Der Anlass fuer diese Runde: das Lesezeichen loest auf JEDER Seite
        aus, und im Bestand liegt ein Objekt, das die Objektradar-Seite selbst
        erfasst hat."""
        self.assertFalse(portale.ist_bekannte_domain("http://localhost:8347/objekt/3/"))

    def test_eine_domain_die_nur_so_endet_gilt_als_unbekannt(self):
        """`endswith` allein traefe auch `nichtidealista.com`."""
        self.assertFalse(portale.ist_bekannte_domain("https://nichtidealista.com/x"))

    def test_eine_ausgeschiedene_laenderdomain_gilt_als_unbekannt(self):
        """`idealista.it` ist am 02.09. herausgefallen und bleibt draussen."""
        self.assertFalse(portale.ist_bekannte_domain("https://www.idealista.it/x"))

    def test_eine_leere_eingabe_gilt_als_unbekannt(self):
        self.assertFalse(portale.ist_bekannte_domain(""))

    def test_eine_eingabe_ohne_host_gilt_als_unbekannt(self):
        self.assertFalse(portale.ist_bekannte_domain("nur-ein-text"))

    def test_eine_kaputte_ipv6_klammer_wirft_nicht(self):
        """Kein 500er aus einer unlesbaren Adresse."""
        self.assertFalse(portale.ist_bekannte_domain("https://[kaputt/x"))

    def test_das_modul_importiert_weiterhin_kein_django(self):
        """Die Zusage des Moduls gilt auch fuer die neue Funktion."""
        quelle = inspect.getsource(portale)
        self.assertNotIn("django", quelle)


class VorschauHinweisTests(TestCase):
    """Abschnitt 4: der Hinweis bei unbekannter Domain.

    GESPERRT wird nichts. Ein Inserat von einem unbekannten Portal muss
    erfassbar bleiben - das ist ein Vorteil dieses Weges und wird nicht
    aufgegeben. Gewarnt wird stattdessen.
    """

    BEKANNT = "https://www.idealista.com/inmueble/12345/"
    UNBEKANNT = "https://www.immowelt.de/expose/2xk4c5r"

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _vorschau(self, url):
        return self.client.get("/uebernehmen/", {"url": url, "titel": "Etwas"})

    def _absenden(self, url, **abweichungen):
        """Der Rumpf, den der Browser aus der Vorschau zurueckschickt.

        `zustand` steht ausdruecklich drin: es ist das EINZIGE Pflichtfeld des
        Vorschauformulars - die Modellspalte traegt einen Default, aber kein
        `blank=True`, und damit ist das Formularfeld erforderlich. Ohne es
        maessen die Zeugen unten eine abgewiesene Uebernahme und haetten mit
        der Domain nichts zu tun.
        """
        daten = {"url": url, "titel": "Haus am Deich", "zustand": Zustand.UNKLAR}
        daten.update(abweichungen)
        return self.client.post("/uebernehmen/", daten)

    def _meldungen(self, antwort):
        return [str(m) for m in antwort.context["messages"]]

    # --- Zeuge: bekannte Portaldomain -> kein Hinweis ---------------------

    def test_eine_bekannte_domain_erzeugt_keinen_hinweis(self):
        self.assertEqual(self._meldungen(self._vorschau(self.BEKANNT)), [])

    def test_auch_eine_suchseite_des_portals_erzeugt_keinen_hinweis(self):
        """Die Warnung haengt an der DOMAIN, nicht am Pfadmuster.

        Ueber `portal_und_id()` gemessen spraenge sie hier mit an - und eine
        Warnung, die bei bekannten Portalen mitspringt, liest nach der dritten
        niemand mehr.
        """
        adresse = "https://www.idealista.com/venta-viviendas/alicante/"
        self.assertEqual(self._meldungen(self._vorschau(adresse)), [])

    # --- Zeuge: unbekannte Domain -> Hinweis erscheint --------------------

    def test_eine_unbekannte_domain_erzeugt_einen_hinweis(self):
        self.assertEqual(len(self._meldungen(self._vorschau(self.UNBEKANNT))), 1)

    def test_der_hinweis_steht_auf_der_seite(self):
        """Im Kontext allein nuetzt er nichts - er muss gerendert werden."""
        self.assertContains(self._vorschau(self.UNBEKANNT), "keinem bekannten Portal")

    def test_der_hinweis_sagt_dass_die_seite_vermutlich_kein_inserat_ist(self):
        self.assertContains(self._vorschau(self.UNBEKANNT), "vermutlich")

    def test_der_hinweis_sagt_dass_speichern_moeglich_bleibt(self):
        """Wer nur "vermutlich kein Inserat" liest, bricht ab - auch dann,
        wenn das Inserat echt ist und nur das Portal unbekannt."""
        self.assertContains(self._vorschau(self.UNBEKANNT), "trotzdem möglich")

    def test_der_hinweis_erscheint_auch_beim_abgewiesenen_formular(self):
        """Die Vorschau wird an ZWEI Stellen gerendert.

        In `get()` gesetzt, fehlte der Hinweis ausgerechnet beim zweiten Blick
        auf dieselbe fremde Seite.
        """
        antwort = self._absenden(self.UNBEKANNT, baujahr="keine-zahl")
        self.assertContains(antwort, "keinem bekannten Portal")

    # --- Zeuge: der Hinweis ist kein Fehler -------------------------------

    def test_der_hinweis_laeuft_auf_der_warnstufe(self):
        """Die Stufe ZWISCHEN Hinweis und Fehler.

        Bis zum 03.09. lief der Hinweis auf der neutralen Stufe, weil es keine
        dazwischen gab. Jetzt gibt es sie, und er laeuft darueber.

        Gemessen an den Tags, die `basis.html` als Klasse ausgibt - daran
        haengt, welche Regel im Stylesheet greift.
        """
        antwort = self._vorschau(self.UNBEKANNT)
        tags = [m.tags for m in antwort.context["messages"]]
        self.assertEqual(tags, ["warning"])

    def test_der_hinweis_laeuft_NICHT_auf_der_neutralen_stufe(self):
        """Das Gegenstueck, und der eigentliche Riegel dieser Runde.

        `messages.info` traegt den Tag `info`, fuer den es im Stylesheet KEINE
        Regel gibt - die Meldung faellt dann auf die neutrale Vorgabe zurueck
        und sieht aus wie "Das Inserat liegt schon in der Liste". Genau das
        war der Zustand, den diese Runde behebt, und er kaeme durch eine
        einzige geaenderte Zeile in der Ansicht zurueck.
        """
        antwort = self._vorschau(self.UNBEKANNT)
        tags = [m.tags for m in antwort.context["messages"]]
        self.assertNotIn("info", tags)

    def test_die_warnklasse_steht_auch_wirklich_in_der_seite(self):
        """Der Tag allein nuetzt nichts - die Vorlage muss ihn ausgeben."""
        self.assertContains(self._vorschau(self.UNBEKANNT), '<li class="warning"')

    def test_der_hinweis_laeuft_nicht_als_fehler(self):
        """Es ist kein Fehler - `--fehler` waere die falsche Stufe.

        `error` griffe im Stylesheet die Fehlerfarbe ab, und die gehoert einem
        Tippfehler im Formular, nicht einer fremden Domain. Das Speichern
        laeuft weiter, und eine Meldung in Fehlerfarbe behauptete das
        Gegenteil.
        """
        antwort = self._vorschau(self.UNBEKANNT)
        tags = [m.tags for m in antwort.context["messages"]]
        self.assertNotIn("error", tags)

    def test_die_seite_traegt_die_fehlerklasse_nicht(self):
        self.assertNotContains(self._vorschau(self.UNBEKANNT), '<li class="error"')

    # --- Zeuge: Speichern funktioniert trotzdem ---------------------------

    def test_eine_unbekannte_domain_laesst_sich_speichern(self):
        self._absenden(self.UNBEKANNT)
        self.assertTrue(Objekt.objects.filter(url=self.UNBEKANNT).exists())

    def test_das_gespeicherte_objekt_traegt_die_gelesenen_werte(self):
        self._absenden(self.UNBEKANNT)
        self.assertEqual(Objekt.objects.get(url=self.UNBEKANNT).titel, "Haus am Deich")

    def test_nach_dem_speichern_fuehrt_der_weg_auf_die_objektansicht(self):
        antwort = self._absenden(self.UNBEKANNT)
        objekt = Objekt.objects.get(url=self.UNBEKANNT)
        self.assertEqual(antwort["Location"], reverse("objekt", args=[objekt.pk]))

    def test_das_objekt_bleibt_ohne_portal_und_ohne_id(self):
        """Die Warnung aendert an der Erkennung nichts - sie warnt nur."""
        self._absenden(self.UNBEKANNT)
        objekt = Objekt.objects.get(url=self.UNBEKANNT)
        self.assertEqual((objekt.portal, objekt.inserats_id), ("", ""))

    def test_die_vorschau_der_unbekannten_domain_legt_nichts_an(self):
        """Der Hinweis laeuft im GET - und der GET legt weiterhin nichts an."""
        self._vorschau(self.UNBEKANNT)
        self.assertFalse(Objekt.objects.exists())


class WarnstufeTests(TestCase):
    """Die Meldungsstufe zwischen Hinweis und Fehler, am 03.09. dazugekommen.

    Der Tag allein reicht nicht: gaebe es `.meldungen li.warning` im
    Stylesheet nicht, faellt die Meldung auf die neutrale Vorgabe zurueck und
    saehe wieder aus wie ein beliebiger Hinweis - die Ansicht schriebe
    weiterhin `messages.warning`, und NICHTS wuerde sich melden. Genau diese
    stille Rueckkehr in den alten Zustand halten die Zeugen hier.

    Gerechnet wird in CIELAB, nicht nach Augenmass. Zwei Zusagen sind Zahlen
    und nur als Zahlen pruefbar: "deutlich sichtbarer als der neutrale
    Hinweis" ist ein Buntheitsabstand, "klar von --fehler und --signal
    unterschieden" ein Farbabstand.
    """

    #: Ab hier gelten zwei Farben als sicher unterscheidbar. Deutlich ueber
    #: der Wahrnehmungsschwelle (etwa 2,3) und mit Luft nach unten, damit eine
    #: kleine Korrektur am Ton den Zeugen nicht grundlos rot macht.
    ABSTAND = 20.0

    #: Die Warnung muss BUNTER sein als der neutrale Hinweis - daraus kommt
    #: ihre Sichtbarkeit, nicht aus mehr Dunkelheit oder mehr Flaeche.
    BUNTHEITSFAKTOR = 4.0

    def _quelle(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8")

    def _wert(self, name):
        """Der Hexwert einer Eigenschaft aus `:root`."""
        treffer = re.search(rf"{re.escape(name)}:\s*(#[0-9A-Fa-f]{{6}})\s*;", self._quelle())
        self.assertIsNotNone(treffer, f"{name} ist nicht definiert")
        return treffer.group(1)

    def _regel(self, waehler):
        quelle = re.sub(r"\s+", " ", self._quelle())
        self.assertIn(f"{waehler} {{", quelle, f"{waehler} fehlt im Stylesheet")
        rest = quelle[quelle.index(f"{waehler} {{") :]
        return rest[: rest.index("}")]

    # --- Farbrechnung -----------------------------------------------------

    @staticmethod
    def _linear(hexwert):
        werte = [int(hexwert.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in werte]

    @classmethod
    def _lab(cls, hexwert):
        r, g, b = cls._linear(hexwert)
        x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

        def f(t):
            return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

        fx, fy, fz = f(x), f(y), f(z)
        return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))

    @classmethod
    def _abstand(cls, a, b):
        return math.dist(cls._lab(a), cls._lab(b))

    @classmethod
    def _buntheit(cls, hexwert):
        _, a, b = cls._lab(hexwert)
        return math.hypot(a, b)

    @classmethod
    def _helligkeit(cls, hexwert):
        r, g, b = cls._linear(hexwert)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @classmethod
    def _kontrast(cls, a, b):
        ha, hb = cls._helligkeit(a), cls._helligkeit(b)
        return (max(ha, hb) + 0.05) / (min(ha, hb) + 0.05)

    @classmethod
    def _mischung(cls, farbe, anteil, grund):
        """`color-mix(in srgb, farbe anteil%, grund)` - Mischung der kodierten Werte."""
        f = [int(farbe.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
        g = [int(grund.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
        return "#%02X%02X%02X" % tuple(
            round(anteil * fc + (1 - anteil) * gc) for fc, gc in zip(f, g)
        )

    # --- Riegel gegen einen Zeugen im Vakuum ------------------------------

    def test_die_farbrechnung_stimmt_an_einem_bekannten_wert(self):
        """Ohne ihn koennten alle Zeugen unten auf einer kaputten Formel gruen
        bleiben. Schwarz auf Weiss ist 21:1, das ist nachschlagbar."""
        self.assertAlmostEqual(self._kontrast("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(self._lab("#FFFFFF")[0], 100.0, places=1)
        self.assertLess(self._buntheit("#808080"), 1.0)

    # --- Die Stufe gibt es ueberhaupt -------------------------------------

    def test_es_gibt_eine_eigene_warnfarbe(self):
        self.assertIn("--warnung:", self._quelle())

    def test_es_gibt_eine_regel_fuer_die_warnstufe(self):
        """Ohne sie faellt `messages.warning` auf die neutrale Vorgabe zurueck."""
        self.assertIn("var(--warnung)", self._regel(".meldungen li.warning"))

    def test_die_warnregel_faerbt_balken_UND_flaeche(self):
        """Beide, wie bei Erfolg und Fehler - sonst ist es eine halbe Stufe."""
        regel = self._regel(".meldungen li.warning")
        self.assertIn("border-left-color: var(--warnung)", regel)
        self.assertIn("var(--warnung)", regel[regel.index("background") :])

    # --- Gedeckt, aber sichtbarer als der neutrale Hinweis ----------------

    def test_die_warnung_ist_deutlich_bunter_als_der_neutrale_hinweis(self):
        """DIE Zusage dieser Runde: sichtbarer als die neutrale Stufe.

        Gemessen an der Buntheit und nicht an der Helligkeit - genau daher
        soll die Aufmerksamkeit kommen. Ein Ton, der nur dunkler waere, machte
        die Meldung lauter, ohne sie unterscheidbar zu machen.
        """
        warnung = self._wert("--warnung")
        neutral = self._wert("--gedaempft")
        self.assertGreater(
            self._buntheit(warnung),
            self._buntheit(neutral) * self.BUNTHEITSFAKTOR,
            f"--warnung {warnung} ist nicht deutlich bunter als --gedaempft {neutral}",
        )

    def test_die_warnung_bleibt_in_der_helligkeitsklasse_des_balkens(self):
        """Gedeckt heisst: sie wird nicht dunkler, nur farbig.

        Der Balken soll genau so viel wiegen wie der neutrale. Waere die
        Warnung deutlich dunkler, waere sie laut statt gedeckt - und naeher an
        `--fehler`, als sie sein darf.
        """
        warnung = self._wert("--warnung")
        neutral = self._wert("--gedaempft")
        self.assertLess(abs(self._lab(warnung)[0] - self._lab(neutral)[0]), 12.0)

    def test_die_warnung_ist_vom_neutralen_hinweis_unterscheidbar(self):
        self.assertGreater(
            self._abstand(self._wert("--warnung"), self._wert("--gedaempft")),
            self.ABSTAND,
        )

    # --- Klar von --signal und --fehler unterschieden ---------------------

    def test_die_warnung_ist_nicht_die_signalfarbe(self):
        """`--signal` bleibt der Preissenkung vorbehalten.

        Eine orange Warnung konkurrierte mit dem wichtigsten Kaufsignal der
        Liste - genau der Grund, aus dem `--fehler` schon eine eigene Farbe
        bekommen hat.
        """
        warnung = self._wert("--warnung")
        signal = self._wert("--signal")
        self.assertNotEqual(warnung.lower(), signal.lower())
        self.assertGreater(self._abstand(warnung, signal), self.ABSTAND)

    def test_die_warnung_ist_nicht_die_fehlerfarbe(self):
        warnung = self._wert("--warnung")
        fehler = self._wert("--fehler")
        self.assertNotEqual(warnung.lower(), fehler.lower())
        self.assertGreater(self._abstand(warnung, fehler), self.ABSTAND)

    def test_die_warnregel_greift_weder_auf_signal_noch_auf_fehler_zu(self):
        regel = self._regel(".meldungen li.warning")
        self.assertNotIn("var(--signal)", regel)
        self.assertNotIn("var(--fehler)", regel)

    def test_die_fehlerstufe_behaelt_ihre_eigene_farbe(self):
        """Riegel: die neue Stufe darf die bestehende nicht uebernehmen."""
        self.assertIn("var(--fehler)", self._regel(".meldungen li.error"))
        self.assertNotIn("var(--warnung)", self._regel(".meldungen li.error"))

    # --- Der Kontrast reicht ----------------------------------------------

    def test_die_schrift_bleibt_auf_der_warnflaeche_lesbar(self):
        """Ein blasser Text auf blasser Flaeche waere schlechter als keine Farbe.

        Die Toenung wird aus derselben Angabe gerechnet, die im Stylesheet
        steht - der Anteil wird also mitgeprueft und nicht angenommen.
        """
        regel = self._regel(".meldungen li.warning")
        anteil = re.search(r"var\(--warnung\)\s*(\d+)%", regel)
        self.assertIsNotNone(anteil, f"kein Mischungsanteil in: {regel}")
        grund = self._mischung(
            self._wert("--warnung"), int(anteil.group(1)) / 100, self._wert("--flaeche")
        )
        self.assertGreater(self._kontrast(grund, self._wert("--text")), 4.5)


class ListenzeilenParser(HTMLParser):
    """Je Zeile der Objektliste: Verweise, Besuchsmarken und deren Umgebung.

    NACHGEZOGEN am 04.09. auf das neue Markup. Bis dahin las er `<tbody>`,
    `<tr>` und `data-spalte`; die Liste ist jetzt eine `<ul class="liste">`
    aus `<li class="objekt">`.

    Eingegrenzt auf die LISTE und nicht auf die ganze Antwort. In diesem
    Projekt haben zwei Zeugen ihre Zeichenkette schon einmal im Basis-Template
    gefunden und dort gemessen, wo die Zusage gar nicht steht. Eine Marke wird
    deshalb nur gezaehlt, wenn sie IN einer Listenzeile sitzt.

    `marke_in` haelt fest, in welchem umgebenden Block die Marke steht. Daran
    haengt die Zusage, dass der Punkt keine eigene Stelle in der Zeile kostet,
    sondern in der Titelzeile vor dem Titel sitzt.

    Geprueft wird auf ELEMENTE und auf die Klassenliste, nicht auf
    `class="…"`-Zeichenketten: ein erweiterter Klassenname lief in der
    Votum-Runde schon einmal an einem Zeugen vorbei.
    """

    def __init__(self):
        super().__init__()
        self.zeilen = []
        self._in_liste = 0
        self._zeile = None
        self._bloecke = []

    @staticmethod
    def _klassen(attrs):
        return (attrs.get("class") or "").split()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        klassen = self._klassen(attrs)
        if tag == "ul" and "liste" in klassen:
            self._in_liste += 1
            return
        if not self._in_liste:
            return
        if tag == "li" and "objekt" in klassen:
            self._zeile = {
                "verweise": [], "marken": [], "marke_in": [], "klassen": klassen,
            }
            self.zeilen.append(self._zeile)
            self._bloecke = ["objekt"]
            return
        if self._zeile is None:
            return
        # Leere Elemente schliessen sich in HTML nicht; nur die, die wirklich
        # einen Block aufmachen, kommen auf den Stapel.
        if tag not in ("img", "input", "br"):
            self._bloecke.append(klassen[0] if klassen else tag)
        if tag == "a" and attrs.get("href"):
            self._zeile["verweise"].append(attrs["href"])
        elif "besuchsmarke" in klassen:
            self._zeile["marken"].append(attrs.get("title"))
            # Der umgebende Block, nicht das Element selbst.
            self._zeile["marke_in"].append(self._bloecke[-2] if len(self._bloecke) > 1 else None)

    def handle_endtag(self, tag):
        if tag == "ul":
            self._in_liste = max(0, self._in_liste - 1)
            self._zeile = None
        elif tag == "li":
            self._zeile = None
            self._bloecke = []
        elif self._zeile is not None and tag not in ("img", "input", "br") and self._bloecke:
            self._bloecke.pop()

    @classmethod
    def lesen(cls, antwort):
        parser = cls()
        parser.feed(antwort.content.decode())
        return parser


class BesuchsmarkeTests(TestCase):
    """Punkt 6: "seit deinem letzten Besuch" an der Objektliste.

    Markiert ist ein Objekt, wenn NACH der Schwelle jemand ANDERES etwas daran
    getan hat. Die Schwelle ist `besuch_davor` und kommt ueber
    `request.neu_seit` aus der Middleware.

    Aufbau der Zeugen: die Schwelle liegt eine Stunde zurueck, und `_objekt()`
    legt Objekte an, deren Einstellzeitpunkt VOR der Schwelle liegt. Ohne das
    truege jedes frisch angelegte Objekt schon wegen seiner eigenen Anlage
    eine Marke - und jeder Zeuge unten waere gruen, ohne die Bewegung zu
    messen, um die es ihm geht.

    Gemessen wird an der gerenderten Liste (`_markiert()`), nicht am Queryset.
    Wo zusaetzlich die Annotation selbst gemeint ist, steht das dabei.
    """

    def setUp(self):
        self.person = Person.objects.create_user("ich", password="ein-langes-passwort")
        self.andere = Person.objects.create_user("andere", password="ein-langes-passwort")
        self.client.force_login(self.person)
        self.schwelle = timezone.now() - timedelta(hours=1)
        self._schwelle_setzen(self.schwelle)
        self._nummer = 0

    # --- Aufbau -----------------------------------------------------------

    def _schwelle_setzen(self, schwelle, letzte_aktivitaet=None):
        """Schwelle setzen und die letzte Aktivitaet FRISCH halten.

        Frisch, damit der naechste Aufruf die Schwelle nicht weiterdreht -
        sonst maesse jeder Zeuge eine Schwelle, die er nicht gesetzt hat.
        """
        Person.objects.filter(pk=self.person.pk).update(
            besuch_davor=schwelle, letzter_besuch=letzte_aktivitaet or timezone.now()
        )

    def _vorher(self):
        return self.schwelle - timedelta(minutes=10)

    def _nachher(self):
        return self.schwelle + timedelta(minutes=10)

    def _objekt(self, eingestellt_von=None, eingestellt_am=None):
        self._nummer += 1
        objekt = Objekt.objects.create(
            url=f"https://x.example/{self._nummer}",
            titel=f"Objekt {self._nummer}",
            eingestellt_von=eingestellt_von,
        )
        # `auto_now_add` vergibt "jetzt"; ueber `update()` gesetzt, weil
        # `save()` den Wert nicht annaehme.
        Objekt.objects.filter(pk=objekt.pk).update(
            eingestellt_am=eingestellt_am or self._vorher()
        )
        return Objekt.objects.get(pk=objekt.pk)

    def _votum(self, objekt, person, wann):
        votum = Votum.objects.create(objekt=objekt, person=person, wertung=Wertung.DAFUER)
        Votum.objects.filter(pk=votum.pk).update(geaendert_am=wann)
        return votum

    def _notiz(self, objekt, person, wann):
        notiz = Notiz.objects.create(objekt=objekt, person=person, text="Ein Hinweis")
        Notiz.objects.filter(pk=notiz.pk).update(erstellt_am=wann)
        return notiz

    def _statusaenderung(self, objekt, person, wann):
        aenderung = objekt.status_setzen(person, Status.BESICHTIGUNG)
        Statusaenderung.objects.filter(pk=aenderung.pk).update(datum=wann)
        return aenderung

    def _preiseintrag(self, objekt, erfasst_am, datum=None):
        eintrag = objekt.preis_setzen(self.andere, Decimal("200000"))
        werte = {"erfasst_am": erfasst_am}
        if datum is not None:
            werte["datum"] = datum
        Preisverlauf.objects.filter(pk=eintrag.pk).update(**werte)
        return eintrag

    # --- Messen -----------------------------------------------------------

    def _antwort(self):
        """Die Liste mit ALLEN Status - sonst faellt die Haelfte heraus,
        sobald ein Zeuge den Status aendert."""
        return self.client.get(
            reverse("objektliste"), {"status": [s.value for s in Status]}
        )

    def _markiert(self, antwort=None):
        """Die Objekte, die auf der gerenderten Liste eine Marke tragen."""
        antwort = antwort if antwort is not None else self._antwort()
        nach_verweis = {reverse("objekt", args=[o.pk]): o.pk for o in Objekt.objects.all()}
        markiert = set()
        for zeile in ListenzeilenParser.lesen(antwort).zeilen:
            if zeile["marken"]:
                markiert |= {
                    nach_verweis[verweis]
                    for verweis in zeile["verweise"]
                    if verweis in nach_verweis
                }
        return markiert

    def _markiert_laut_abfrage(self, antwort=None):
        """Dasselbe an der Annotation statt am Markup."""
        antwort = antwort if antwort is not None else self._antwort()
        return {o.pk for o in antwort.context["objekte"] if o.seit_besuch_bewegt}

    # --- Riegel gegen einen Zeugen im Vakuum ------------------------------

    def test_der_parser_findet_eine_vorhandene_marke(self):
        """Ohne ihn waere jeder `assertEqual(..., set())` unten auch dann
        gruen, wenn der Parser grundsaetzlich nichts findet."""
        objekt = self._objekt()
        self._votum(objekt, self.andere, self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_ohne_jede_bewegung_traegt_die_liste_keine_marke(self):
        """Die Gegenrichtung: der Parser meldet nicht einfach jede Zeile."""
        self._objekt()
        self.assertEqual(self._markiert(), set())

    def test_die_marke_wird_nicht_ausserhalb_der_liste_gefunden(self):
        """Der Riegel auf den Parser selbst.

        Er zaehlt nur, was IN einer Listenzeile steht. Eine Marke im Rahmen
        der Seite - Kopfzeile, Navigation, Basis-Template - darf nicht
        durchschlagen. Gemessen an einer Zeile ohne Marke, vor der eine steht.
        """
        parser = ListenzeilenParser()
        parser.feed(
            '<span class="besuchsmarke" title="seit deinem letzten Besuch"></span>'
            '<ul class="liste"><li class="objekt"><div class="mitte">'
            '<div class="titelzeile"><a href="/objekt/1/">ohne Marke</a></div>'
            "</div></li></ul>"
        )
        self.assertEqual([z["marken"] for z in parser.zeilen], [[]])

    # --- Zusage 1 und 5: fremde Bewegung nach der Schwelle markiert --------

    def test_ein_fremd_eingestelltes_objekt_nach_der_schwelle_markiert(self):
        objekt = self._objekt(
            eingestellt_von=self.andere, eingestellt_am=self._nachher()
        )
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_ein_fremdes_votum_nach_der_schwelle_markiert(self):
        objekt = self._objekt()
        self._votum(objekt, self.andere, self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_eine_fremde_notiz_nach_der_schwelle_markiert(self):
        objekt = self._objekt()
        self._notiz(objekt, self.andere, self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_eine_fremde_statusaenderung_nach_der_schwelle_markiert(self):
        objekt = self._objekt()
        self._statusaenderung(objekt, self.andere, self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_ein_preiseintrag_nach_der_schwelle_markiert(self):
        objekt = self._objekt()
        self._preiseintrag(objekt, self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_ein_objekt_ohne_einwerfer_markiert_ebenfalls(self):
        """`eingestellt_von` ist nullbar, und ab Schritt 3 ist das der
        Normalfall: der Mail-Parser legt Objekte ohne Person an.

        Ein `~Q(eingestellt_von=person)` allein liesse in SQL eine NULL
        uebrig - und genau die Objekte, die niemand eingeworfen hat, truegen
        dann nie eine Marke.
        """
        objekt = self._objekt(eingestellt_von=None, eingestellt_am=self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})

    # --- Zusage 2: eigene Bewegung markiert nicht -------------------------

    def test_das_selbst_eingestellte_objekt_markiert_nicht(self):
        self._objekt(eingestellt_von=self.person, eingestellt_am=self._nachher())
        self.assertEqual(self._markiert(), set())

    def test_das_eigene_votum_markiert_nicht(self):
        objekt = self._objekt()
        self._votum(objekt, self.person, self._nachher())
        self.assertEqual(self._markiert(), set())

    def test_die_eigene_notiz_markiert_nicht(self):
        objekt = self._objekt()
        self._notiz(objekt, self.person, self._nachher())
        self.assertEqual(self._markiert(), set())

    def test_die_eigene_statusaenderung_markiert_nicht(self):
        objekt = self._objekt()
        self._statusaenderung(objekt, self.person, self._nachher())
        self.assertEqual(self._markiert(), set())

    def test_die_eigene_bewegung_verdeckt_die_fremde_nicht(self):
        """Riegel gegen ein zu grobes "eigene Bewegung zaehlt nicht".

        Wer die eigene Bewegung nicht je Eintrag, sondern je OBJEKT
        ausschliesst, loescht damit auch die fremde Bewegung am selben
        Objekt - und die Marke verschwaende genau dann, wenn sie am
        wichtigsten ist: bei einem Objekt, an dem gerade alle arbeiten.
        """
        objekt = self._objekt()
        self._votum(objekt, self.person, self._nachher())
        self._notiz(objekt, self.andere, self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_der_eigene_preiseintrag_markiert_trotzdem(self):
        """Die bewusste Ausnahme aus Abschnitt 2 der Spezifikation.

        Am Preisverlauf haengt keine Person - `02` fuehrt nur Objekt, Datum,
        Preis und Quelle. Eine von Hand eingetragene Preisaenderung ist
        deshalb nicht zuzuordnen und markiert auch fuer die eintragende
        Person. Das ist eine ENTSCHEIDUNG und kein Fehler; sie steht hier als
        Zeuge, damit sie nicht unbemerkt gedreht wird.
        """
        objekt = self._objekt()
        eintrag = objekt.preis_setzen(self.person, Decimal("180000"))
        Preisverlauf.objects.filter(pk=eintrag.pk).update(erfasst_am=self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})

    # --- Zusage 3: Bewegung vor der Schwelle markiert nicht ---------------

    def test_ein_fremd_eingestelltes_objekt_vor_der_schwelle_markiert_nicht(self):
        self._objekt(eingestellt_von=self.andere, eingestellt_am=self._vorher())
        self.assertEqual(self._markiert(), set())

    def test_ein_fremdes_votum_vor_der_schwelle_markiert_nicht(self):
        objekt = self._objekt()
        self._votum(objekt, self.andere, self._vorher())
        self.assertEqual(self._markiert(), set())

    def test_eine_fremde_notiz_vor_der_schwelle_markiert_nicht(self):
        objekt = self._objekt()
        self._notiz(objekt, self.andere, self._vorher())
        self.assertEqual(self._markiert(), set())

    def test_eine_fremde_statusaenderung_vor_der_schwelle_markiert_nicht(self):
        objekt = self._objekt()
        self._statusaenderung(objekt, self.andere, self._vorher())
        self.assertEqual(self._markiert(), set())

    def test_ein_preiseintrag_vor_der_schwelle_markiert_nicht(self):
        objekt = self._objekt()
        self._preiseintrag(objekt, self._vorher())
        self.assertEqual(self._markiert(), set())

    def test_genau_auf_der_schwelle_markiert_nicht(self):
        """`__gt`, nicht `__gte`.

        Die Schwelle IST die letzte Aktivitaet der Person aus dem vorherigen
        Besuch. Was in dieser Mikrosekunde geschah, hat sie gesehen.
        """
        objekt = self._objekt()
        self._votum(objekt, self.andere, self.schwelle)
        self.assertEqual(self._markiert(), set())

    # --- Zusage 4: Schwelle `None` markiert nichts ------------------------

    def test_ohne_schwelle_ist_nichts_markiert(self):
        """Der erste Besuch einer Person, oder ein Konto von vor der
        Einfuehrung der Besuchszeiten.

        Die Gegenlesart - "alles ist neu" - liesse beim ersten Login die
        komplette Liste leuchten, und danach schaut niemand mehr hin.
        Gemessen MIT frischer fremder Bewegung: ohne sie waere der Zeuge auch
        dann gruen, wenn gar nichts zu markieren waere.
        """
        self._schwelle_setzen(None)
        objekt = self._objekt(eingestellt_von=self.andere, eingestellt_am=timezone.now())
        self._votum(objekt, self.andere, timezone.now())
        self._notiz(objekt, self.andere, timezone.now())
        self.assertEqual(self._markiert(), set())

    def test_ohne_schwelle_traegt_auch_die_annotation_nichts(self):
        """Nicht nur das Template schweigt - schon die Abfrage sagt Nein."""
        self._schwelle_setzen(None)
        objekt = self._objekt(eingestellt_von=self.andere, eingestellt_am=timezone.now())
        self.assertEqual(self._markiert_laut_abfrage(), set())
        self.assertIn(objekt.pk, {o.pk for o in self._antwort().context["objekte"]})

    # --- Nachtrag: `erfasst_am` fuehrt, nicht `datum` ---------------------

    def test_ein_alter_preiseintrag_mit_frischer_erfassung_markiert(self):
        """Das fachliche Preisdatum darf beliebig weit zurueckliegen.

        Wer heute eine Preissenkung von vorletzter Woche nachtraegt, hat
        HEUTE etwas getan - und genau das soll die Marke zeigen.
        """
        objekt = self._objekt()
        self._preiseintrag(
            objekt, erfasst_am=self._nachher(), datum=date(2026, 1, 15)
        )
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_ein_heutiger_preiseintrag_mit_alter_erfassung_markiert_nicht(self):
        """Die Gegenrichtung: ein heutiges Preisdatum macht einen laengst
        gesehenen Eintrag nicht wieder frisch.

        `datum` ist von Hand setzbar und an nichts gebunden - es darf vor wie
        hinter dem Erfassungszeitpunkt liegen. Hier liegt es DAHINTER: erfasst
        wurde der Eintrag vorgestern, das Preisdatum steht auf heute.

        Gegen `erfasst_am` geprueft ist das ein laengst gesehener Eintrag und
        traegt keine Marke. Gegen `datum` geprueft leuchtete er - und mit ihm
        jeder Eintrag, dessen Preisdatum juenger ist als der letzte Besuch,
        ganz gleich wann ihn jemand eingetragen hat.

        Die Schwelle liegt eigens auf GESTERN und nicht wie sonst eine Stunde
        zurueck. `datum` ist ein `DateField`, und Django wirft die Uhrzeit beim
        Vergleich weg: laege die Schwelle auf demselben Kalendertag wie
        `datum`, waere `datum > schwelle` auch in der falschen Fassung falsch
        und der Zeuge liefe ins Leere. Die Sabotage-Gegenprobe hat genau das
        aufgedeckt - er war gruen, ohne die Pruefrichtung zu messen.
        """
        self.schwelle = timezone.now() - timedelta(days=1)
        self._schwelle_setzen(self.schwelle)
        objekt = self._objekt()
        self._preiseintrag(
            objekt,
            erfasst_am=timezone.now() - timedelta(days=2),
            datum=timezone.localdate(),
        )
        self.assertEqual(self._markiert(), set())

    # --- Zusage 6, 7 und 8: die Schwelle steht und rueckt nach ------------

    def test_zwei_aufrufe_kurz_hintereinander_lassen_die_marke_stehen(self):
        """Zeuge 6. Unter BESUCHSPAUSE bleibt die Schwelle stehen - und damit
        die Marke. Verschwaende sie beim zweiten Blick, waere sie wertlos:
        niemand traut einem Hinweis, der beim Hinsehen weggeht."""
        objekt = self._objekt()
        self._votum(objekt, self.andere, self._nachher())
        self.assertEqual(self._markiert(), {objekt.pk})
        self.assertEqual(self._markiert(), {objekt.pk})

    def test_zwei_aufrufe_kurz_hintereinander_bewegen_die_schwelle_nicht(self):
        self._objekt()
        self._antwort()
        self._antwort()
        self.assertEqual(
            Person.objects.get(pk=self.person.pk).besuch_davor, self.schwelle
        )

    def test_nach_der_besuchspause_rueckt_die_schwelle_nach(self):
        """Zeuge 7."""
        letzte_aktivitaet = timezone.now() - BESUCHSPAUSE
        self._schwelle_setzen(self.schwelle, letzte_aktivitaet=letzte_aktivitaet)
        self._antwort()
        self.assertEqual(
            Person.objects.get(pk=self.person.pk).besuch_davor, letzte_aktivitaet
        )

    def test_der_erste_aufruf_eines_neuen_besuchs_nutzt_die_frische_schwelle(self):
        """Zeuge 8 - der Zeuge fuer die REIHENFOLGE in der Middleware.

        Aufbau: die alte Schwelle liegt fuenf Stunden zurueck, die letzte
        Aktivitaet des vorherigen Besuchs eine Besuchspause. Dieser Aufruf
        dreht die Schwelle also auf die letzte Aktivitaet.

        `gesehen` bewegte sich DAZWISCHEN - nach der alten Schwelle, aber vor
        der neuen. Die Person hat es in ihrem letzten Besuch also bereits
        gesehen, und es darf nicht leuchten. Wuerde die Schwelle VOR dem
        Fortschreiben gelesen, truege es eine Marke.

        `frisch` bewegte sich nach BEIDEN Schwellen und ist der Riegel: ohne
        ihn waere der Zeuge auch dann gruen, wenn ueberhaupt nichts markiert
        wuerde.
        """
        letzte_aktivitaet = timezone.now() - BESUCHSPAUSE
        self._schwelle_setzen(
            timezone.now() - timedelta(hours=5), letzte_aktivitaet=letzte_aktivitaet
        )
        gesehen = self._objekt(eingestellt_am=self._vorher())
        self._votum(gesehen, self.andere, timezone.now() - timedelta(hours=2))
        frisch = self._objekt(eingestellt_am=self._vorher())
        self._votum(frisch, self.andere, timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._markiert(), {frisch.pk})

    # --- Zusage 3 der Darstellung ----------------------------------------

    def test_die_marke_traegt_die_erklaerung_im_title(self):
        objekt = self._objekt()
        self._votum(objekt, self.andere, self._nachher())
        marken = [t for z in ListenzeilenParser.lesen(self._antwort()).zeilen for t in z["marken"]]
        self.assertEqual(marken, ["seit deinem letzten Besuch"])

    def test_die_marke_traegt_kein_wort_in_der_liste(self):
        """Das Wort "neu" ist in der Oberflaeche gesperrt - es gehoert dem
        Status NEU ("von niemandem angesehen") und meint etwas anderes. Die
        Marke traegt ueberhaupt kein Wort, nur den `title`."""
        objekt = self._objekt()
        self._votum(objekt, self.andere, self._nachher())
        inhalt = self._antwort().content.decode()
        stelle = inhalt.index('class="besuchsmarke"')
        self.assertRegex(inhalt[stelle:], r'^class="besuchsmarke" title="[^"]+"></span>')

    def test_die_marke_sitzt_in_der_titelzeile(self):
        """NACHGEZOGEN am 04.09. - hier stand `test_die_marke_kostet_keine_spalte`.

        Er mass zweierlei: dass die Marke in der Zelle "Objekt" sitzt, und
        dass die Zeile genau so viele Zellen hat wie die Tabelle
        Spaltenkoepfe. Die zweite Haelfte ist mit der Tabelle gefallen - es
        gibt keine Spalten mehr, also auch keine zwoelfte.

        Die ZUSAGE ist dieselbe geblieben und in der neuen Bauform sogar
        schaerfer: die Marke steht in der Titelzeile, vor dem Titel. Sie ist
        damit kein eigenes Feld der Zeile, das in jeder Zeile Platz kostete -
        auch in den neunundvierzig ohne Marke.
        """
        objekt = self._objekt()
        self._votum(objekt, self.andere, self._nachher())
        parser = ListenzeilenParser.lesen(self._antwort())
        self.assertEqual(parser.zeilen[0]["marke_in"], ["titelzeile"])

    def test_eine_zeile_ohne_bewegung_bekommt_keinen_platzhalter(self):
        """Kein leerer Punkt, kein leeres Element - nichts. Ein Platzhalter
        veraenderte die Zeilenhoehe und machte genau das kaputt, wofuer die
        Liste da ist: untereinander stehende Zahlen."""
        bewegt = self._objekt()
        self._votum(bewegt, self.andere, self._nachher())
        self._objekt()
        marken = [len(z["marken"]) for z in ListenzeilenParser.lesen(self._antwort()).zeilen]
        self.assertEqual(sorted(marken), [0, 1])

    # --- Zusage 11: beide Fassungen --------------------------------------

    def _stylesheet(self):
        quelle = (settings.BASE_DIR / "static" / "objektradar.css").read_text(
            encoding="utf-8"
        )
        # Kommentare heraus: sie nennen die Klasse ebenfalls, und ein Zeuge,
        # der einen Kommentar misst, misst gar nichts.
        return re.sub(r"/\*.*?\*/", "", quelle, flags=re.S)

    def _regeln_zur_marke(self):
        """Alle Regeln, deren Selektor `.besuchsmarke` nennt - mit ihrer Stelle."""
        return [
            (treffer.group(1), treffer.group(2), treffer.start())
            for treffer in re.finditer(
                r"([^{}]*\.besuchsmarke[^{}]*)\{([^{}]*)\}", self._stylesheet()
            )
        ]

    def test_die_titelzeile_steht_in_jeder_breite(self):
        """Zeuge 11, erste Haelfte - NACHGEZOGEN.

        Hier stand `test_das_markup_der_marke_ist_fuer_beide_fassungen_
        dasselbe`. Es gibt seit dem 04.09. nur noch EIN Markup, also ist der
        Satz von selbst wahr - und ein Zeuge, der etwas misst, das gar nicht
        mehr falsch sein kann, misst nichts.

        Gemessen wird stattdessen die Stelle, an der die Zusage jetzt brechen
        koennte: die Titelzeile, in der die Marke sitzt, darf in keiner
        Fassung ausgeblendet werden. Sie ist der Traeger - waere sie in einer
        Breite weg, waere es die Marke mit ihr, und die zweite und dritte
        Haelfte des Zeugen (Aussehen ausserhalb des Media-Blocks, kein
        `display: none` an der Marke selbst) faenden das nicht.
        """
        quelle = self._stylesheet()
        for treffer in re.finditer(r"([^{}]*\.titelzeile[^{}]*)\{([^{}]*)\}", quelle):
            with self.subTest(selektor=treffer.group(1).strip()):
                gedraengt = treffer.group(2).replace(" ", "").replace("\n", "")
                self.assertNotIn("display:none", gedraengt)
                self.assertNotIn("visibility:hidden", gedraengt)

    def test_die_marke_bekommt_ihr_aussehen_ausserhalb_jedes_media_blocks(self):
        """Zeuge 11, zweite Haelfte.

        Ein leeres `<span>` ohne Masse ist unsichtbar. Staende die einzige
        Regel im Block ab 48rem, truege die Tabelle die Marke und die Karte
        ein Element ohne Ausdehnung - und die erste Haelfte oben fiele darauf
        herein, weil das Markup ja da waere.

        Auf oberster Ebene heisst: bis zu dieser Stelle sind alle geoeffneten
        Klammern wieder geschlossen.
        """
        quelle = self._stylesheet()
        regeln = self._regeln_zur_marke()
        self.assertTrue(regeln, "keine Regel zu `.besuchsmarke` im Stylesheet")
        oberste = [
            rumpf
            for _, rumpf, stelle in regeln
            if quelle.count("{", 0, stelle) == quelle.count("}", 0, stelle)
        ]
        self.assertTrue(oberste, "die Marke wird nur innerhalb eines Media-Blocks gestaltet")
        self.assertIn("background", "".join(oberste))

    def test_die_marke_wird_in_keiner_fassung_ausgeblendet(self):
        """Zeuge 11, dritte Haelfte - und der, den die Sabotage trifft.

        `display: none` in irgendeiner Regel zur Marke nimmt sie genau einer
        der beiden Fassungen weg, je nachdem, wo die Regel steht.
        """
        for selektor, rumpf, _ in self._regeln_zur_marke():
            with self.subTest(selektor=selektor.strip()):
                self.assertNotIn("display:none", rumpf.replace(" ", "").replace("\n", ""))

    # --- Zusage 10: Abfragelast ------------------------------------------

    def _adresse(self):
        """Mit gesetztem Filter und gesetzter Sortierung, wie bei den
        Geschwistern in `SortierungTests`: beides veraendert den Abfragepfad.

        ALLE Status, weil die Zeugen unten den Status aendern und die Objekte
        sonst aus der Liste fielen.
        """
        return "/?" + "&".join(f"status={s.value}" for s in Status) + "&sortierung=-qm_preis"

    def test_mehr_bewegte_objekte_kosten_nicht_mehr_abfragen(self):
        """Zeuge 10 - der eigentliche Bauteil dieses Punktes.

        Fuenf Bewegungsarten je Objekt, fuenfzig Objekte je Seite: naiv sind
        das 250 Abfragen je Seitenaufruf. Als `Exists()` im SELECT sind es
        null zusaetzliche.

        Gemessen mit FUENFZIG Objekten und nicht mit fuenf. Eine kleine Menge
        faengt ein N+1 nicht - das ist in diesem Projekt schon einmal
        passiert und steht als bekannter Fehler in den Projektnotizen.

        Jedes Objekt bekommt ALLE Bewegungsarten. Ohne Bewegung liefen die
        Unterabfragen zwar auch, aber eine Fassung, die nur bei vorhandenen
        Eintraegen nachschlaegt, kaeme ungesehen durch.

        Der EINSTELLZEITPUNKT liegt bewusst VOR der Schwelle, obwohl das
        Objekt markiert sein soll. Die Sabotage-Gegenprobe hat es aufgedeckt:
        lag er dahinter, war das Objekt schon aus seiner eigenen ZEILE heraus
        markiert - und diese eine Bedingung braucht keine Unterabfrage. Eine
        Schleife je Objekt kurzschliesst darauf, fragt die Datenbank kein
        einziges Mal und kam ungesehen durch. Jetzt kann die Marke NUR aus den
        vier Bewegungsarten kommen, die eine Unterabfrage brauchen.

        Die erwartete Zahl wird beim ersten Durchgang ERMITTELT und nicht
        hingeschrieben: Sitzung und Middleware fragen ohnehin mit, und deren
        Zahl ist nicht die Zusage, die hier gehalten werden soll.
        """
        def anlegen(anzahl):
            for _ in range(anzahl):
                objekt = self._objekt(eingestellt_von=self.andere)
                self._votum(objekt, self.andere, self._nachher())
                self._notiz(objekt, self.andere, self._nachher())
                self._statusaenderung(objekt, self.andere, self._nachher())
                self._preiseintrag(objekt, self._nachher())

        adresse = self._adresse()
        self.client.get(adresse)  # Aufwaermen, damit der Verbindungsaufbau nicht mitzaehlt.
        anlegen(5)
        with CaptureQueriesContext(connection) as mit_fuenf:
            self.client.get(adresse)
        anlegen(views.OBJEKTE_JE_SEITE - 5)
        with self.assertNumQueries(len(mit_fuenf)):
            self.client.get(adresse)

    def test_bei_dieser_messung_sind_die_marken_ueberhaupt_da(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darueber.

        Zeigte die Liste die Marke gar nicht an - weil die Annotation fehlt,
        das Template den Zweig nicht betritt oder der Filter die Objekte
        ausblendet -, waere die Abfragezahl selbstverstaendlich konstant und
        der Zeuge darueber gruen, ohne irgendetwas zu messen.

        Derselbe Aufbau wie dort, samt Einstellzeitpunkt VOR der Schwelle:
        ein Riegel, der etwas anderes misst als der Zeuge, den er sichert,
        sichert ihn nicht.
        """
        objekt = self._objekt(eingestellt_von=self.andere)
        self._votum(objekt, self.andere, self._nachher())
        self._notiz(objekt, self.andere, self._nachher())
        self._statusaenderung(objekt, self.andere, self._nachher())
        self._preiseintrag(objekt, self._nachher())
        self.assertEqual(self._markiert(self.client.get(self._adresse())), {objekt.pk})

    def test_die_seitengroesse_deckt_die_messung_ab(self):
        """Der Zeuge oben legt `OBJEKTE_JE_SEITE` Objekte an und misst damit
        EINE Seite. Waere die Seitengroesse kleiner als fuenfzig, maesse er
        weniger Zeilen als zugesagt."""
        self.assertGreaterEqual(views.OBJEKTE_JE_SEITE, 50)


class ErfassungszeitpunktMigrationTests(TestCase):
    """Migration 0006: `Preisverlauf.erfasst_am` und der Bestandsnachtrag.

    Der Bestand wird aus `datum` ABGELEITET und nicht auf den
    Migrationszeitpunkt gesetzt. Stuenden alle Alteintraege auf "jetzt", laege
    ihr Erfassungszeitpunkt hinter jeder bestehenden Besuchsschwelle - und
    beim naechsten Aufruf leuchtete die halbe Liste gleichzeitig auf. Genau
    einmal, und danach traute niemand der Marke mehr.

    Die Ableitung wird gegen die ECHTE Modellregistrierung gefahren. Sie liest
    nur `datum` und schreibt nur `erfasst_am`; ein historischer Zustand
    zwischen den drei Operationen einer Datei liesse sich ueber
    `project_state()` ohnehin nicht greifen.
    """

    def setUp(self):
        self.objekt = Objekt.objects.create(url="https://x.example/1")

    def _eintrag(self, datum, erfasst_am=None):
        eintrag = Preisverlauf.objects.create(
            objekt=self.objekt, preis=Decimal("200000"), datum=datum
        )
        # `auto_now_add` hat "jetzt" gesetzt - genau den Wert, den die
        # Migration im Bestand NICHT stehen lassen soll.
        Preisverlauf.objects.filter(pk=eintrag.pk).update(
            erfasst_am=erfasst_am or timezone.now()
        )
        return eintrag

    def _ableiten(self):
        erfassungsmigration.aus_datum_ableiten(django_apps, None)

    def _nachher(self, eintrag):
        return Preisverlauf.objects.get(pk=eintrag.pk).erfasst_am

    # --- die Verdrahtung ---------------------------------------------------

    def test_die_migration_fuehrt_genau_diese_funktion_aus(self):
        """Ohne diesen Zeugen sind die folgenden blind: sie rufen die Funktion
        direkt auf. Waere sie nicht in den Operationen verdrahtet, liefe die
        Migration im Betrieb nichts - und die Zeugen unten waeren gruen."""
        lauf = [
            o for o in erfassungsmigration.Migration.operations
            if isinstance(o, migrations.RunPython)
        ]
        self.assertEqual([o.code for o in lauf], [erfassungsmigration.aus_datum_ableiten])

    def test_die_ableitung_laeuft_zwischen_anlegen_und_festziehen(self):
        """Die Reihenfolge ist die Zusage, nicht die Kosmetik.

        Zuerst nullbar anlegen, dann fuellen, dann auf `auto_now_add`
        festziehen. Liefe die Ableitung nach dem Festziehen, muesste die
        Spalte schon vorher nicht-null sein - und die Bestandszeilen bekaemen
        den Wert, den sie gerade nicht bekommen sollen.
        """
        arten = [type(o).__name__ for o in erfassungsmigration.Migration.operations]
        self.assertEqual(arten, ["AddField", "RunPython", "AlterField"])

    def test_das_feld_wird_zuerst_nullbar_und_ohne_auto_now_add_angelegt(self):
        anlegen = erfassungsmigration.Migration.operations[0]
        self.assertTrue(anlegen.field.null)
        self.assertFalse(getattr(anlegen.field, "auto_now_add", False))

    def test_das_feld_wird_danach_auf_auto_now_add_festgezogen(self):
        festziehen = erfassungsmigration.Migration.operations[2]
        self.assertTrue(festziehen.field.auto_now_add)

    def test_die_ableitung_ist_rueckwaerts_ein_noop(self):
        lauf = erfassungsmigration.Migration.operations[1]
        self.assertIs(lauf.reverse_code, migrations.RunPython.noop)

    # --- die Zusage --------------------------------------------------------

    def test_der_bestand_bekommt_mitternacht_ortszeit_des_preisdatums(self):
        eintrag = self._eintrag(date(2026, 1, 15))
        self._ableiten()
        self.assertEqual(
            self._nachher(eintrag),
            timezone.make_aware(datetime.combine(date(2026, 1, 15), time.min)),
        )

    def test_der_bestand_bekommt_nicht_den_migrationszeitpunkt(self):
        """Die eigentliche Zusage dieser Migration.

        Ein Eintrag mit einem Preisdatum von vor dreissig Tagen darf danach
        nicht so aussehen, als sei er gerade eben erfasst worden.
        """
        eintrag = self._eintrag(timezone.localdate() - timedelta(days=30))
        self._ableiten()
        self.assertLess(self._nachher(eintrag), timezone.now() - timedelta(days=29))

    def test_ein_alteintrag_leuchtet_nach_der_migration_nicht_auf(self):
        """Dieselbe Zusage, aber an der Wirkung gemessen statt am Wert.

        Gegen eine Schwelle von gestern darf ein dreissig Tage alter Eintrag
        keine Marke ausloesen. Auf den Migrationszeitpunkt gesetzt taete er
        genau das - und mit ihm jeder andere Alteintrag gleichzeitig.
        """
        eintrag = self._eintrag(timezone.localdate() - timedelta(days=30))
        self._ableiten()
        schwelle = timezone.now() - timedelta(days=1)
        self.assertFalse(
            Preisverlauf.objects.filter(pk=eintrag.pk, erfasst_am__gt=schwelle).exists()
        )

    def test_die_ableitung_geht_ueber_jede_zeile(self):
        """Riegel: `bulk_update` mit einer leeren Liste faellt nicht auf.

        Verglichen wird in ORTSZEIT. Mitternacht Europe/Berlin ist in UTC der
        Vorabend, und `erfasst_am.date()` liefert den UTC-Tag - der Zeuge
        haette den Bestand sonst pauschal um einen Tag danebengesehen.
        """
        eintraege = [
            self._eintrag(timezone.localdate() - timedelta(days=tage))
            for tage in (10, 20, 30)
        ]
        self._ableiten()
        for eintrag in eintraege:
            with self.subTest(datum=eintrag.datum):
                self.assertEqual(
                    timezone.localtime(self._nachher(eintrag)).date(), eintrag.datum
                )

    def test_ein_frischer_eintrag_bekommt_weiterhin_den_erfassungszeitpunkt(self):
        """Die Migration betrifft den BESTAND. Neue Eintraege kommen ueber
        `auto_now_add` und tragen die echte Uhrzeit - sonst haette die Marke
        ab morgen wieder nur Tagesgenauigkeit."""
        eintrag = Preisverlauf.objects.create(
            objekt=self.objekt, preis=Decimal("190000")
        )
        self.assertAlmostEqual(
            eintrag.erfasst_am, timezone.now(), delta=timedelta(minutes=1)
        )


# =========================================================================
# Verdecktes Votum - Bauspezifikation vom 04.09.
#
# Kippt die Entscheidung aus `01` und `02` ("Alle sehen alle Vota"). Wer an
# einem Objekt noch nicht abgestimmt hat, sieht dort die Vota der anderen
# nicht - weder in der Liste noch in der Objektansicht.
# =========================================================================


class VotumzellenParser(HTMLParser):
    """Je Zeile der Objektliste: der Votum-Block mit Text, Verweisen und Punkten.

    NACHGEZOGEN am 04.09. auf das neue Markup. Bis dahin las er `<tbody>` und
    die Zelle mit `data-spalte="Votum"`; die Liste ist jetzt eine
    `<ul class="liste">` und der Block ein `<div class="votum">` in der Zeile.

    Eingegrenzt auf die LISTE und auf den Votum-Block EINER Zeile. In diesem
    Projekt haben Zeugen ihre Zeichenkette schon einmal im Basis-Template
    gefunden und dort gemessen, wo die Zusage gar nicht steht.

    `punkte` ist am 04.09. dazugekommen. Die Punktreihe IST der Zaehlstand in
    anderer Form: fuenf Punkte, von denen zwei gefuellt sind, sagen dasselbe
    wie "2 dafür · 3 offen". Eine Verdeckung, die den Satz weglaesst und die
    Punkte stehen liesse, waere keine - und ohne diesen Zaehler faende das
    kein Zeuge.

    Die Zeile wird ueber den Verweis in ihrer Titelzeile wiedererkannt
    (`objekt_href`). Ueber die Reihenfolge ginge es auch, aber dann haenge
    Zeuge 4 - zwei Objekte, eines frei, eines verdeckt - an der Sortierung der
    Liste statt an der Zusage.

    Geprueft wird auf ELEMENTE und Klassenlisten, nicht auf
    `class="…"`-Zeichenketten: ein erweiterter Klassenname lief in der
    Votum-Runde schon einmal an einem Zeugen vorbei.
    """

    def __init__(self):
        super().__init__()
        self.zeilen = []
        self._in_liste = 0
        self._zeile = None
        self._tiefe_votum = 0
        self._in_stimme = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        klassen = (attrs.get("class") or "").split()
        if tag == "ul" and "liste" in klassen:
            self._in_liste += 1
            return
        if not self._in_liste:
            return
        if tag == "li" and "objekt" in klassen:
            self._zeile = {"objekt_href": None, "text": "", "verweise": [], "punkte": []}
            self.zeilen.append(self._zeile)
            self._tiefe_votum = 0
            return
        if self._zeile is None:
            return
        if "votum" in klassen:
            self._tiefe_votum = 1
        elif self._tiefe_votum:
            self._tiefe_votum += 1
        if self._tiefe_votum:
            if "stimme" in klassen:
                # Die Art der Stimme, nicht die Klassenliste: `dafuer`,
                # `anschauen`, `raus`, `offen`.
                self._zeile["punkte"].append(
                    next((k for k in klassen if k != "stimme"), None)
                )
                # Das Zeichen IM Punkt ("+", "−", "?") ist kein Text des
                # Blocks. Zaehlte es mit, truege `text` bei freigeschaltetem
                # Stand ein Praefix aus Zeichen, und jeder Zeuge auf den
                # Zaehlstand muesste es wegrechnen.
                self._in_stimme = True
            if tag == "a" and attrs.get("href"):
                self._zeile["verweise"].append(attrs["href"])
        elif tag == "a" and attrs.get("href") and self._zeile["objekt_href"] is None:
            self._zeile["objekt_href"] = attrs["href"]

    def handle_data(self, daten):
        if self._tiefe_votum and self._zeile is not None and not self._in_stimme:
            self._zeile["text"] += daten

    def handle_endtag(self, tag):
        if tag == "ul":
            self._in_liste = max(0, self._in_liste - 1)
            self._zeile = None
            self._tiefe_votum = 0
            self._in_stimme = False
        elif tag == "li":
            self._zeile = None
            self._tiefe_votum = 0
            self._in_stimme = False
        elif self._tiefe_votum:
            self._tiefe_votum -= 1
            self._in_stimme = False

    @classmethod
    def nach_href(cls, antwort):
        """`{Adresse der Objektansicht: {"text", "verweise", "punkte"}}` je Zeile."""
        parser = cls()
        parser.feed(antwort.content.decode())
        return {
            zeile["objekt_href"]: {
                "text": " ".join(zeile["text"].split()),
                "verweise": zeile["verweise"],
                "punkte": zeile["punkte"],
            }
            for zeile in parser.zeilen
        }


class VotaBlockParser(HTMLParser):
    """Die Eintraege aus `<ul class="vota">` der Objektansicht.

    Gemessen wird am BLOCK und nicht an der blossen Anwesenheit eines Wortes
    auf der Seite: "dafuer", "anschauen" und "raus" stehen ohnehin auf jeder
    Objektansicht - als Beschriftung der drei Wertungsknoepfe im Formular, das
    auch ohne eigenes Votum bedienbar bleibt. Ein `assertNotContains`
    auf "anschauen" waere deshalb rot, egal was die Verdeckung tut, und ein
    `assertContains` gruen, egal was sie tut.

    Was ausschliesslich zu den Vota der anderen gehoert, ist die Liste selbst:
    `ul.vota` mit je einem `<li>` aus Person, Wertung und Begruendung.
    """

    def __init__(self):
        super().__init__()
        self.eintraege = []
        self.listen = 0
        self._in_liste = False
        self._eintrag = None
        self._feld = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        klassen = (attrs.get("class") or "").split()
        if tag == "ul" and "vota" in klassen:
            self._in_liste = True
            self.listen += 1
        elif not self._in_liste:
            return
        elif tag == "li":
            self._eintrag = {"person": "", "wertung": "", "begruendung": ""}
            self.eintraege.append(self._eintrag)
        elif tag == "span" and self._eintrag is not None:
            self._feld = next(
                (name for name in self._eintrag if name in klassen), None
            )

    def handle_data(self, daten):
        if self._feld is not None and self._eintrag is not None:
            self._eintrag[self._feld] += daten.strip()

    def handle_endtag(self, tag):
        if tag == "ul":
            self._in_liste = False
            self._eintrag = None
        elif tag == "li":
            self._eintrag = None
        elif tag == "span":
            self._feld = None

    @classmethod
    def lesen(cls, antwort):
        parser = cls()
        parser.feed(antwort.content.decode())
        return parser.eintraege

    @classmethod
    def anzahl_listen(cls, antwort):
        parser = cls()
        parser.feed(antwort.content.decode())
        return parser.listen


class VerdecktesVotumBasis(TestCase):
    """Gemeinsamer Unterbau der Zeugen zum verdeckten Votum.

    FUENF Personen, weil die Votum-Uebersicht der Liste die Zahl der aktiven
    Personen fuer "offen" braucht - mit zweien staende dort nie ein
    Zaehlstand, an dem sich eine Verdeckung messen liesse.

    Liste und Objektansicht verlangen eine Anmeldung; ohne sie maessen alle
    Zeugen unten dieselbe Umleitung und niemand merkte es.
    """

    #: Eine Zeichenkette, die auf keiner Seite dieses Projekts sonst vorkommt.
    #: Die Begruendung bekommt einen EIGENEN Zeugen, und der taugt nur mit
    #: einem Text, den kein Formular, kein Statusname und kein Kommentar
    #: zufaellig ebenfalls traegt.
    BEGRUENDUNG = "Zisterne unter der Terrasse"

    def setUp(self):
        self.person = Person.objects.create_user(
            "ich", password="ein-langes-passwort", first_name="Ich", last_name="Selbst"
        )
        self.anna = Person.objects.create_user(
            "anna", first_name="Anna", last_name="Beispiel"
        )
        self.bernd = Person.objects.create_user(
            "bernd", first_name="Bernd", last_name="Beispiel"
        )
        self.clara = Person.objects.create_user(
            "clara", first_name="Clara", last_name="Beispiel"
        )
        self.doris = Person.objects.create_user(
            "doris", first_name="Doris", last_name="Beispiel"
        )
        self.client.force_login(self.person)
        self._nummer = 0

    # --- Aufbau -----------------------------------------------------------

    def _objekt(self, **felder):
        self._nummer += 1
        felder.setdefault("url", f"https://x.example/{self._nummer}")
        felder.setdefault("titel", f"Objekt {self._nummer}")
        return Objekt.objects.create(**felder)

    def _votum(self, objekt, person, wertung=Wertung.DAFUER, begruendung=""):
        return Votum.objects.create(
            objekt=objekt, person=person, wertung=wertung, begruendung=begruendung
        )

    def _liste(self, adresse="/"):
        return self.client.get(adresse)

    def _seite(self, objekt):
        return self.client.get(reverse("objekt", args=[objekt.pk]))

    def _zelle(self, objekt, adresse="/"):
        """Text und Verweise der Votum-Zelle in der Zeile DIESES Objekts."""
        zeilen = VotumzellenParser.nach_href(self._liste(adresse))
        return zeilen[reverse("objekt", args=[objekt.pk])]


class VerdecktesVotumInDerListeTests(VerdecktesVotumBasis):
    """Abschnitt 2: die Votum-Spalte der Liste.

    Aufbau: drei andere stimmen mit "dafuer". Ohne eigenes Votum stuende in
    der Spalte "3 dafuer · 2 offen", mit eigenem "3 dafuer · 1 anschauen ·
    1 offen". Beide Zeichenketten kommen auf der Seite sonst nirgends vor -
    "raus" allein taugte nicht, das Wort ist auch ein Statusname und steht im
    Filterblock.
    """

    def setUp(self):
        super().setUp()
        self.objekt = self._objekt(titel="Zur Abstimmung")
        for person in (self.anna, self.bernd, self.clara):
            self._votum(self.objekt, person, Wertung.DAFUER)

    #: Was in der Zeile staende, wenn die Verdeckung ausfiele.
    VERDECKT = "3 dafür · 2 offen"
    #: Was dort steht, sobald selbst abgestimmt wurde.
    FREI = "3 dafür · 1 anschauen · 1 offen"
    #: Was im verdeckten Zustand dasteht: der Aufruf und ein kurzer Hinweis.
    #: Der Hinweis ist am 04.09. dazugekommen - `02` von heute verlangt ihn:
    #: eine Zeile, in der nur "abstimmen" steht, sagt nicht, WARUM dort kein
    #: Stand steht, und sieht deshalb aus wie ein Anzeigefehler.
    AUFRUF = "abstimmen — die Vota der anderen erscheinen danach"

    def _selbst_abstimmen(self):
        self._votum(self.objekt, self.person, Wertung.ANSCHAUEN)

    # --- Zeuge 1 ----------------------------------------------------------

    def test_ohne_eigenes_votum_steht_der_zaehlstand_nicht_im_antworttext(self):
        """Zeuge 1 - der Kern der Zusage.

        NICHT "ist unsichtbar", sondern nicht vorhanden: der Zaehlstand wird
        gar nicht erst gerendert. Gemessen am ganzen Antworttext und nicht nur
        an der Zelle - wer den Quelltext ansieht, soll ihn nirgends finden,
        auch nicht in einem `title`, einem Datenattribut oder einem Kommentar.
        """
        self.assertNotContains(self._liste(), self.VERDECKT)

    def test_ohne_eigenes_votum_steht_auch_die_blosse_zahl_nicht_da(self):
        """Riegel gegen eine Verdeckung, die nur den ZUSAMMENGESETZTEN Satz
        weglaesst und die Zahl anderswo stehen laesst.

        "3 dafür" ist der ankernde Teil - genau der, der die eigene Stimme
        zieht. Der Zeuge darueber faende ihn nicht mehr, sobald jemand die
        Zusammensetzung aendert; dieser hier schon.
        """
        self.assertNotContains(self._liste(), "3 dafür")

    def test_ohne_eigenes_votum_steht_dort_auch_kein_noch_kein_votum(self):
        """`02` sagt: keine Zahl, kein Zaehlstand, kein "noch kein Votum".

        Der Satz ist selbst eine Aussage ueber den Stand - er behauptete, es
        habe niemand abgestimmt, waehrend drei es getan haben.
        """
        self.assertNotIn(views.KEIN_VOTUM, self._zelle(self.objekt)["text"])

    # --- Zeuge 2 ----------------------------------------------------------

    def test_ohne_eigenes_votum_steht_der_aufruf_zum_abstimmen_da(self):
        """Zeuge 2. Wortlaut "abstimmen" - nicht "keine Angabe", nicht leer.

        Eine leere Zeile saehe aus wie ein Anzeigefehler, und der Block
        traegt hier seine einzige verbliebene Aufgabe: den Weg zur eigenen
        Stimme.

        Der Hinweis daneben gehoert dazu und ist keine Zugabe: ohne ihn bleibt
        offen, ob niemand abgestimmt hat oder ob der Stand verdeckt ist.
        """
        self.assertEqual(self._zelle(self.objekt)["text"], self.AUFRUF)

    def test_der_hinweis_verraet_nicht_ob_ueberhaupt_jemand_gestimmt_hat(self):
        """Der Riegel am Hinweis selbst.

        Drei andere haben an diesem Objekt gestimmt. Der Hinweis darf davon
        nichts sagen - weder eine Zahl noch "es gibt welche" noch "es gibt
        keine". Er sagt nur, wann etwas zu sehen sein wird.
        """
        text = self._zelle(self.objekt)["text"]
        self.assertNotRegex(text, r"\d")
        self.assertNotIn(views.KEIN_VOTUM, text)

    def test_ohne_eigenes_votum_steht_kein_einziger_punkt_da(self):
        """Die Punktreihe IST der Zaehlstand, nur in anderer Form.

        Fuenf Punkte, von denen drei gefuellt sind, sagen "3 von 5 haben
        gestimmt" - genau die Auskunft, die verdeckt sein soll. Ein Zeuge, der
        nur den Satz misst, faende diese Luecke nicht: der Satz kann fehlen,
        waehrend die Punkte dastehen.
        """
        self.assertEqual(self._zelle(self.objekt)["punkte"], [])

    def test_der_aufruf_verlinkt_auf_die_objektansicht(self):
        """Getrennt vom Zeugen darueber: dass das Wort dasteht, sagt noch
        nicht, dass es irgendwohin fuehrt. Abgestimmt wird in der
        Objektansicht - ein Knopf in der Liste braeuchte eine Wertung, und
        die waere geraten."""
        self.assertEqual(
            self._zelle(self.objekt)["verweise"],
            [reverse("objekt", args=[self.objekt.pk])],
        )

    # --- Zeuge 3 ----------------------------------------------------------

    def test_mit_eigenem_votum_steht_der_zaehlstand_da(self):
        """Zeuge 3 - und der Riegel gegen alle Zeugen oben im Vakuum.

        Verdeckte die Liste den Zaehlstand IMMER, waeren die Zeugen 1 und 2
        gruen, ohne die Freischaltung zu messen.
        """
        self._selbst_abstimmen()
        self.assertContains(self._liste(), self.FREI)

    def test_mit_eigenem_votum_steht_der_aufruf_nicht_mehr_da(self):
        """Beide Zustaende schliessen einander aus. Staende der Aufruf neben
        dem Zaehlstand, saehe die Spalte nach einer offenen Abstimmung aus,
        an der man noch teilnehmen kann - und man hat schon."""
        self._selbst_abstimmen()
        self.assertNotIn("abstimmen", self._zelle(self.objekt)["text"])

    def test_jedes_votum_schaltet_frei_auch_anschauen(self):
        """`02`: jedes Votum schaltet frei, auch "anschauen". Keine
        Sonderbehandlung einzelner Wertungen.

        Der Zeuge darueber stimmt bereits mit "anschauen" ab; dieser hier
        prueft die beiden anderen Wertungen, damit die Zusage nicht an einer
        einzelnen haengt.
        """
        for wertung in (Wertung.DAFUER, Wertung.RAUS):
            with self.subTest(wertung=wertung):
                Votum.objects.update_or_create(
                    objekt=self.objekt,
                    person=self.person,
                    defaults={"wertung": wertung},
                )
                self.assertNotIn("abstimmen", self._zelle(self.objekt)["text"])

    # --- Zeuge 4 ----------------------------------------------------------

    def test_die_freischaltung_gilt_je_objekt_in_einer_einzigen_antwort(self):
        """Zeuge 4 - der Riegel gegen einen globalen Schalter.

        Dieselbe Person, zwei Objekte, an EINEM gestimmt. Eine Fassung, die
        "hat diese Person irgendwo gestimmt" fragt, zeigte beide - und faellt
        hier.

        Gemessen in EINER Antwort und nicht in zweien: zwei Aufrufe koennten
        sich in der Sitzung, im Besuchszeitpunkt oder in der Sortierung
        unterscheiden, und der Unterschied laege dann nicht bewiesen am
        Objekt.
        """
        zweites = self._objekt(titel="Ohne eigene Stimme")
        for person in (self.anna, self.bernd, self.clara):
            self._votum(zweites, person, Wertung.DAFUER)
        self._selbst_abstimmen()

        zeilen = VotumzellenParser.nach_href(self._liste())
        self.assertEqual(
            zeilen[reverse("objekt", args=[self.objekt.pk])]["text"], self.FREI
        )
        self.assertEqual(
            zeilen[reverse("objekt", args=[zweites.pk])]["text"], self.AUFRUF
        )
        # Und die Punktreihe steht nur an dem Objekt, das freigeschaltet ist.
        self.assertEqual(zeilen[reverse("objekt", args=[zweites.pk])]["punkte"], [])
        self.assertEqual(
            len(zeilen[reverse("objekt", args=[self.objekt.pk])]["punkte"]), 5
        )

    def test_ein_votum_an_einem_objekt_laesst_das_andere_verdeckt(self):
        """Dieselbe Lage, am ANTWORTTEXT gemessen.

        Der Zeuge darueber sagt, was in welcher Zelle steht. Dieser hier
        haelt die Zusage aus Abschnitt 4 fuer das zweite Objekt: sein
        Zaehlstand steht nirgends in der Antwort - auch nicht ausserhalb der
        Tabelle.
        """
        zweites = self._objekt(titel="Ohne eigene Stimme")
        for person in (self.anna, self.bernd, self.clara):
            self._votum(zweites, person, Wertung.DAFUER)
        self._selbst_abstimmen()
        # Am zweiten Objekt haben genau drei gestimmt und die angemeldete
        # Person nicht - "3 dafür · 2 offen" ist damit sein Zaehlstand und
        # steht auf der Seite nur, wenn die Verdeckung dort ausgefallen ist.
        self.assertNotContains(self._liste(), self.VERDECKT)

    # --- Zeuge 12 ---------------------------------------------------------

    def test_die_zusage_gilt_in_beiden_fassungen(self):
        """Zeuge 12, erste Haelfte - NACHGEZOGEN.

        Es gibt seit dem 04.09. nur noch EIN Markup; die Frage "steht der
        Aufruf auch in der anderen Fassung" beantwortet sich damit von
        selbst. Gemessen wird stattdessen, dass der Block ueberhaupt da ist
        und nicht etwa erst ab einer Breite gerendert wird: `data-spalte`
        gibt es nicht mehr, der Traeger heisst jetzt `.votum`.
        """
        self.assertEqual(self._zelle(self.objekt)["text"], self.AUFRUF)
        self.assertEqual(len(_klassen_von(self._liste(), "votum")), 1)

    def test_das_stylesheet_verdeckt_den_votum_block_in_keiner_fassung(self):
        """Zeuge 12, zweite Haelfte - und der, den die Sabotage trifft.

        Verlegte jemand die Verdeckung ins Stylesheet, staende der Zaehlstand
        im Markup und waere nur fuer das Auge weg: im Quelltext zu finden, im
        Suchfeld des Browsers zu finden, im gespeicherten HTML zu finden. Und
        je nachdem, ob die Regel im Media-Block steht oder darueber, traefe
        sie nur EINE der beiden Fassungen.

        Geprueft an jeder Regel, deren Selektor den Votum-Block oder die
        Punktreihe nennt - beide tragen den Stand. Kommentare heraus: sie
        nennen die Klassen ebenfalls, und ein Zeuge, der einen Kommentar
        misst, misst gar nichts.
        """
        quelle = (settings.BASE_DIR / "static" / "objektradar.css").read_text(
            encoding="utf-8"
        )
        ohne_kommentare = re.sub(r"/\*.*?\*/", "", quelle, flags=re.S)
        gefunden = 0
        for name in (r"\.votum\b", r"\.stimmen\b", r"\.stimme\b", r"\.votum-text\b"):
            for treffer in re.finditer(rf"([^{{}}]*{name}[^{{}}]*)\{{([^{{}}]*)\}}", ohne_kommentare):
                gefunden += 1
                with self.subTest(selektor=treffer.group(1).strip()):
                    gedraengt = treffer.group(2).replace(" ", "").replace("\n", "")
                    self.assertNotIn("display:none", gedraengt)
                    self.assertNotIn("visibility:hidden", gedraengt)
        # Riegel gegen einen Zeugen im Vakuum: faende der Ausdruck gar keine
        # Regel, liefe die Schleife leer und bliebe gruen.
        self.assertGreater(gefunden, 0, "keine Regel zum Votum-Block im Stylesheet")


class VerdecktesVotumInDerObjektansichtTests(VerdecktesVotumBasis):
    """Abschnitt 3: die Vota der anderen in der Objektansicht.

    Aufbau: Anna stimmt mit "anschauen" und einer unverwechselbaren
    Begruendung, Bernd mit "raus". Zwei andere und nicht einer: bei einem
    liesse sich "alle verdeckt" nicht von "einer faellt durch" unterscheiden.
    """

    def setUp(self):
        super().setUp()
        self.objekt = self._objekt(titel="Finca am Hang")
        self._votum(self.objekt, self.anna, Wertung.ANSCHAUEN, self.BEGRUENDUNG)
        self._votum(self.objekt, self.bernd, Wertung.RAUS, "Zu weit weg")

    def _selbst_abstimmen(self, wertung=Wertung.DAFUER, begruendung=""):
        return self._votum(self.objekt, self.person, wertung, begruendung)

    # --- Zeuge 5 ----------------------------------------------------------

    def test_ohne_eigenes_votum_steht_keine_fremde_wertung_im_markup(self):
        """Zeuge 5, erste Haelfte: die Wertungen der anderen.

        Gemessen am Block `ul.vota` und NICHT am Wort: "anschauen" und "raus"
        stehen ohnehin auf jeder Objektansicht - als Beschriftung der drei
        Wertungsknoepfe des Formulars, das bedienbar bleiben muss. Ein Zeuge
        auf das blosse Wort maesse das Formular und nicht die Verdeckung.
        """
        self.assertEqual(VotaBlockParser.lesen(self._seite(self.objekt)), [])

    def test_ohne_eigenes_votum_steht_die_liste_der_anderen_gar_nicht_da(self):
        """Nicht nur leer, sondern nicht vorhanden.

        Eine leere `<ul class="vota">` waere der Ort, an dem die Eintraege
        stehen - und ein Stylesheet oder ein spaeterer Eingriff koennte sie
        wieder fuellen, ohne dass ein Zeuge es merkt.

        NACHGEBESSERT in der Sabotage-Gegenprobe. Die erste Fassung suchte die
        Zeichenkette `class="vota"` im Antworttext. Die Sabotage "Verdeckung
        ins Stylesheet verlegen" schrieb `class="vota verdeckt"` - dieselbe
        Liste, dieselbe Klasse, und der Zeuge blieb gruen, weil das
        Anfuehrungszeichen nicht mehr da stand, wo er es erwartete. Gemessen
        wird deshalb am ELEMENT, ueber denselben Parser wie die Eintraege.
        """
        self.assertEqual(VotaBlockParser.anzahl_listen(self._seite(self.objekt)), 0)

    def test_mit_eigenem_votum_steht_die_liste_der_anderen_da(self):
        """Riegel gegen den Zeugen darueber im Vakuum: faende der Parser die
        Liste NIE, waere er gruen, ohne die Verdeckung zu messen."""
        self._selbst_abstimmen()
        self.assertEqual(VotaBlockParser.anzahl_listen(self._seite(self.objekt)), 1)

    def test_ohne_eigenes_votum_steht_die_fremde_begruendung_nicht_im_antworttext(self):
        """Zeuge 5, zweite Haelfte - mit eigener, unverwechselbarer Zeichenkette.

        Die Begruendung ist der Teil, der am staerksten ankert: eine Zahl
        laesst sich uebersehen, ein Satz wie "Dach ist neu" nicht. Sie
        bekommt deshalb einen eigenen Zeugen, und der misst am GANZEN
        Antworttext: die Sabotage "Begruendung stehen lassen, nur die Wertung
        verdecken" faellt genau hier.
        """
        self.assertNotContains(self._seite(self.objekt), self.BEGRUENDUNG)

    def test_ohne_eigenes_votum_steht_der_name_der_anderen_nicht_da(self):
        """Auch die Person nicht: `02` nennt Wertung, Begruendung, Person und
        Zaehlstand in einem Atemzug. Wer weiss, DASS Anna gestimmt hat,
        weiss zwar noch nicht wie - aber er weiss, dass die Abstimmung
        laeuft, und genau das ist ein Zaehlstand von eins."""
        antwort = self._seite(self.objekt)
        self.assertNotContains(antwort, "Anna Beispiel")
        self.assertNotContains(antwort, "Bernd Beispiel")

    def test_an_stelle_der_vota_steht_ein_hinweis(self):
        """`02`: an ihrer Stelle steht ein kurzer Hinweis, dass die Vota der
        anderen nach der eigenen Stimme sichtbar werden.

        Ohne ihn saehe die Seite aus, als habe niemand abgestimmt - und das
        waere eine Falschaussage ueber den Stand, keine Verdeckung.
        """
        self.assertContains(
            self._seite(self.objekt), "Sichtbar, sobald du selbst abgestimmt hast."
        )

    def test_der_hinweis_behauptet_nicht_dass_niemand_abgestimmt_hat(self):
        """Der dritte Zweig des Blocks gehoert HINTER die Freischaltung.

        "Von den anderen hat noch niemand abgestimmt." ist selbst eine
        Aussage ueber die Vota der anderen. Vor der Freischaltung waere sie
        entweder ein verratener Zaehlstand oder - wie hier, wo zwei gestimmt
        haben - schlicht falsch.
        """
        self.assertNotContains(
            self._seite(self.objekt), "Von den anderen hat noch niemand abgestimmt."
        )

    # --- Zeuge 6 ----------------------------------------------------------

    def test_ohne_eigenes_votum_steht_das_votum_formular_da(self):
        """Zeuge 6, erste Haelfte. Die Verdeckung nimmt die Vota der anderen
        weg, nicht den Weg zur eigenen Stimme - sonst waere sie nicht
        aufloesbar und die Spalte in der Liste zeigte fuer immer
        "abstimmen"."""
        antwort = self._seite(self.objekt)
        self.assertContains(
            antwort, f'action="{reverse("votum_setzen", args=[self.objekt.pk])}"'
        )
        for wert, _ in Wertung.choices:
            with self.subTest(wertung=wert):
                self.assertContains(antwort, f'name="wertung" value="{wert}"')

    def test_das_votum_formular_ist_nicht_abgeschaltet(self):
        """Ein `disabled` an den Knoepfen liesse das Formular dastehen und
        nichts tun - das Markup des Zeugen darueber waere unveraendert."""
        self.assertNotContains(self._seite(self.objekt), "disabled")

    def test_ohne_eigenes_votum_laesst_sich_wirklich_abstimmen(self):
        """Zeuge 6, zweite Haelfte: bedienbar heisst, es kommt etwas an.

        Das Markup zu pruefen genuegt nicht - eine Ansicht, die den POST
        abweist, liesse das Formular unveraendert dastehen.
        """
        self.client.post(
            reverse("votum_setzen", args=[self.objekt.pk]),
            {"wertung": Wertung.DAFUER, "begruendung": "Passt"},
        )
        self.assertTrue(
            Votum.objects.filter(objekt=self.objekt, person=self.person).exists()
        )

    # --- Zeuge 7 ----------------------------------------------------------

    def test_mit_eigenem_votum_stehen_die_fremden_wertungen_da(self):
        """Zeuge 7, erste Haelfte - und der Riegel gegen Zeuge 5 im Vakuum.

        Verdeckte die Ansicht die fremden Vota IMMER, waere Zeuge 5 gruen,
        ohne die Freischaltung zu messen.
        """
        self._selbst_abstimmen()
        eintraege = VotaBlockParser.lesen(self._seite(self.objekt))
        self.assertEqual(
            {(e["person"], e["wertung"]) for e in eintraege},
            {("Anna Beispiel", "anschauen"), ("Bernd Beispiel", "raus")},
        )

    def test_mit_eigenem_votum_steht_die_fremde_begruendung_da(self):
        """Zeuge 7, zweite Haelfte - der Riegel gegen den Begruendungs-Zeugen
        im Vakuum. Er misst dieselbe Zeichenkette an derselben Stelle."""
        self._selbst_abstimmen()
        self.assertContains(self._seite(self.objekt), self.BEGRUENDUNG)

    def test_mit_eigenem_votum_steht_der_hinweis_nicht_mehr_da(self):
        self._selbst_abstimmen()
        self.assertNotContains(
            self._seite(self.objekt), "Sichtbar, sobald du selbst abgestimmt hast."
        )

    def test_das_eigene_votum_steht_nicht_zwischen_den_anderen(self):
        """Unveraendert aus der Zeit davor: die eigene Wertung steht oben im
        Formular, nicht ein zweites Mal in der Liste darunter."""
        self._selbst_abstimmen()
        eintraege = VotaBlockParser.lesen(self._seite(self.objekt))
        self.assertNotIn("Ich Selbst", {e["person"] for e in eintraege})

    # --- Zeuge 8 ----------------------------------------------------------

    def test_das_eigene_votum_ist_sichtbar_wenn_sonst_nichts_zu_sehen_ist(self):
        """Zeuge 8. Das eigene Votum wird NICHT mitverdeckt.

        Gemessen in dem Zustand, in dem eine Person gestimmt hat und es sonst
        nichts zu sehen gibt: die anderen haben noch nicht gestimmt. Das ist
        der Zustand, den eine Fassung verliert, die den ganzen Votum-Block an
        die Vota der anderen haengt - dann waere die eigene Wertung weg,
        obwohl sie nur von einem selbst kommt.

        Der Zustand ist ausserdem der einzige, in dem sich "eigenes Votum
        sichtbar" ueberhaupt getrennt messen laesst: das eigene Votum IST die
        Freischaltung, wer eines hat, ist frei. "Vor der Freischaltung" kann
        deshalb nur heissen: bevor es etwas freizuschalten gibt.

        Die eigene Wertung muss markiert sein - ohne die Markierung liesse
        sich nicht erkennen, wie man gestimmt hat, und das ist die
        Voraussetzung dafuer, es zu aendern.
        """
        Votum.objects.filter(objekt=self.objekt).delete()
        self._selbst_abstimmen(Wertung.RAUS, self.BEGRUENDUNG)
        antwort = self._seite(self.objekt)
        self.assertContains(antwort, 'value="raus" aria-pressed="true"')
        self.assertContains(antwort, self.BEGRUENDUNG)

    def test_die_eigene_begruendung_steht_im_formular_und_nicht_daneben(self):
        """Sie steht im Textfeld, weil sie von dort aus geaendert wird.

        Stuende sie nur als Text auf der Seite, waere sie zwar sichtbar, aber
        beim naechsten Speichern weg - das Feld ginge leer ab.
        """
        Votum.objects.filter(objekt=self.objekt).delete()
        self._selbst_abstimmen(Wertung.RAUS, self.BEGRUENDUNG)
        self.assertContains(
            self._seite(self.objekt),
            f'rows="2">{self.BEGRUENDUNG}</textarea>',
            html=False,
        )

    # --- Zeuge 9 ----------------------------------------------------------

    def test_nach_dem_abstimmen_ist_im_naechsten_aufruf_alles_frei(self):
        """Zeuge 9. Der Weg durch die Anwendung, nicht am Modell vorbei.

        Alle Zeugen oben legen ihr Votum mit `Votum.objects.create` an. Dieser
        hier geht durch `votum_setzen` - faengt die Ansicht die Wertung ab
        oder schriebe sie an einer anderen Person fest, bliebe die Seite
        verdeckt, und keiner der anderen Zeugen saehe es.
        """
        self.assertNotContains(self._seite(self.objekt), self.BEGRUENDUNG)
        self.client.post(
            reverse("votum_setzen", args=[self.objekt.pk]),
            {"wertung": Wertung.ANSCHAUEN, "begruendung": ""},
        )
        self.assertContains(self._seite(self.objekt), self.BEGRUENDUNG)

    def test_nach_dem_abstimmen_ist_auch_die_liste_frei(self):
        """Dieselbe Zusage fuer die Liste: eine Freischaltung, die nur eine
        der beiden Seiten erreicht, waere keine."""
        self.client.post(
            reverse("votum_setzen", args=[self.objekt.pk]),
            {"wertung": Wertung.ANSCHAUEN, "begruendung": ""},
        )
        self.assertNotIn("abstimmen", self._zelle(self.objekt)["text"])

    # --- Zeuge 10 ---------------------------------------------------------

    def test_ein_geaendertes_votum_haelt_die_freischaltung(self):
        """Zeuge 10. `votum_setzen` ersetzt das bestehende Votum ueber
        `update_or_create` - ein Weg, auf dem eine Fassung, die auf das
        ANLEGEN eines Votums horcht, die Freischaltung verlieren koennte.

        Alle drei Wertungen durchlaufen, damit die Zusage nicht an einer
        einzelnen haengt.
        """
        for wertung, _ in Wertung.choices:
            with self.subTest(wertung=wertung):
                self.client.post(
                    reverse("votum_setzen", args=[self.objekt.pk]),
                    {"wertung": wertung, "begruendung": "Neue Begründung"},
                )
                self.assertContains(self._seite(self.objekt), self.BEGRUENDUNG)

    def test_ein_geaendertes_votum_bleibt_ein_einziges(self):
        """Riegel gegen einen Zeugen, der die Freischaltung nur deshalb
        haelt, weil bei jedem Wechsel ein zweites Votum entstanden ist."""
        for wertung, _ in Wertung.choices:
            self.client.post(
                reverse("votum_setzen", args=[self.objekt.pk]),
                {"wertung": wertung, "begruendung": ""},
            )
        self.assertEqual(
            Votum.objects.filter(objekt=self.objekt, person=self.person).count(), 1
        )


class VerdecktesVotumAbfragelastTests(VerdecktesVotumBasis):
    """Abschnitt 5: die Abfragezahl der Liste bleibt konstant.

    Die Frage "hat diese Person an diesem Objekt gevotet" wird AUF DER
    ABFRAGE beantwortet, als `Exists()`-Annotation. Kein Zugriff je Zeile in
    der Vorlage, keine Schleife in der Ansicht.
    """

    def _adresse(self):
        """Mit gesetztem Filter und gesetzter Sortierung, wie bei den
        Geschwistern in `SortierungTests` und `BesuchsmarkeTests`: beides
        veraendert den Abfragepfad."""
        return (
            "/?" + "&".join(f"status={s.value}" for s in Status) + "&sortierung=-qm_preis"
        )

    def _gemischt(self, anzahl):
        """`anzahl` Objekte, abwechselnd mit und ohne eigenes Votum.

        An JEDEM Objekt stimmen drei andere ab. Ohne fremde Vota liefe die
        Unterabfrage zwar auch, aber eine Fassung, die nur bei vorhandenen
        Eintraegen nachschlaegt, kaeme ungesehen durch.

        KEINE Zeile kann die Frage aus sich selbst beantworten: an `Objekt`
        haengt keine Spalte, die "diese Person hat hier gestimmt" wuesste -
        anders als bei der Besuchsmarke, wo `eingestellt_am` an der Zeile
        steht und eine naive Schleife darauf kurzschliessen konnte. Die
        Unterabfrage ist damit nicht wegzukuerzen, und ein Zugriff je Zeile
        kostet wirklich eine Abfrage je Zeile.
        """
        for lauf in range(anzahl):
            objekt = self._objekt()
            for person in (self.anna, self.bernd, self.clara):
                self._votum(objekt, person, Wertung.DAFUER)
            if lauf % 2 == 0:
                self._votum(objekt, self.person, Wertung.ANSCHAUEN)

    def test_mehr_objekte_kosten_nicht_mehr_abfragen(self):
        """Zeuge 11 - der Bauteil dieses Abschnitts.

        Gemessen mit FUENFZIG Objekten und nicht mit fuenf. Eine kleine Menge
        faengt ein N+1 nicht - das ist in diesem Projekt schon einmal
        passiert und steht als bekannter Fehler in den Projektnotizen.

        Etwa die Haelfte mit eigenem Votum, die andere ohne. Eine Menge, in
        der alle Objekte gleich stehen, misst den anderen Zweig nicht: die
        Vorlage betraete nur einen von beiden, und ein Nachschlagen im
        anderen bliebe unentdeckt.

        Die erwartete Zahl wird beim ersten Durchgang ERMITTELT und nicht
        hingeschrieben: Sitzung und Middleware fragen ohnehin mit, und deren
        Zahl ist nicht die Zusage, die hier gehalten werden soll.
        """
        adresse = self._adresse()
        self.client.get(adresse)  # Aufwaermen, damit der Verbindungsaufbau nicht mitzaehlt.
        self._gemischt(6)
        with CaptureQueriesContext(connection) as mit_sechs:
            self.client.get(adresse)
        self._gemischt(views.OBJEKTE_JE_SEITE - 6)
        with self.assertNumQueries(len(mit_sechs)):
            self.client.get(adresse)

    def test_bei_dieser_messung_stehen_beide_zweige_wirklich_da(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darueber.

        Betraete die Liste nur einen der beiden Zweige - weil die Annotation
        fehlt, der Filter die Objekte ausblendet oder alle Objekte gleich
        stehen -, waere die Abfragezahl selbstverstaendlich konstant und der
        Zeuge gruen, ohne irgendetwas zu messen.

        Derselbe Aufbau wie dort, in derselben Groesse: ein Riegel, der etwas
        anderes misst als der Zeuge, den er sichert, sichert ihn nicht.
        """
        self._gemischt(views.OBJEKTE_JE_SEITE)
        texte = [
            zeile["text"]
            for zeile in VotumzellenParser.nach_href(
                self._liste(self._adresse())
            ).values()
        ]
        self.assertEqual(len(texte), views.OBJEKTE_JE_SEITE)
        self.assertEqual(
            texte.count("abstimmen — die Vota der anderen erscheinen danach"),
            views.OBJEKTE_JE_SEITE // 2,
        )
        self.assertEqual(
            texte.count("3 dafür · 1 anschauen · 1 offen"), views.OBJEKTE_JE_SEITE // 2
        )

    def test_die_seitengroesse_deckt_die_messung_ab(self):
        """Der Zeuge oben legt `OBJEKTE_JE_SEITE` Objekte an und misst damit
        EINE Seite. Waere die Seitengroesse kleiner als fuenfzig, maesse er
        weniger Zeilen als zugesagt."""
        self.assertGreaterEqual(views.OBJEKTE_JE_SEITE, 50)

    def test_die_annotation_kommt_aus_der_abfrage_und_nicht_aus_einer_schleife(self):
        """Die Zusage an der Methode selbst, ohne Ansicht und ohne Vorlage.

        `hat_eigenes_votum` steht nach EINER Abfrage an jedem Objekt. Eine
        Fassung, die den Wert in der Ansicht nachtraegt, braeuchte hier
        einundfuenfzig - und dieser Zeuge nennt die Stelle beim Namen,
        waehrend der Zeuge oben nur "irgendwo mehr Abfragen" sagt.
        """
        self._gemischt(10)
        with self.assertNumQueries(1):
            werte = {
                objekt.pk: objekt.hat_eigenes_votum
                for objekt in Objekt.objects.mit_eigenem_votum(self.person)
            }
        self.assertEqual(sorted(werte.values()), [False] * 5 + [True] * 5)

    def test_die_annotation_fragt_nach_dieser_person(self):
        """Der Riegel gegen einen Filter, der die Person verliert.

        Ohne ihn waere `hat_eigenes_votum` bei jedem Objekt wahr, an dem
        IRGENDWER gestimmt hat. Der Zeuge darueber faende das nicht: an jedem
        seiner Objekte stimmen drei andere mit, also waere ueberall `True`
        herausgekommen - und die Haelfte, die er zaehlt, waere zufaellig
        richtig gewesen, nur eben aus dem falschen Grund.
        """
        objekt = self._objekt()
        self._votum(objekt, self.anna, Wertung.DAFUER)
        werte = Objekt.objects.mit_eigenem_votum(self.person)
        self.assertFalse(werte.get(pk=objekt.pk).hat_eigenes_votum)
        self.assertTrue(
            Objekt.objects.mit_eigenem_votum(self.anna)
            .get(pk=objekt.pk)
            .hat_eigenes_votum
        )

    def test_die_annotation_bleibt_am_richtigen_objekt(self):
        """Riegel gegen eine Unterabfrage ohne `OuterRef` - "hat diese Person
        irgendwo gestimmt" waere ein globaler Schalter."""
        mit = self._objekt()
        ohne = self._objekt()
        self._votum(mit, self.person, Wertung.DAFUER)
        werte = {
            objekt.pk: objekt.hat_eigenes_votum
            for objekt in Objekt.objects.mit_eigenem_votum(self.person)
        }
        self.assertTrue(werte[mit.pk])
        self.assertFalse(werte[ohne.pk])

    def test_die_votumzaehlung_bleibt_neben_der_annotation_richtig(self):
        """Der Riegel gegen das Kreuzprodukt.

        Die Liste zieht drei bedingte `Count` ueber `vota`. Waere die
        Freischaltung als zweites Aggregat ueber dieselbe Relation gebaut,
        liefe sie ueber denselben JOIN und vervielfachte die Zahlen still.
        Eine `Exists`-Subquery steht im SELECT und joint nicht.

        Gemessen an der gerenderten Spalte: dort wuerde die Verfaelschung
        sichtbar.
        """
        objekt = self._objekt()
        for person in (self.anna, self.bernd, self.clara):
            self._votum(objekt, person, Wertung.DAFUER)
        self._votum(objekt, self.person, Wertung.ANSCHAUEN)
        for text in ("erste Notiz", "zweite Notiz"):
            Notiz.objects.create(objekt=objekt, person=self.person, text=text)
        self.assertEqual(
            self._zelle(objekt)["text"], "3 dafür · 1 anschauen · 1 offen"
        )


# =========================================================================
# Oberflaeche neu - die Zeugen der Runde vom 04.09.
# =========================================================================


class UnterzeilenParser(HTMLParser):
    """Die Teile der Unterzeile je Listenzeile, ohne die Statusmarke.

    Gemessen wird an ELEMENTEN und nicht an Zeichenketten: die Zusage lautet,
    dass fuer ein leeres Feld KEIN Element und kein Gedankenstrich entsteht,
    und "kein Element" laesst sich nur an Elementen pruefen. In der
    Votum-Runde ist ein Zeuge daran vorbeigelaufen, dass er eine
    `class="…"`-Zeichenkette suchte.

    Die Statusmarke wird ausgelassen: sie steht in JEDER Zeile und ist keine
    der Angaben, um die es hier geht.
    """

    def __init__(self):
        super().__init__()
        self.zeilen = []
        self._in_liste = 0
        self._zeile = None
        self._in_unterzeile = 0
        self._teil = None

    def handle_starttag(self, tag, attrs):
        klassen = (dict(attrs).get("class") or "").split()
        if tag == "ul" and "liste" in klassen:
            self._in_liste += 1
            return
        if not self._in_liste:
            return
        if tag == "li" and "objekt" in klassen:
            self._zeile = []
            self.zeilen.append(self._zeile)
            self._in_unterzeile = 0
            return
        if self._zeile is None:
            return
        if "unterzeile" in klassen:
            self._in_unterzeile = 1
        elif self._in_unterzeile:
            self._in_unterzeile += 1
            if self._in_unterzeile == 2 and "statusmarke" not in klassen:
                # Nur die unmittelbaren Kinder der Unterzeile sind Teile.
                self._teil = ""
                self._zeile.append(None)

    def handle_data(self, daten):
        if self._teil is not None:
            self._teil += daten

    def handle_endtag(self, tag):
        if tag == "ul":
            self._in_liste = max(0, self._in_liste - 1)
            self._zeile = None
            self._in_unterzeile = 0
        elif tag == "li":
            self._zeile = None
            self._in_unterzeile = 0
        elif self._in_unterzeile:
            if self._teil is not None and self._in_unterzeile == 2:
                self._zeile[-1] = " ".join(self._teil.split())
                self._teil = None
            self._in_unterzeile -= 1

    @classmethod
    def lesen(cls, antwort):
        parser = cls()
        parser.feed(antwort.content.decode())
        return parser.zeilen


class UnterzeileTests(TestCase):
    """Zeugen 6 und 7: was leer ist, faellt weg - einzeln.

    Die Liste fuehrte bis zum 04.09. eigene Spalten fuer Ort, Region/Land und
    Zustand. Eine Spalte muss in JEDER Zeile etwas enthalten, also stand dort
    ein Gedankenstrich, sobald ein Feld leer war - und leer sind sie fast
    alle, solange nur eine URL eingeworfen wurde. Die Zusage aus `02` ("leere
    Felder zeigt die Objektansicht, die Liste nicht") war damit nie gehalten.

    Jetzt stehen die drei als Unterzeile beim Titel und fallen einzeln weg.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _teile(self):
        zeilen = UnterzeilenParser.lesen(self.client.get("/"))
        self.assertEqual(len(zeilen), 1, "erwartet wird genau eine Listenzeile")
        return zeilen[0]

    # --- Riegel gegen einen Zeugen im Vakuum ------------------------------

    def test_der_parser_findet_ueberhaupt_teile(self):
        """Ohne ihn waere jedes `assertEqual(..., [])` unten auch dann gruen,
        wenn der Parser grundsaetzlich nichts findet."""
        Objekt.objects.create(url="https://x/1", ort="Ronda")
        self.assertEqual(self._teile(), ["Ronda"])

    def test_der_parser_zaehlt_die_statusmarke_nicht_mit(self):
        """Sie steht in jeder Zeile und ist keine der Angaben, um die es geht.

        Zaehlte er sie mit, waere die leere Unterzeile nie leer und Zeuge 6
        koennte gar nicht mehr fallen.
        """
        Objekt.objects.create(url="https://x/1")
        self.assertNotIn("neu", self._teile())

    # --- Zeuge 6 ----------------------------------------------------------

    def test_ohne_ort_region_und_zustand_steht_in_der_unterzeile_nichts(self):
        """Zeuge 6 - der Kern.

        Kein leeres Element, kein Gedankenstrich. Ein Objekt direkt nach dem
        Einwurf traegt keines der drei Felder; das ist der Normalfall und
        nicht der Sonderfall.
        """
        Objekt.objects.create(url="https://x/1")
        self.assertEqual(self._teile(), [])

    def test_in_der_zeile_ohne_angaben_steht_auch_kein_gedankenstrich(self):
        """Am Text der ganzen Zeile gemessen, nicht nur an den Teilen.

        Der Zeuge darueber faende einen Strich nicht, der ausserhalb eines
        Elements steht - direkt zwischen zwei Marken zum Beispiel. Der
        Gedankenstrich der ZAHLEN bleibt ausdruecklich erlaubt: dort haelt er
        die Spalten auseinander, und dafuer ist er da.
        """
        Objekt.objects.create(url="https://x/1")
        inhalt = self.client.get("/").content.decode()
        block = inhalt[inhalt.index('<div class="unterzeile">') :]
        block = block[: block.index("</div>")]
        self.assertNotIn("—", block)

    def test_der_zustand_unklar_gilt_als_leer(self):
        """`unklar` ist die Vorbelegung des Feldes und keine Aussage.

        Stuende er da, truege ihn fast jede Zeile - und die Angabe verloere
        genau dort ihren Wert, wo sie einen haette. Die Objektansicht zeigt
        ihn weiterhin; dort ist ein leeres Feld die Aufforderung, es zu
        fuellen.
        """
        objekt = Objekt.objects.create(url="https://x/1")
        self.assertEqual(objekt.zustand, Zustand.UNKLAR)
        self.assertEqual(self._teile(), [])

    def test_ein_leeres_feld_zieht_die_gefuellten_nicht_mit(self):
        """Einzeln, nicht als Gruppe. Ohne diesen Zeugen koennte die
        Unterzeile ganz wegfallen, sobald EIN Feld leer ist - und Zeuge 6
        bliebe gruen."""
        Objekt.objects.create(url="https://x/1", ort="Ronda", zustand=Zustand.MITTEL)
        self.assertEqual(self._teile(), ["Ronda", "mittel"])

    # --- Zeuge 7 ----------------------------------------------------------

    def test_ein_objekt_mit_ort_rendert_ihn(self):
        """Zeuge 7 - der Riegel gegen eine Unterzeile, die immer leer ist.

        Ohne ihn waere Zeuge 6 gruen, indem die Liste die Unterzeile
        ueberhaupt nicht mehr ausgibt.
        """
        Objekt.objects.create(url="https://x/1", ort="Ronda")
        self.assertIn("Ronda", self._teile())

    def test_region_und_land_stehen_als_EIN_teil(self):
        """Sie beantworten gemeinsam die Frage "wo".

        Als zwei Teile bekaemen sie einen Trennpunkt zwischen sich und
        saehen aus wie zwei Angaben; "Málaga · ES" liest sich als Ort und
        Land, "Málaga, ES" als eine Herkunft.
        """
        Objekt.objects.create(url="https://x/1", region="Málaga", land=Land.ES)
        self.assertEqual(self._teile(), ["Málaga, Spanien"])

    def test_die_region_allein_steht_ohne_komma(self):
        """Ein Komma ohne zweiten Teil waere ein abgeschnittener Satz."""
        Objekt.objects.create(url="https://x/1", region="Málaga")
        self.assertEqual(self._teile(), ["Málaga"])

    def test_das_land_allein_steht_ohne_komma(self):
        Objekt.objects.create(url="https://x/1", land=Land.ES)
        self.assertEqual(self._teile(), ["Spanien"])

    def test_alle_drei_stehen_in_der_reihenfolge_des_entwurfs(self):
        """Ort, dann Herkunft, dann Zustand - vom Engsten zum Weitesten und
        dann zur Beschaffenheit."""
        Objekt.objects.create(
            url="https://x/1",
            ort="Álora",
            region="Málaga",
            land=Land.ES,
            zustand=Zustand.KERNSANIERUNG,
        )
        self.assertEqual(self._teile(), ["Álora", "Málaga, Spanien", "Kernsanierung"])

    def test_die_trennpunkte_kommen_aus_dem_stylesheet(self):
        """Und nicht aus der Vorlage.

        Dort muessten sie an jedem Teil ausser dem ersten stehen, und "ausser
        dem ersten" ist in der Template-Sprache eine Zaehlung - die dann fuer
        jede weggefallene Angabe wieder stimmen muesste. Im Stylesheet ist es
        ein Selektor, und ein weggefallener Teil nimmt seinen Punkt von selbst
        mit.
        """
        quelle = re.sub(r"\s+", " ", (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8"))
        self.assertIn(".unterzeile >", quelle)
        self.assertIn('content: " ·"', quelle)

    def test_der_trennpunkt_haengt_hinten_am_teil(self):
        """Und nicht vorn am naechsten.

        Vorn stand er bis zur Sichtpruefung. Am Handy bricht die Unterzeile
        um, und dann fing die naechste Zeile mit "· Kernsanierung" an - eine
        Aufzaehlung, der der Anfang fehlt. Hinten angehaengt bleibt der Punkt
        bei dem Wort, zu dem er gehoert.
        """
        quelle = re.sub(r"\s+", " ", (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8"))
        self.assertIn("::after {", quelle[quelle.index(".unterzeile >") :][:200])
        self.assertNotIn(".unterzeile > * + *::before", quelle)


class FilterblockOffenTests(TestCase):
    """Zeuge 8: der Block steht offen, wenn er etwas verbirgt - und sonst zu."""

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _offen(self, adresse="/"):
        """Ob das `<details>` des Filterblocks `open` traegt.

        Am ELEMENT gemessen und nicht an der Zeichenkette `open` irgendwo in
        der Antwort: das Wort steht auch in Fliesstext und Attributwerten.
        """
        inhalt = self.client.get(adresse).content.decode()
        stelle = inhalt.index("<details")
        return " open>" in inhalt[stelle : inhalt.index(">", stelle) + 1]

    # --- Riegel gegen einen Zeugen im Vakuum ------------------------------

    def test_es_gibt_ueberhaupt_einen_aufklappbaren_block(self):
        self.assertContains(self.client.get("/"), "<details")

    def test_die_ableitung_erkennt_ein_offenes_element(self):
        """Der Riegel auf `_offen` selbst: an einer Adresse mit gesetztem
        Filter muss sie wahr liefern, sonst misst jeder `assertFalse` unten
        nichts."""
        self.assertTrue(self._offen("/?land=ES"))

    # --- zu, wenn nichts abweicht -----------------------------------------

    def test_ohne_parameter_ist_der_block_zu(self):
        self.assertFalse(self._offen("/"))

    def test_der_vorbelegte_statusfilter_klappt_ihn_nicht_auf(self):
        """DER Fall, an dem sich "abweichend" von "gesetzt" unterscheidet.

        Jeder Blaetter- und Sortierlink traegt den Statusfilter vollstaendig
        mit. Danach steht `status` viermal in der Adresse - und es ist
        trotzdem genau die Vorbelegung. Ein Block, der darauf aufklappte,
        waere nach dem ersten Klick nie wieder zu.
        """
        adresse = "/?" + "&".join(f"status={s.value}" for s in STATUS_VORBELEGUNG)
        self.assertFalse(self._offen(adresse))

    def test_die_reihenfolge_der_statuswerte_ist_dabei_gleichgueltig(self):
        """Die Adresse traegt sie in der Reihenfolge, in der die Kaestchen
        abgesendet wurden; die Vorbelegung in der der Auswahlliste. Verglichen
        werden MENGEN."""
        adresse = "/?" + "&".join(
            f"status={s.value}" for s in reversed(STATUS_VORBELEGUNG)
        )
        self.assertFalse(self._offen(adresse))

    def test_ein_leerer_parameter_klappt_ihn_nicht_auf(self):
        """`?suche=` steht in der Adresse und schraenkt nichts ein."""
        self.assertFalse(self._offen("/?suche="))

    def test_sortierung_und_seitenzahl_klappen_ihn_nicht_auf(self):
        for adresse in ("/?sortierung=qm_preis", "/?seite=1"):
            with self.subTest(adresse=adresse):
                self.assertFalse(self._offen(adresse))

    # --- offen, sobald etwas abweicht -------------------------------------

    def test_ein_gesetzter_landfilter_klappt_ihn_auf(self):
        self.assertTrue(self._offen("/?land=ES"))

    def test_eine_freitextsuche_klappt_ihn_auf(self):
        self.assertTrue(self._offen("/?suche=Ronda"))

    def test_eine_abweichende_statusauswahl_klappt_ihn_auf(self):
        """Weniger als die Vorbelegung - hier wird wirklich etwas verborgen."""
        self.assertTrue(self._offen("/?status=neu"))

    def test_eine_erweiterte_statusauswahl_klappt_ihn_ebenfalls_auf(self):
        """MEHR als die Vorbelegung ist auch eine Abweichung.

        Wer `raus` dazugeschaltet hat, sieht Objekte, die sonst nicht
        dastuenden - und soll am Block ablesen koennen, warum.
        """
        adresse = "/?" + "&".join(f"status={s.value}" for s in Status)
        self.assertTrue(self._offen(adresse))

    def test_der_leere_statusfilter_klappt_ihn_auf(self):
        """`?status=` heisst "keiner" und liefert null Treffer. Bliebe der
        Block zu, staende die leere Liste ohne jede Erklaerung da."""
        self.assertTrue(self._offen("/?status="))

    def test_eine_preisuntergrenze_von_null_klappt_ihn_auf(self):
        """Eine 0 ist ein gesetzter Filter, kein leeres Feld.

        Derselbe Unterschied wie in `filtern()`, und er wird an genau einer
        Stelle getroffen - `_gesetzt()`.
        """
        self.assertTrue(self._offen("/?preis_von=0"))

    # --- die Kopfzeile sagt, was gilt -------------------------------------

    def test_die_kopfzeile_nennt_die_zahl_der_status(self):
        self.assertContains(self.client.get("/"), "4 Status")

    def test_die_kopfzeile_sagt_dass_sonst_nichts_eingeschraenkt_ist(self):
        self.assertContains(self.client.get("/"), forms.SONST_ALLE)

    def test_die_kopfzeile_nennt_den_gesetzten_filter(self):
        antwort = self.client.get("/?land=ES")
        self.assertContains(antwort, "Land")
        self.assertNotContains(antwort, forms.SONST_ALLE)

    def test_die_kopfzeile_nennt_den_WERT_des_filters_nicht(self):
        """Eine Freitextsuche kann beliebig lang sein, und die Kopfzeile soll
        eine Zeile bleiben. Was gesucht wird, steht im Feld darunter."""
        inhalt = self.client.get("/?suche=Zisterne").content.decode()
        kopf = inhalt[inhalt.index("<summary") : inhalt.index("</summary>")]
        self.assertIn("Suche", kopf)
        self.assertNotIn("Zisterne", kopf)

    # --- die Marken zeigen, was wirklich gilt -----------------------------

    def test_die_vorbelegten_status_stehen_angehakt_da(self):
        """Sonst widerspricht sich der Block: die Kopfzeile nennt vier, und
        keine Marke ist abgesetzt.

        Der Fehlstand ist aelter als diese Runde - ein gebundenes Formular
        rendert aus den Daten, und in der Adresse stand nichts. Sichtbar wird
        er erst, seit die Kopfzeile die Zahl nennt.
        """
        inhalt = self.client.get("/").content.decode()
        block = inhalt[inhalt.index('<div class="kaestchen">') :]
        block = block[: block.index("</div>")]
        self.assertEqual(block.count("checked"), len(STATUS_VORBELEGUNG))

    def test_die_angehakten_marken_sind_die_der_vorbelegung(self):
        """Nicht irgendwelche vier."""
        inhalt = self.client.get("/").content.decode()
        block = inhalt[inhalt.index('<div class="kaestchen">') :]
        block = block[: block.index("</div>")]
        angehakt = {
            treffer
            for treffer in re.findall(r'value="([^"]+)"[^>]*checked', block)
        }
        self.assertEqual(angehakt, {s.value for s in STATUS_VORBELEGUNG})

    def test_eine_gesetzte_auswahl_schlaegt_die_vorbelegung(self):
        inhalt = self.client.get("/?status=raus").content.decode()
        block = inhalt[inhalt.index('<div class="kaestchen">') :]
        block = block[: block.index("</div>")]
        self.assertEqual(re.findall(r'value="([^"]+)"[^>]*checked', block), ["raus"])

    def test_der_leere_statusfilter_hakt_nichts_an(self):
        """`?status=` heisst "keiner" - und sieht dann auch so aus."""
        inhalt = self.client.get("/?status=").content.decode()
        block = inhalt[inhalt.index('<div class="kaestchen">') :]
        block = block[: block.index("</div>")]
        self.assertNotIn("checked", block)

    def test_die_marken_aendern_nichts_an_der_trefferanzeige(self):
        """Riegel: geaendert wird ausschliesslich, was DASTEHT.

        `ist_gefiltert()` liest weiterhin, was wirklich in der Adresse stand.
        Liefe es kuenftig ueber die angezeigte Auswahl, truege die
        ungefilterte Liste eine Trefferanzeige.
        """
        self.assertFalse(self.client.get("/").context["ist_gefiltert"])


class KantenfarbeTests(TestCase):
    """Zeuge 10: die Statusfarbe steht als Kante an der Zeile, je Status verschieden.

    Die Kante loest den gemeldeten Punkt, dass fuenf der sechs alten
    Flaechentoene zu dicht am Papierton lagen. Sie steht AUF dem Papier statt
    darin.

    Gemessen an drei Stellen, weil die Zusage an drei Stellen brechen kann:
    die Zeile traegt die Klasse, die Klasse setzt eine eigene Farbe, und die
    Kante liest sie.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _quelle(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8")

    def _alle_sichtbar(self):
        return self.client.get(
            reverse("objektliste"), {"status": [s.value for s in Status]}
        )

    def test_jede_zeile_traegt_ihre_statusklasse(self):
        """Am ELEMENT gemessen und ueber die MENGE der Zeilen.

        Ueber die Menge, damit auch auffaellt, wenn alle Zeilen dieselbe
        Klasse tragen - der Fall, den eine fest hineingeschriebene Klasse
        erzeugte.
        """
        for status in Status:
            Objekt.objects.create(url=f"https://x/{status}", titel=str(status), status=status)
        zeilen = _klassen_von(self._alle_sichtbar(), "objekt")
        self.assertEqual(
            sorted(sorted(k) for k in zeilen),
            sorted(sorted(["objekt", f"status-{s.value}"]) for s in Status),
        )

    def test_die_zeile_traegt_keine_stilangabe_mit_der_farbe(self):
        """Der Entwurf setzt die Farbe als `style="--kante:…"` an der Zeile.

        Das waere eine zweite Zuordnung zwischen Status und Farbe, diesmal in
        der Vorlage, neben der im Stylesheet - und zwei Tabellen fuer dieselbe
        Regel driften. Genau diese Falle hat das Projekt bei der Statusmarke
        schon einmal umgangen.
        """
        Objekt.objects.create(url="https://x/1", status=Status.HEISSE_SPUR)
        self.assertNotContains(self.client.get("/"), "style=")

    def test_die_kante_liest_die_statusfarbe(self):
        """Die Regel, die die Kante zeichnet, greift auf `--status` zu.

        Ohne sie truege die Zeile ihre Klasse und saehe trotzdem aus wie jede
        andere.
        """
        quelle = re.sub(r"\s+", " ", self._quelle())
        regel = quelle[quelle.index(".objekt::before {") :]
        regel = regel[: regel.index("}")]
        self.assertIn("var(--status", regel)
        self.assertIn("background", regel)

    def test_die_kante_hat_ueberhaupt_eine_breite(self):
        """Eine Kante ohne Breite ist keine. Sie ist ein Pseudo-Element ohne
        Inhalt; ohne `width` waere sie null Pixel breit und unsichtbar."""
        quelle = re.sub(r"\s+", " ", self._quelle())
        regel = quelle[quelle.index(".objekt::before {") :]
        regel = regel[: regel.index("}")]
        self.assertRegex(regel, r"width:\s*[1-9]")

    def test_die_kante_steht_ausserhalb_jedes_media_blocks(self):
        """Sie gilt in beiden Fassungen. Stuende sie nur ab 48rem, faehle am
        Handy genau die Unterscheidung, die diese Runde herstellt - und das
        Handy ist das Geraet, an dem die Liste unterwegs gelesen wird."""
        quelle = self._quelle()
        start = quelle.index("@media (min-width: 48rem)")
        offen = quelle.index("{", start)
        tiefe = 0
        for stelle in range(offen, len(quelle)):
            if quelle[stelle] == "{":
                tiefe += 1
            elif quelle[stelle] == "}":
                tiefe -= 1
                if tiefe == 0:
                    block = quelle[offen : stelle + 1]
                    break
        else:
            raise AssertionError("Der Media-Block ist nicht geschlossen.")
        self.assertIn(".objekt::before {", re.sub(r"\s+", " ", quelle.replace(block, "")))

    def test_je_status_eine_andere_kante(self):
        """Sechs Klassen auf dieselbe Farbe waeren sechs gleiche Kanten.

        Die Werte werden aus der Datei GELESEN, nicht hier wiederholt - eine
        zweite Liste driftet von der ersten weg.
        """
        quelle = re.sub(r"\s+", " ", self._quelle())
        variablen = dict(
            re.findall(r"(--status-[a-z_]+):\s*(#[0-9A-Fa-f]{6})", self._quelle())
        )
        gesetzt = []
        for status in Status:
            regel = quelle[quelle.index(f".status-{status.value} {{") :]
            regel = regel[: regel.index("}")]
            treffer = re.search(r"--status:\s*var\((--status-[a-z_]+)\)", regel)
            self.assertIsNotNone(treffer, f"{status.value} setzt keine Kantenfarbe")
            gesetzt.append(variablen[treffer.group(1)].lower())
        self.assertEqual(len(set(gesetzt)), len(Status.choices))

    def test_die_pille_liest_dieselbe_farbe(self):
        """Eine Farbe, zwei Traeger. Zwei getrennte Angaben koennten
        auseinanderlaufen, und dann truege eine Zeile eine gruene Kante und
        eine blaue Pille."""
        quelle = re.sub(r"\s+", " ", self._quelle())
        regel = quelle[quelle.index(".statusmarke {") :]
        regel = regel[: regel.index("}")]
        self.assertIn("var(--status", regel)

    def test_die_objektansicht_traegt_dieselbe_kante(self):
        """`04`: die Ansicht uebernimmt die Bausteine, damit Liste und Ansicht
        nicht auseinanderlaufen."""
        objekt = Objekt.objects.create(url="https://x/1", status=Status.BESICHTIGUNG)
        klassen = _klassen_von(
            self.client.get(reverse("objekt", args=[objekt.pk])), "kopfblock"
        )
        self.assertEqual(klassen, [["kopfblock", "status-besichtigung"]])


class SchriftTests(TestCase):
    """Zeuge 12: die Schrift kommt aus `static/`, nicht von einem fremden Server.

    Das Projekt speichert bewusst keine Kontaktdaten und haelt sich
    datenschutzrechtlich zurueck. Ein Schriftabruf bei jedem Seitenaufruf
    uebermittelte bei jedem Aufruf die Adresse des Abrufenden an einen
    Dritten - genau das, was hier nicht stattfinden soll. Der Entwurf laedt
    Archivo von Google Fonts; das ist die eine Stelle, an der ihm
    ausdruecklich nicht gefolgt wird.

    Gemessen an drei Seiten: die Datei ist wirklich da, das Stylesheet nennt
    nur sie, und keine Vorlage verweist nach draussen.
    """

    #: Wo die Datei liegt. Ausgeschrieben und nicht aus dem Stylesheet
    #: abgeleitet: der Zeuge soll melden, wenn sie umzieht, und nicht
    #: stillschweigend mitwandern.
    DATEI = "schriften/archivo-variabel.woff2"

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _quelle(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8")

    def _font_face(self):
        quelle = re.sub(r"/\*.*?\*/", "", self._quelle(), flags=re.S)
        bloecke = re.findall(r"@font-face\s*\{([^{}]*)\}", quelle)
        self.assertNotEqual(bloecke, [], "keine @font-face-Regel im Stylesheet")
        return bloecke

    # --- die Datei ist da -------------------------------------------------

    def test_die_schriftdatei_ist_ueber_die_static_konfiguration_auffindbar(self):
        """Der eigentliche Zeuge. Eine Pruefung auf die Zeichenkette im
        Stylesheet bliebe gruen, wenn die Datei fehlte - die Seite faellt dann
        stumm auf den Systemstack zurueck, und niemandem meldet sich etwas."""
        self.assertIsNotNone(finders.find(self.DATEI))

    def test_die_datei_ist_wirklich_eine_woff2(self):
        """Riegel gegen eine leere oder falsch benannte Datei.

        `woff2` beginnt mit der Signatur `wOF2`. Eine `ttf` unter diesem Namen
        laedt der Browser nicht, und die Seite faellt wieder stumm zurueck.
        """
        with open(finders.find(self.DATEI), "rb") as datei:
            self.assertEqual(datei.read(4), b"wOF2")

    def test_das_stylesheet_verweist_auf_genau_diese_datei(self):
        self.assertIn(self.DATEI, "".join(self._font_face()))

    # --- kein fremder Server ---------------------------------------------

    def test_keine_schriftquelle_zeigt_auf_eine_fremde_domain(self):
        """Zeuge 12, Kern. Gemessen an JEDER `url()` des Stylesheets, nicht
        nur an denen in `@font-face`: ein Hintergrundbild von einem fremden
        Server waere derselbe Vorgang."""
        quelle = re.sub(r"/\*.*?\*/", "", self._quelle(), flags=re.S)
        for ziel in re.findall(r"url\(\s*['\"]?([^'\")]+)", quelle):
            with self.subTest(ziel=ziel):
                self.assertFalse(ziel.startswith(("http:", "https:", "//")), ziel)

    def test_das_stylesheet_importiert_nichts(self):
        """`@import` ist der zweite Weg, von dem aus ein fremder Server
        nachgeladen wird - und der leisere."""
        self.assertNotIn("@import", re.sub(r"/\*.*?\*/", "", self._quelle(), flags=re.S))

    def test_keine_vorlage_verweist_auf_einen_fremden_server(self):
        """`<link>` und `preconnect` im Kopf, wie der Entwurf sie hat.

        Geprueft werden ALLE Vorlagen des Projekts, nicht nur `basis.html`:
        eine einzelne Seite koennte sich ihren eigenen Kopf bauen.
        """
        for vorlage in (settings.BASE_DIR / "templates").rglob("*.html"):
            with self.subTest(vorlage=vorlage.name):
                # Kommentare heraus: sie nennen den Entwurf und das, was
                # bewusst NICHT gebaut wurde - `preconnect` und Google Fonts
                # stehen namentlich darin. Ein Zeuge, der einen Kommentar
                # misst, misst gar nichts.
                inhalt = re.sub(
                    r"{% comment %}.*?{% endcomment %}", "", vorlage.read_text(encoding="utf-8"), flags=re.S
                )
                inhalt = re.sub(r"{#.*?#}", "", inhalt)
                for tag in re.findall(r"<link[^>]*>", inhalt):
                    self.assertNotRegex(tag, r'href="(https?:)?//')
                    self.assertNotIn("preconnect", tag)

    def test_die_ausgelieferte_seite_traegt_keinen_fremden_verweis(self):
        """Am gerenderten HTML gemessen, nicht nur an den Dateien."""
        inhalt = self.client.get("/").content.decode()
        for tag in re.findall(r"<link[^>]*>", inhalt):
            with self.subTest(tag=tag):
                self.assertNotRegex(tag, r'href="(https?:)?//')

    def test_es_gibt_ueberhaupt_einen_link_im_kopf(self):
        """Riegel gegen den Zeugen darueber im Vakuum: faende er gar kein
        `<link>`, bliebe er gruen, auch wenn das Stylesheet fehlte."""
        self.assertRegex(self.client.get("/").content.decode(), r"<link[^>]*stylesheet")

    # --- was die Schrift leisten muss ------------------------------------

    def test_die_datei_deckt_die_vier_schnitte_ab(self):
        """400, 500, 600, 700 - aus EINER variablen Datei.

        Der Bereich in `font-weight` ist die Zusage: klemmte ihn jemand auf
        `400`, faenden die drei anderen Schnitte nicht mehr statt, und die
        Oberflaeche saehe ueberall gleich schwer aus.
        """
        block = "".join(self._font_face())
        treffer = re.search(r"font-weight:\s*(\d+)\s+(\d+)", block)
        self.assertIsNotNone(treffer, f"kein Gewichtsbereich in: {block}")
        von, bis = int(treffer.group(1)), int(treffer.group(2))
        for gewicht in (400, 500, 600, 700):
            with self.subTest(gewicht=gewicht):
                self.assertLessEqual(von, gewicht)
                self.assertGreaterEqual(bis, gewicht)

    def test_die_schrift_faellt_auf_den_systemstack_zurueck(self):
        """Faellt die Datei aus, sieht die Oberflaeche anders aus und
        funktioniert unveraendert. Ohne Rueckfall staende sie in der
        Standardschrift des Browsers - und die traegt keine Tabellenziffern."""
        quelle = re.sub(r"\s+", " ", self._quelle())
        regel = quelle[quelle.index("body {") :]
        regel = regel[: regel.index("}")]
        self.assertIn("Archivo", regel)
        self.assertIn("system-ui", regel)

    def test_das_nachladen_blockiert_die_seite_nicht(self):
        """`font-display: swap`. Ohne ihn zeigt der Browser bis zu drei
        Sekunden lang gar keinen Text - am Handy im Zug eine leere Seite."""
        self.assertIn("font-display: swap", "".join(self._font_face()))

    def test_die_ziffern_stehen_untereinander(self):
        """Tabellenziffern auf dem ganzen Dokument.

        Ohne sie stehen 750.000 und 89.500 nicht untereinander, und genau das
        Untereinander ist der Zweck des Werkzeugs. Am `body` und nicht an der
        Liste: die Zahlen stehen inzwischen an vier Stellen, und eine spaeter
        dazukommende waere sonst wieder vergessen.
        """
        quelle = re.sub(r"\s+", " ", self._quelle())
        regel = quelle[quelle.index("body {") :]
        regel = regel[: regel.index("}")]
        self.assertIn("font-variant-numeric: tabular-nums", regel)

    # Hier sollte ein Zeuge stehen, der prueft, dass die Schriftdatei das
    # Merkmal `tnum` WIRKLICH fuehrt. `font-variant-numeric: tabular-nums` ist
    # nur eine Bitte an die Schrift; fuehrt die Datei das Merkmal nicht, steht
    # die Regel da und die Ziffern stehen trotzdem nicht untereinander.
    #
    # Er ist nicht baubar, ohne eine Abhaengigkeit dazuzunehmen. In einer
    # `woff2` sind auch die Tabellenverzeichnisse brotli-gepackt; die
    # Merkmalskennungen stehen nirgends im Klartext. Sie zu lesen braucht
    # einen Brotli-Entpacker und einen Schriftparser - zwei Abhaengigkeiten
    # fuer einen Zeugen, und das Projekt haengt bislang an dreien insgesamt.
    #
    # Geprueft wurde es EINMAL, beim Beschaffen der Datei, mit `fontTools` in
    # einer weggeworfenen Umgebung: die Achse `wght` laeuft von 100 bis 900,
    # die Merkmalsliste enthaelt `tnum`, und alle Zeichen der spanischen und
    # deutschen Ortsnamen sind belegt. Das steht im Bericht und bleibt
    # unbewacht - wer die Datei austauscht, muss es erneut pruefen.


class ZahlenblockTests(TestCase):
    """Was ueber die Zahlen der Zeile festgelegt ist.

    Kein Test misst Abstand, Kontrast oder Rhythmus - diese hier halten die
    Festlegungen, die sich still zuruecknehmen liessen.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)
        Objekt.objects.create(
            url="https://x/1",
            titel="Finca",
            aktueller_preis=Decimal("199000"),
            wohnflaeche=Decimal("100"),
        )

    def _quelle(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8")

    def _regel(self, waehler):
        quelle = re.sub(r"\s+", " ", self._quelle())
        self.assertIn(f"{waehler} {{", quelle, f"{waehler} fehlt im Stylesheet")
        rest = quelle[quelle.index(f"{waehler} {{") :]
        return rest[: rest.index("}")]

    def _angaben(self):
        """`{Etikett: Klassenliste}` fuer jede Angabe der EINZIGEN Listenzeile.

        Ueber das ETIKETT geschluesselt und nicht ueber die Stellung im Block:
        eine dazwischengeschobene Angabe verschoebe jede Positionszahl, das
        Wort nicht. Und ueber die Klassenliste des Elements, nicht ueber eine
        `class="…"`-Zeichenkette - ein erweiterter Klassenname lief in diesem
        Projekt schon einmal an einem Zeugen vorbei.
        """
        inhalt = self.client.get("/").content.decode()
        gefunden = {}
        for klassen, block in re.findall(
            r'<div class="(zahl[^"]*)">(.*?)</div>', inhalt, re.S
        ):
            etikett = re.search(r'<span class="etikett">([^<]*)</span>', block)
            self.assertIsNotNone(etikett, f"Angabe ohne Etikett: {block}")
            gefunden[etikett.group(1)] = klassen.split()
        return gefunden

    def test_der_parser_findet_ueberhaupt_alle_vier_angaben(self):
        """Riegel gegen die Zeugen darunter im Vakuum.

        Faende die Ableitung nichts, waere `assertNotIn("haupt", ...)` von
        selbst gruen - ein Zeuge, der eine leere Liste durchsucht, misst
        nichts.
        """
        self.assertEqual(
            sorted(self._angaben()), ["Grundstück", "Kaufpreis", "Wohnfläche", "je m²"]
        )

    def test_der_kaufpreis_ist_die_groesste_zahl_der_zeile(self):
        """UMGEDREHT am 05.09., und das ist eine ausdrueckliche Entscheidung.

        Bis dahin trug "je m²" die Hauptgroesse: so setzt es der Entwurf, und
        bei Widerspruch zwischen Beschreibung und Datei galt die Datei. Die
        Entscheidung ist anders gefallen - der Kaufpreis ist die Zahl, an der
        eine Zeile haengenbleibt.

        Die Liste ist damit ausserdem mit der Objektansicht einig: dort steht
        der Kaufpreis schon immer groesser als alles andere auf der Seite.

        Die Reihenfolge der vier Angaben bleibt unveraendert; nur die
        Gewichtung wandert.
        """
        self.assertIn("haupt", self._angaben()["Kaufpreis"])

    def test_der_quadratmeterpreis_steht_daneben_in_normaler_groesse(self):
        """Die Gegenrichtung, und sie braucht einen eigenen Zeugen.

        Der Zeuge darueber faellt zwar auch, wenn die Hauptgroesse zurueck auf
        "je m²" wandert - aber nicht, wenn sie versehentlich an BEIDEN
        Angaben steht. Dann gaebe es zwei gleich grosse Zahlen und keine
        Hierarchie mehr.
        """
        self.assertNotIn("haupt", self._angaben()["je m²"])

    def test_genau_eine_angabe_traegt_die_hauptgroesse(self):
        """Zwei Hauptzahlen sind keine Hierarchie, null sind auch keine."""
        haupt = [name for name, klassen in self._angaben().items() if "haupt" in klassen]
        self.assertEqual(haupt, ["Kaufpreis"])

    def test_die_hauptzahl_ist_wirklich_groesser_gesetzt(self):
        """Die Klasse allein macht nichts groesser."""
        regel = self._regel(".zahl.haupt .wert")
        treffer = re.search(r"font-size:\s*([\d.]+)rem", regel)
        self.assertIsNotNone(treffer, regel)
        gross = float(treffer.group(1))
        normal = re.search(r"font-size:\s*([\d.]+)rem", self._regel(".zahl .wert"))
        self.assertIsNotNone(normal, "keine Groesse an `.zahl .wert`")
        self.assertGreater(gross, float(normal.group(1)))

    def test_kein_geldbetrag_bricht_um(self):
        """Weder zwischen Zahl und Waehrungszeichen noch innerhalb der Zahl.

        Gehalten von `white-space: nowrap` und nicht von geschuetzten
        Leerzeichen im Markup: die stuenden als unsichtbare Zeichen in jedem
        gespeicherten Auszug und in jedem Zeugen.

        An der ANGABE gemessen und nicht am Block. Der Block muss umbrechen
        duerfen: stand `nowrap` an ihm, lief die Reihe am Handy aus ihrer
        Spalte heraus, und der Preis je m² - die groesste Zahl der Zeile -
        war rechts abgeschnitten. Aufgefallen ist das bei der Sichtpruefung
        im Browser; ein Zeuge auf den Block haette es festgeschrieben.

        An `.zahl` UND an `.wert`: die aeussere Angabe allein genuegte,
        solange nichts darin sie ueberschreibt - und genau das kann passieren.
        """
        for waehler in (".zahl", ".zahl .wert", ".preisaenderung"):
            with self.subTest(waehler=waehler):
                self.assertIn("white-space: nowrap", self._regel(waehler))

    def test_eine_fehlende_zahl_steht_als_gedankenstrich(self):
        """Nicht als Luecke - sonst rutschen die Werte gegeneinander, und eine
        leere Stelle liest sich ausserdem wie eine Null."""
        Objekt.objects.all().delete()
        Objekt.objects.create(url="https://x/2", titel="Ohne Zahlen")
        inhalt = self.client.get("/").content.decode()
        block = inhalt[inhalt.index('<div class="zahlen">') :]
        # Vier Angaben, vier Striche.
        self.assertEqual(block[: block.index('<div class="votum">')].count("—"), 4)

    def test_der_gedankenstrich_ist_gedaempft(self):
        """Man muss SEHEN, dass nichts da ist - und nicht, dass dort etwas
        steht."""
        Objekt.objects.all().delete()
        Objekt.objects.create(url="https://x/2", titel="Ohne Zahlen")
        klassen = _klassen_von(self.client.get("/"), "fehlt")
        self.assertEqual(len(klassen), 4)
        self.assertIn("var(--linie-stark)", self._regel(".zahl .wert.fehlt"))


class TitelOhneTitelTests(TestCase):
    """Objekte ohne Titel zeigen die gekuerzte URL - gedaempft, nicht fett.

    Sie sind erkennbar unfertig, und ein fett gesetzter Link auf eine Adresse
    sieht aus wie eine Angabe.
    """

    def setUp(self):
        self.person = Person.objects.create_user("steffen", password="lang-genug-123")
        self.client.force_login(self.person)

    def _titelklassen(self):
        return _klassen_von(self.client.get("/"), "titel")

    def test_ohne_titel_traegt_der_verweis_die_nackte_form(self):
        Objekt.objects.create(url="https://x/nur-ein-link")
        self.assertEqual(self._titelklassen(), [["titel", "nackt"]])

    def test_mit_titel_traegt_er_sie_nicht(self):
        """Riegel: sonst saehe jede Zeile unfertig aus."""
        Objekt.objects.create(url="https://x/1", titel="Finca bei Ronda")
        self.assertEqual(self._titelklassen(), [["titel"]])

    def test_die_nackte_form_ist_gedaempft_und_ungefettet(self):
        quelle = re.sub(r"\s+", " ", (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8"))
        regel = quelle[quelle.index(".titel.nackt {") :]
        regel = regel[: regel.index("}")]
        self.assertIn("var(--gedaempft)", regel)
        self.assertIn("font-weight: 400", regel)

    def test_der_titel_ist_sonst_gefettet(self):
        """Riegel unter dem Zeugen darueber: waere der Titel ohnehin nicht
        gefettet, saegte die nackte Form nichts aus."""
        quelle = re.sub(r"\s+", " ", (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8"))
        regel = quelle[quelle.index(".titel {") :]
        regel = regel[: regel.index("}")]
        treffer = re.search(r"font-weight:\s*(\d+)", regel)
        self.assertIsNotNone(treffer, regel)
        self.assertGreater(int(treffer.group(1)), 400)

    def test_die_url_steht_unveraendert_da(self):
        """Gekuerzt wird im Stylesheet und nicht im Markup.

        Der Entwurf zeigt "immowelt.de/expose/ecd16e27-fa4b-…" - ohne Schema
        und mit Auslassungszeichen. Das Auslassungszeichen kommt aus
        `text-overflow`; das Schema wegzuschneiden waere eine zweite Formel
        neben `__str__`, und zwei Formeln fuer eine Regel driften.
        """
        Objekt.objects.create(url="https://x/nur-ein-link")
        self.assertContains(self.client.get("/"), ">https://x/nur-ein-link</a>")


class VotumpunkteTests(ListenTestBasis):
    """Die Punktreihe: eine je Person, plus der bisherige Text.

    Sie steht nur, wo der Zaehlstand ohnehin steht - sie IST der Zaehlstand in
    anderer Form. Das haelt `VerdecktesVotumInDerListeTests`; hier geht es
    darum, dass sie richtig ist.
    """

    def setUp(self):
        super().setUp()
        self.andere = [
            Person.objects.create_user(f"person{n}", first_name=f"P{n}") for n in range(4)
        ]
        self.objekt = self._objekt(titel="Zur Abstimmung")

    def _punkte(self):
        zeilen = VotumzellenParser.nach_href(self.client.get("/"))
        return zeilen[reverse("objekt", args=[self.objekt.pk])]["punkte"]

    def _votum(self, person, wertung):
        Votum.objects.update_or_create(
            objekt=self.objekt, person=person, defaults={"wertung": wertung}
        )

    def test_ein_punkt_je_person(self):
        """Fuenf aktive Personen, fuenf Punkte - auch wenn nur einer gestimmt hat."""
        self._votum(self.person, Wertung.DAFUER)
        self.assertEqual(len(self._punkte()), Person.objects.filter(is_active=True).count())

    def test_die_abgegebenen_stimmen_stehen_vorn(self):
        self._votum(self.person, Wertung.DAFUER)
        self._votum(self.andere[0], Wertung.RAUS)
        self.assertEqual(self._punkte(), ["dafuer", "raus", "offen", "offen", "offen"])

    def test_die_reihenfolge_verraet_nicht_wer_wann_gestimmt_hat(self):
        """Die Punkte stehen nach WERTUNG sortiert, nicht nach Zeitpunkt.

        Eine zeitliche Reihenfolge waere eine Angabe ueber Personen: wer
        zuerst gestimmt hat, liesse sich bei fuenf Leuten aus zwei Aufrufen
        ablesen. Wer wie gestimmt hat, steht in der Objektansicht - und dort
        erst nach der Freischaltung.
        """
        self._votum(self.person, Wertung.RAUS)
        self._votum(self.andere[0], Wertung.DAFUER)
        # Zuerst "raus", dann "dafür" - die Reihe zeigt trotzdem "dafür" vorn.
        self.assertEqual(self._punkte()[:2], ["dafuer", "raus"])

    def test_die_punkte_stimmen_mit_dem_text_ueberein(self):
        """Zwei Traeger derselben Auskunft. Liefen sie auseinander, saehe man
        drei Punkte und laese "2 dafür"."""
        for person, wertung in (
            (self.person, Wertung.DAFUER),
            (self.andere[0], Wertung.DAFUER),
            (self.andere[1], Wertung.ANSCHAUEN),
        ):
            self._votum(person, wertung)
        zeile = VotumzellenParser.nach_href(self.client.get("/"))[
            reverse("objekt", args=[self.objekt.pk])
        ]
        self.assertEqual(zeile["text"], "2 dafür · 1 anschauen · 2 offen")
        self.assertEqual(
            zeile["punkte"], ["dafuer", "dafuer", "anschauen", "offen", "offen"]
        )

    def test_mehr_stimmen_als_aktive_personen_ergeben_keine_negativen_punkte(self):
        """Wer nach seinem Votum stillgelegt wurde, zaehlt nicht mehr zu den
        aktiven Personen - sein Votum steht aber weiter da."""
        for person in [self.person, *self.andere]:
            self._votum(person, Wertung.DAFUER)
        Person.objects.filter(pk=self.andere[0].pk).update(is_active=False)
        self.assertEqual(self._punkte(), ["dafuer"] * 5)

    def test_jeder_punkt_traegt_ein_zeichen(self):
        """Farbe allein traegt keine Bedeutung.

        Wer die Toene nicht unterscheidet, saehe fuenf gleiche Kreise. Der
        offene Punkt traegt ausdruecklich KEINES - ein leerer Kreis ist die
        Aussage.
        """
        self._votum(self.person, Wertung.DAFUER)
        inhalt = self.client.get("/").content.decode()
        block = inhalt[inhalt.index('<span class="stimmen">') :]
        block = block[: block.index("</span></span>") + len("</span></span>")]
        for name, _wertung, _wort, zeichen in views.VOTUM_ZAEHLUNGEN:
            with self.subTest(name=name):
                self.assertNotEqual(zeichen, "")

    def test_jeder_punkt_traegt_seine_beschriftung_im_title(self):
        """Das Zeichen allein sagt nicht, was es heisst."""
        self._votum(self.person, Wertung.DAFUER)
        inhalt = self.client.get("/").content.decode()
        self.assertIn('title="dafür"', inhalt)
        self.assertIn('title="offen"', inhalt)
