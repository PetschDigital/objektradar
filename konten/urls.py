from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("anmelden/", views.AnmeldeView.as_view(), name="login"),
    # Abmelden ist seit Django 5 nur per POST erreichbar - ein GET-Link liesse
    # sich von fremden Seiten aus ausloesen.
    path("abmelden/", LogoutView.as_view(), name="logout"),
]
