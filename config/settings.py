"""Django-Einstellungen fuer Objektradar.

Zugangsdaten kommen ausschliesslich aus .env, nie aus dieser Datei.
"""

from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    wert = os.environ.get(name, default)
    if wert is None:
        raise RuntimeError(f"{name} fehlt in .env (Vorlage: .env.example)")
    return wert


SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG", "False").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "konten",
    "objekte",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Jede Ansicht verlangt eine Anmeldung, ausser sie traegt ausdruecklich
    # `login_not_required`. Andersherum - ein Dekorator je Ansicht - waere eine
    # vergessene Ansicht offen, und nichts wuerde sich melden. Die Liste der
    # oeffentlichen Ansichten ist deshalb ein Test, kein Blick ins Urlconf.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    # Schreibt die Aktivitaet fort. Steht VOR der Ansicht, damit die Schwelle
    # fuer "neu seit deinem letzten Besuch" schon gedreht ist, wenn die Ansicht
    # sie liest.
    "konten.middleware.BesuchMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5433"),
    }
}

# Custom User von Anfang an - nachtraeglich in Django praktisch nicht mehr aenderbar.
AUTH_USER_MODEL = "konten.Person"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "objektliste"
LOGOUT_REDIRECT_URL = "login"

# Ein Jahr Sitzung, bei jedem Request verlaengert: jeder meldet sich einmal an
# und danach nicht wieder.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# Der Zaehler des Anmelde-Rate-Limits liegt im Cache, nicht in einer eigenen
# Tabelle des Datenmodells: die waere eine Migration, und `django-axes` waere
# eine Abhaengigkeit.
#
# Der Cache liegt in der DATENBANK, nicht im Prozessspeicher. Gunicorn laeuft
# mit zwei Arbeitsprozessen; `LocMemCache` ist prozesslokal, das Limit gaelte
# je Prozess und die tatsaechliche Zahl der Fehlversuche verdoppelte sich.
# Gegen Redis spricht ein zweiter Dienst, der laufen, ueberwacht und
# abgesichert werden muesste - die Schreiblast sind wenige Zeilen je
# Anmeldeversuch.
#
# Die Tabelle legt `make createcachetable` an. Im Testlauf uebernimmt das
# Djangos Testrunner selbst.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

# Rate-Limit auf dem Login. Fuenf Fehlversuche je Absender und Benutzername,
# danach eine Viertelstunde zu.
LOGIN_VERSUCHE = 5
LOGIN_SPERRE_SEKUNDEN = 15 * 60

# Steht ein Reverse Proxy davor? Standard ist NEIN. Eingeschaltet wird das auf
# dem Server, wo Caddy die Anfrage annimmt und an Gunicorn weiterreicht - dort
# ist `REMOTE_ADDR` fuer jede Anfrage 127.0.0.1, und das Rate-Limit zaehlte
# sonst global statt je Absender. Lokal bleibt es aus: ohne Proxy ist
# `X-Forwarded-For` frei waehlbar und hoebe das Limit auf.
VERTRAUE_PROXY = env("DJANGO_VERTRAUE_PROXY", "False").lower() in {"1", "true", "yes"}

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

# Ohne das steht in der Liste "199000 €". Die Liste ist zum Vergleichen da -
# eine sechsstellige Zahl ohne Gruppierung ist im Vorbeisehen nicht von einer
# siebenstelligen zu unterscheiden. Formularfelder sind nicht betroffen: die
# lokalisieren nur mit `localize=True`, und keins hier tut das.
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Ohne diese Zeile findet Django `static/objektradar.css` NICHT: der Pfad liegt
# auf Projektebene, und der `AppDirectoriesFinder` sieht nur in
# `<app>/static/` nach. Der Fehler waere stumm - die Seite saehe einfach
# weiter unformatiert aus, und niemand bekaeme eine Meldung.
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    # Caddy leitet selbst schon auf https um. Die Zeile kostet im Betrieb
    # trotzdem nichts und greift, falls Gunicorn je ohne Proxy erreichbar wird.
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Fuenf Minuten, nicht ein Jahr - fuer die ersten Betriebstage. Ein
    # gesetzter HSTS-Kopf ist im Browser BINDEND: geht am Zertifikat etwas
    # schief, kaeme mit einer Jahresfrist niemand mehr behelfsweise ueber HTTP
    # drauf, auch nicht der Betreiber. Nach ein paar ruhigen Tagen von Hand
    # hochziehen (Jahr = 60 * 60 * 24 * 365).
    SECURE_HSTS_SECONDS = 300
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # `SECURE_HSTS_PRELOAD` steht hier bewusst NICHT: Preloading gilt fuer die
    # Hauptdomain, und diese Unterdomain kommt weder in die Browser-Liste noch
    # soll sie das. Ein Eintrag dort ist praktisch nicht zurueckzunehmen.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
