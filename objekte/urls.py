from django.urls import path

from . import views

urlpatterns = [
    path("", views.ObjektlisteView.as_view(), name="objektliste"),
    path("einwerfen/", views.objekt_anlegen, name="objekt_anlegen"),
    # Eine Adresse, zwei Stationen: GET zeigt die Vorschau, POST uebernimmt.
    path("uebernehmen/", views.UebernehmenView.as_view(), name="uebernehmen"),
    path("lesezeichen/", views.LesezeichenView.as_view(), name="lesezeichen"),
    path("objekt/<int:pk>/", views.ObjektView.as_view(), name="objekt"),
    path(
        "objekt/<int:pk>/bearbeiten/",
        views.ObjektBearbeitenView.as_view(),
        name="objekt_bearbeiten",
    ),
    # Eine Adresse, zwei Stationen - wie bei der Uebernahme: GET zeigt die
    # Bestaetigung, POST loescht. Ein Loeschen haengt nicht an einem Link.
    path(
        "objekt/<int:pk>/loeschen/",
        views.ObjektLoeschenView.as_view(),
        name="objekt_loeschen",
    ),
    path("objekt/<int:pk>/votum/", views.votum_setzen, name="votum_setzen"),
    path("objekt/<int:pk>/status/", views.status_setzen, name="status_setzen"),
    path("objekt/<int:pk>/notiz/", views.notiz_anlegen, name="notiz_anlegen"),
]
