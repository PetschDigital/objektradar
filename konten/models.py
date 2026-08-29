from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

# Ab dieser Luecke zwischen zwei Aufrufen beginnt ein neuer Besuch. Kuerzere
# Abstaende sind Weiterklicken innerhalb desselben Besuchs.
BESUCHSPAUSE = timedelta(minutes=30)


class Person(AbstractUser):
    """Konto einer der vier bis fuenf Personen der Gruppe.

    Keine Rollen, alle duerfen alles. Konten werden von Hand angelegt
    (`make superuser`), es gibt bewusst keinen Registrierungsweg.
    """

    letzter_besuch = models.DateTimeField(
        "letzte Aktivität",
        null=True,
        blank=True,
        help_text="Wird bei jedem Aufruf fortgeschrieben. Nicht von Hand ändern.",
    )
    besuch_davor = models.DateTimeField(
        "letzte Aktivität im vorherigen Besuch",
        null=True,
        blank=True,
        help_text='Schwelle für "Neu seit deinem letzten Besuch".',
    )

    class Meta:
        verbose_name = "Person"
        verbose_name_plural = "Personen"
        ordering = ["username"]

    def __str__(self):
        return self.anzeigename

    @property
    def anzeigename(self):
        return self.get_full_name() or self.username

    @property
    def neu_seit(self):
        """Zeitschwelle fuer "seit deinem letzten Besuch" - oder None.

        None heisst auf Datenebene genau eines: es ist kein vorheriger Besuch
        bekannt. Entweder war noch keiner da, oder er liegt vor der Einfuehrung
        der Besuchszeiten.

        Es heisst ausdruecklich NICHT "also ist alles neu". Was eine Ansicht
        aus einer fehlenden Schwelle macht - alles markieren oder nichts -,
        entscheidet die Ansicht. Beide Lesarten sind vertretbar, und genau
        deshalb gehoert die Entscheidung dorthin, wo man sie sieht, und nicht
        als Nebensatz in eine Property.
        """
        return self.besuch_davor

    def besuch_registrieren(self, jetzt=None):
        """Aktivitaet fortschreiben. Gibt zurueck, ob ein neuer Besuch begann.

        `letzter_besuch` ist die LETZTE AKTIVITAET und wird bei jedem Aufruf
        geschrieben. Die Luecke wird gegen diesen Wert gemessen, nicht gegen
        den Beginn des Besuchs - sonst rotiert die Schwelle mitten in einer
        langen Sitzung, weil der Abstand zum Besuchsbeginn irgendwann von
        allein BESUCHSPAUSE ueberschreitet. Gemessen an sieben Aufrufen im
        Abstand von zehn Minuten: die Schwelle wanderte in einer Stunde
        zweimal, obwohl niemand pausiert hatte.

        Erst wenn zwischen zwei Aufrufen BESUCHSPAUSE liegt, wandert die
        bisherige letzte Aktivitaet nach `besuch_davor` und wird damit zur
        Schwelle fuer "Neu seit deinem letzten Besuch".
        """
        jetzt = jetzt or timezone.now()
        neuer_besuch = (
            self.letzter_besuch is not None
            and jetzt - self.letzter_besuch >= BESUCHSPAUSE
        )
        if neuer_besuch:
            self.besuch_davor = self.letzter_besuch
        self.letzter_besuch = jetzt
        self.save(update_fields=["letzter_besuch", "besuch_davor"])
        return neuer_besuch
