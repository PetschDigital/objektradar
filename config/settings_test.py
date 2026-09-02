"""Einstellungen fuer den Testlauf.

Im Betrieb steht Caddy vor Gunicorn und beendet dort HTTPS. Im Testlauf gibt es
keinen Proxy, also auch kein HTTPS. Abgeschaltet wird genau das, was der
fehlende Proxy verursacht - nicht mehr.

`DEBUG` bleibt ausdruecklich auf `False`. Mit `DEBUG=True` verschwaenden
dieselben Fehler ebenfalls, aber nebenbei aendern sich Fehlerseiten,
Hostpruefung und Abfrageprotokollierung. Der Testlauf maesse dann eine
Konfiguration, die es im Betrieb nirgends gibt.

Ein Test, der prueft, dass `SECURE_SSL_REDIRECT` hier `False` ist, waere eine
Tautologie und steht deshalb nicht da. Der Zeuge dieser Datei ist der Testlauf
selbst: er laeuft mit `DJANGO_DEBUG=False` durch.
"""

from config.settings import *  # noqa: F401,F403

# `SecurityMiddleware` antwortete sonst vor JEDER Ansicht mit 301 auf https://,
# und `response.context` waere ueberall None.
SECURE_SSL_REDIRECT = False

# Der Testclient spricht HTTP. Mit `Secure` gesetzt kaeme kein Cookie zurueck.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

SECURE_HSTS_SECONDS = 0
