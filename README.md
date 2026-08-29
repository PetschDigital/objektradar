# Objektradar

Internes Werkzeug zur gemeinsamen Objektsuche. Maßgeblich für den Bau ist
`../BAUSPEC_Objektradar_Schritt1.md`, die Wissensbasis liegt in `../Systemdateien/`.

Stand: Schritt 1, Gerüst und Datenmodell. Oberfläche noch nicht gebaut.

## Einrichten

```
cp .env.example .env          # Werte eintragen
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
make db-up
make migrate
make superuser                # Konten werden von Hand angelegt
make run
```

## Interface

`make` ist das Interface, `manage.py` wird nicht direkt aufgerufen.

    make db-up          Postgres-Container starten
    make check          Django-Systemprüfung
    make makemigrations APP=objekte
    make migrate
    make superuser      Konto anlegen (es gibt keinen Registrierungsweg)
    make run

## Aufbau

    config/     Einstellungen, URLs
    konten/     Person (AbstractUser) - Konten und Besuchszeiten
    objekte/    Objekt, Bild, Preisverlauf, Statusaenderung, Votum, Notiz

## Festlegungen, die man beim Lesen kennen muss

- **Einziges Pflichtfeld am Objekt ist `url`.** Alles andere darf leer bleiben.
  Ein Objekt, von dem nur der Link bekannt ist, muss speicherbar sein.
- **Der Preisverlauf führt.** `Objekt.aktueller_preis` ist eine Kopie davon,
  kein Eingabefeld: `save()` legt den Eintrag nur beim Anlegen an und liest die
  Spalte danach aus der Datenbank nach, statt sie zu schreiben. Geändert wird
  der Preis über `Objekt.preis_setzen(person, preis, quelle)`. Ohne das dreht
  eine veraltete Instanz die letzte Senkung lautlos zurück.
- **€/m² ist kein Feld**, sondern die Annotation `qm_preis` aus
  `Objekt.objects.mit_qm_preis()`. Es gibt bewusst keine gleichnamige Property.
- **Verworfene Objekte werden nicht gelöscht**, nur ausgeblendet
  (`Objekt.objects.sichtbar()`).
- **Der Status wird immer manuell gesetzt**, nie aus den Vota abgeleitet.
  `Objekt.status_setzen(person, status)` protokolliert die Änderung.
- **Preis und Status sind im Admin readonly.** Beide haben eine
  Historientabelle, die das Formular nicht füllen würde. Der Preis wird über
  den Preisverlauf-Inline gesetzt, der Status über eine Admin-Action je Status
  — die geht über `status_setzen()` und protokolliert deshalb.
- **`aktueller_preis` ist `editable=False`** und taucht in keinem Formular auf.
  Wer es ausdrücklich in ein `ModelForm` aufnimmt, bekommt einen `FieldError` —
  laut statt still.
- **Personen werden nicht gelöscht**, sondern auf `is_active = False` gesetzt.
  Die Fremdschlüssel stehen auf `PROTECT`, damit kein Votum und keine
  Statusänderung stillschweigend verschwindet.

## Was nicht gebaut wird

Kein Scraping der Portale, keine Kontaktdaten aus Inseraten, kein Rollen- oder
Rechtekonzept, keine Renovierungskalkulation. Begründung in
`../Systemdateien/03_Technik.md`.
