"""Zusagen des Datenmodells. Laeuft gegen Postgres, nicht gegen SQLite.

Der Unterschied ist nicht kosmetisch: SQLite liefert bei einer Division durch
null still NULL, Postgres wirft `division_by_zero`. Der Riegel gegen
Wohnflaeche 0 und der partielle Unique-Constraint sind nur hier bezeugt.

Je Zusage eine eigene Testmethode. Zwei Assertions in einer Methode messen die
zweite nicht mehr, sobald die erste faellt.
"""

import ast
import html as htmlwerkzeug
import inspect
import re
import textwrap
from datetime import date, timedelta
from html.parser import HTMLParser
from importlib import import_module
from urllib.parse import parse_qs, urlparse
from unittest import mock
from decimal import Decimal

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import FieldError
from django.db import IntegrityError, connection, migrations, transaction
from django.db.migrations.executor import MigrationExecutor
from django.forms import modelform_factory
from django.templatetags.static import static
from django.urls import reverse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.html import escape

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
from .forms import STATUS_VORBELEGUNG
from .models import Notiz, Objekt, Preisverlauf, Votum
from .portale import portal_und_id

#: Der Modulname faengt mit einer Ziffer an - ein `import` schreibt sich dafuer
#: nicht hin. Der Zugriff ist noetig, weil die Zeugen unten die Funktion der
#: Migration aufrufen und getrennt davon pruefen, dass die Migration genau
#: diese Funktion auch ausfuehrt.
nachtragsmigration = import_module("objekte.migrations.0003_portal_und_inserats_id_nachtragen")

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
        self.assertContains(antwort, f'<a href="/objekt/{pk}/">idealista · 12345</a>')

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

    def test_der_einwerfer_steht_ohne_zusatzabfrage_bereit(self):
        """`select_related("eingestellt_von")` - und der Zeuge, der ihn haelt.

        Die Gegenprobe zu Punkt 5 hat gezeigt: den Aufruf zu entfernen liess
        die ganze Testreihe gruen. Er war eine blinde Zusage, weil die Vorlage
        das Feld heute nicht anzeigt - und die erste Spalte "eingeworfen von"
        braechte damit ein N+1 mit, das niemand kommen sieht.

        Gemessen wird der ZUGRIFF, nicht die Anwesenheit des Aufrufs: ein
        Strukturtest auf `select_related` im Quelltext bewiese nur, dass da
        ein Wort steht.
        """
        for nummer in range(5):
            Objekt.objects.create(url=f"https://x/{nummer}", eingestellt_von=self.person)
        objekte = self._seite().context["objekte"]
        with self.assertNumQueries(0):
            for objekt in objekte:
                self.assertEqual(objekt.eingestellt_von, self.person)

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

    def test_leere_felder_werden_ausgelassen(self):
        # Nicht als "—" gezeigt. Eine Liste aus zwanzig Gedankenstrichen
        # verdeckt die drei Zeilen, die tatsaechlich etwas sagen.
        self.assertNotContains(self._seite(), "Baujahr")

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
        anna = Person.objects.create_user("anna", first_name="Anna", last_name="B.")
        Votum.objects.create(
            objekt=self.objekt, person=anna, wertung=Wertung.DAFUER, begruendung="Lage"
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

    def test_idealista_italien_mit_spanischem_pfad(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.it/inmueble/12345/"),
            ("idealista", "12345"),
        )

    def test_idealista_portugal_mit_spanischem_pfad(self):
        self.assertEqual(
            portal_und_id("https://www.idealista.pt/inmueble/12345/"),
            ("idealista", "12345"),
        )

    def test_ein_landessprachlicher_pfad_wird_NICHT_erkannt(self):
        """Haelt eine gemeldete Luecke fest, statt sie zu behaupten.

        Die Spezifikation nennt `.it` und `.pt` als Domains, gibt aber nur das
        spanische Pfadmuster `inmueble` vor. Ob die beiden Laenderseiten diesen
        Pfad ueberhaupt ausliefern, ist unbelegt - eine echte URL lag nicht vor.
        Solange das so ist, laufen `.it` und `.pt` ins Leere, und dieser Zeuge
        sagt genau das. Faellt er, weil jemand ein Muster nachgetragen hat, ist
        das kein Schaden, sondern der Anlass, ihn zu ersetzen.
        """
        self.assertEqual(
            portal_und_id("https://www.idealista.it/immobile/12345/"), ("", "")
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


class SpaltenParser(HTMLParser):
    """Liest Spaltenkoepfe und `data-spalte` je Zeile aus der Objektliste.

    Von Hand statt mit einer Bibliothek: das Projekt haengt an Django,
    psycopg und dotenv - eine vierte Abhaengigkeit fuer einen Zeugen waere ein
    schlechter Tausch.
    """

    def __init__(self):
        super().__init__()
        self.kopf = []      # Text je <th> im <thead>
        self.zellen = []    # Liste der data-spalte-Werte je <tbody>-Zeile
        self._im_thead = False
        self._im_th = False
        self._text = ""
        self._zeile = None

    def handle_starttag(self, tag, attrs):
        werte = dict(attrs)
        if tag == "thead":
            self._im_thead = True
        elif tag == "th" and self._im_thead:
            self._im_th = True
            self._text = ""
        elif tag == "tr" and not self._im_thead:
            self._zeile = []
        elif tag == "td" and self._zeile is not None:
            self._zeile.append(werte.get("data-spalte"))

    def handle_endtag(self, tag):
        if tag == "thead":
            self._im_thead = False
        elif tag == "th" and self._im_th:
            self._im_th = False
            self.kopf.append(self._text.strip())
        elif tag == "tr" and self._zeile is not None:
            self.zellen.append(self._zeile)
            self._zeile = None

    def handle_data(self, daten):
        if self._im_th:
            self._text += daten


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

    def _geparst(self):
        Objekt.objects.create(url="https://x/1", titel="Erstes", ort="Palma")
        Objekt.objects.create(url="https://x/2", titel="Zweites", ort="Sóller")
        parser = SpaltenParser()
        parser.feed(self._seite().content.decode())
        return parser

    def test_die_liste_hat_ueberhaupt_spaltenkoepfe(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darunter.

        Faende der Parser keinen Kopf, verglichen die Zusage-11-Zeugen zwei
        leere Listen und blieben gruen - auch wenn kein einziges `data-spalte`
        im Template steht.
        """
        self.assertNotEqual(self._geparst().kopf, [])

    def test_die_liste_hat_ueberhaupt_zeilen(self):
        self.assertNotEqual(self._geparst().zellen, [])

    def test_jede_zelle_traegt_die_bezeichnung_ihres_spaltenkopfs(self):
        """Ohne diesen Zeugen faellt eine spaeter ergaenzte Spalte in der
        Kartenansicht ohne Bezeichnung heraus, und niemand merkt es.

        Verglichen wird gegen den TEXT des Spaltenkopfs, nicht gegen dessen
        eigenes `data-spalte`: sonst pruefte der Zeuge zwei Attribute
        gegeneinander, die man gemeinsam falsch setzen kann.
        """
        geparst = self._geparst()
        self.assertEqual(geparst.zellen, [geparst.kopf] * len(geparst.zellen))

    def test_das_stylesheet_nennt_ueberhaupt_benannte_spalten(self):
        self.assertNotEqual(self._benannte_spalten_aus_dem_stylesheet(), set())

    def test_die_benannten_spalten_des_stylesheets_gibt_es_in_der_liste(self):
        """Das Stylesheet richtet die Zahlenspalten ueber ihren NAMEN aus.

        `nth-child` waere gegen eine spaeter eingeschobene Spalte blind - der
        Name ist es nicht. Der Preis dafuer: eine Umbenennung faellt STILL aus,
        die Regel greift dann einfach nicht mehr und die Zahlen stehen wieder
        linksbuendig. Dieser Zeuge ist der Riegel dagegen.

        Die Namen werden aus der CSS-Datei GELESEN, nicht hier wiederholt -
        eine zweite Liste driftet von der ersten weg.
        """
        self.assertLessEqual(
            self._benannte_spalten_aus_dem_stylesheet(), set(self._geparst().kopf)
        )

    def _benannte_spalten_aus_dem_stylesheet(self):
        quelle = (settings.BASE_DIR / "static" / "objektradar.css").read_text(
            encoding="utf-8"
        )
        return set(re.findall(r'\[data-spalte="([^"]+)"\]', quelle))

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
    der Blick auf den Bildschirm - diese Zeugen halten nur die beiden
    Festlegungen fest, die sich still zuruecknehmen liessen.
    """

    def _quelle(self):
        return (settings.BASE_DIR / "static" / "objektradar.css").read_text(encoding="utf-8")

    def _block_ab_48rem(self):
        """Der Inhalt des `@media (min-width: 48rem)`-Blocks, ueber Klammerzaehlung.

        Ohne diese Eingrenzung koennte die Kappungsregel auf oberster Ebene
        stehen und der Zeuge bliebe gruen - waehrend die Kartenansicht unter
        48rem ihre Objektzeile auf eine Zeile zusammenzoege und abschnitte.
        """
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
                    return quelle[offen : stelle + 1]
        raise AssertionError("Der Media-Block ist nicht geschlossen.")

    # --- 4.1: die Objektspalte kappen -------------------------------------

    def test_der_media_block_ist_ueberhaupt_auffindbar(self):
        """Riegel gegen einen vakuum-gruenen Zeugen darunter.

        Waere der Block leer, faende `assertIn` nichts und meldete sich - aber
        `_block_ab_48rem` wuerfe schon vorher. Dieser Zeuge macht sichtbar,
        welcher der beiden Faelle vorliegt.
        """
        self.assertNotEqual(self._block_ab_48rem().strip("{} \n"), "")

    def test_die_objektzelle_wird_ab_48rem_gekappt(self):
        block = self._block_ab_48rem()
        regel = block[block.index('[data-spalte="Objekt"]') :]
        regel = regel[: regel.index("}")]
        for eigenschaft in (
            "max-width: 22rem",
            "overflow: hidden",
            "text-overflow: ellipsis",
            "white-space: nowrap",
        ):
            with self.subTest(eigenschaft=eigenschaft):
                self.assertIn(eigenschaft, regel)

    def test_unter_48rem_bleibt_die_objektzelle_ungekappt(self):
        """Die Kappungsregel steht NUR im Media-Block.

        In der Kartenansicht ist die Objektzelle die Ueberschrift der Karte und
        hat die ganze Zeilenbreite - dort ist Platz, dort darf sie umbrechen.
        """
        quelle = self._quelle()
        ausserhalb = quelle.replace(self._block_ab_48rem(), "")
        self.assertNotIn('[data-spalte="Objekt"]', ausserhalb)

    # --- 4.2: Fehlerfarbe von der Preissenkung trennen --------------------

    def test_es_gibt_eine_eigene_fehlerfarbe(self):
        self.assertIn("--fehler:", self._quelle())

    def test_die_meldungsregel_greift_auf_die_fehlerfarbe_zu(self):
        quelle = self._quelle()
        regel = quelle[quelle.index(".meldungen li.error") :]
        regel = regel[: regel.index("}")]
        self.assertIn("var(--fehler)", regel)

    def test_die_signalfarbe_wird_nirgends_verwendet(self):
        """`--signal` bleibt der Preissenkung vorbehalten.

        Gemessen an `var(--signal)`, nicht an `--signal` - die Definition
        selbst soll ja stehen bleiben. Faellt dieser Zeuge, hat sich die
        lauteste Farbe des Werkzeugs an eine Stelle gesetzt, an der sie das
        Kaufsignal abstumpft.
        """
        self.assertNotIn("var(--signal)", self._quelle())


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

    ANDERE_SCHREIBWEISE = "https://www.idealista.it/en/inmueble/12345?utm_source=mail"

    def test_die_vorschau_erkennt_das_bestehende_objekt_ueber_den_schluessel(self):
        """Andere Laenderdomain, Sprachpraefix, Tracking-Parameter, kein
        abschliessender Schraegstrich - und trotzdem dasselbe Inserat.

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

    def test_die_leiste_traegt_jeden_schluessel_in_beide_richtungen(self):
        antwort = self._seite()
        for schluessel in views.SORTIERSCHLUESSEL:
            for wert in (schluessel, f"-{schluessel}"):
                with self.subTest(wert=wert):
                    self.assertContains(antwort, escape(f"sortierung={wert}"))


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

    def test_eine_ungueltige_seitenzahl_faellt_auf_seite_eins(self):
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertEqual(self._seite("/?seite=abc").context["page_obj"].number, 1)

    def test_eine_zu_hohe_seitenzahl_faellt_auf_seite_eins(self):
        """Nicht auf die LETZTE Seite.

        `Paginator.get_page()` schickt eine zu hohe Zahl auf die letzte Seite;
        das ist ein anderes Versprechen. Faellt dieser Zeuge, ist vermutlich
        `get_page()` an die Stelle von `page()` getreten.
        """
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertEqual(self._seite("/?seite=900").context["page_obj"].number, 1)

    def test_eine_negative_seitenzahl_faellt_auf_seite_eins(self):
        self._viele(3)
        with mock.patch.object(views, "OBJEKTE_JE_SEITE", 2):
            self.assertEqual(self._seite("/?seite=-4").context["page_obj"].number, 1)

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
        parameter = self._sortierlink("/?land=ES", "-aktueller_preis")
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

    def test_die_spalte_steht_in_der_liste(self):
        self._stimmen([Wertung.DAFUER, Wertung.RAUS])
        antwort = self._seite()
        self.assertContains(antwort, 'data-spalte="Votum"')
        self.assertContains(antwort, "1 dafür · 1 raus · 3 offen")

    # --- der Riegel gegen das Kreuzprodukt --------------------------------

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
