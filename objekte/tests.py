"""Zusagen des Datenmodells. Laeuft gegen Postgres, nicht gegen SQLite.

Der Unterschied ist nicht kosmetisch: SQLite liefert bei einer Division durch
null still NULL, Postgres wirft `division_by_zero`. Der Riegel gegen
Wohnflaeche 0 und der partielle Unique-Constraint sind nur hier bezeugt.

Je Zusage eine eigene Testmethode. Zwei Assertions in einer Methode messen die
zweite nicht mehr, sobald die erste faellt.
"""

import ast
import re
from datetime import date, timedelta
from importlib import import_module
from unittest import mock
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import FieldError
from django.db import IntegrityError, connection, migrations, transaction
from django.db.migrations.executor import MigrationExecutor
from django.forms import modelform_factory
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import portale, views
from .choices import Land, PreisQuelle, Portal, Quelle, Status, Wertung, Zustand
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
        antwort = self._einwerfen(follow=True)
        self.assertContains(antwort, "https://www.idealista.com/inmueble/12345/")

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
        """Riegel gegen ein N+1 beim naechsten Spaltenzuwachs.

        Gezaehlt wird der Unterschied, nicht die absolute Zahl: Sitzung und
        Besuchs-Middleware fragen ohnehin mit, und deren Zahl ist nicht die
        Zusage, die hier gehalten werden soll.
        """
        self._seite()  # Aufwaermen, damit Verbindungsaufbau nicht mitzaehlt.
        Objekt.objects.create(url="https://x/1")
        with CaptureQueriesContext(connection) as mit_einem:
            self._seite()
        for nummer in range(2, 8):
            Objekt.objects.create(url=f"https://x/{nummer}")
        with CaptureQueriesContext(connection) as mit_sieben:
            self._seite()
        self.assertEqual(len(mit_sieben), len(mit_einem))


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
