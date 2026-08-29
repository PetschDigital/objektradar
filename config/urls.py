from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Kontenanlage und Notfallkorrekturen. Ersetzt nicht die Oberflaeche.
    path("admin/", admin.site.urls),
    path("", include("konten.urls")),
    path("", include("objekte.urls")),
]
