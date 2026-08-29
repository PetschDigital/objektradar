from django.urls import path

from . import views

urlpatterns = [
    path("", views.ObjektlisteView.as_view(), name="objektliste"),
    path("einwerfen/", views.objekt_anlegen, name="objekt_anlegen"),
    path("objekt/<int:pk>/", views.ObjektView.as_view(), name="objekt"),
    path(
        "objekt/<int:pk>/bearbeiten/",
        views.ObjektBearbeitenView.as_view(),
        name="objekt_bearbeiten",
    ),
    path("objekt/<int:pk>/votum/", views.votum_setzen, name="votum_setzen"),
    path("objekt/<int:pk>/status/", views.status_setzen, name="status_setzen"),
    path("objekt/<int:pk>/notiz/", views.notiz_anlegen, name="notiz_anlegen"),
]
