from django.contrib.auth.views import (
    LogoutView,
    PasswordChangeDoneView,
    PasswordChangeView,
)
from django.urls import path, reverse_lazy

from . import views

urlpatterns = [
    path("anmelden/", views.AnmeldeView.as_view(), name="login"),
    # Abmelden ist seit Django 5 nur per POST erreichbar - ein GET-Link liesse
    # sich von fremden Seiten aus ausloesen.
    path("abmelden/", LogoutView.as_view(), name="logout"),
    # Passwort aendern. Djangos Ansichten unveraendert: `PasswordChangeView`
    # ruft `update_session_auth_hash()` selbst auf - wer sein Passwort aendert,
    # bleibt hier angemeldet, waehrend Sitzungen auf anderen Geraeten auslaufen.
    # Genau das ist der halbe Zweck der Seite und wird nicht umgebaut.
    #
    # Gesetzt wird nur `success_url`: die Vorgabe zeigt auf den Namen
    # `password_change_done`, den es hier nicht gibt - die Adressen sind
    # deutsch. Ohne diese Zeile faellt erst der ERFOLGSFALL mit
    # `NoReverseMatch` um, also die eine Stelle, die beim Draufsehen am
    # ehesten als "laeuft" durchgeht.
    path(
        "passwort/",
        PasswordChangeView.as_view(success_url=reverse_lazy("passwort_geaendert")),
        name="passwort_aendern",
    ),
    path(
        "passwort/geaendert/",
        PasswordChangeDoneView.as_view(),
        name="passwort_geaendert",
    ),
]
