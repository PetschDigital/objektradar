from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Person


@admin.register(Person)
class PersonAdmin(UserAdmin):
    """Kontenanlage von Hand - es gibt bewusst keinen Registrierungsweg."""

    list_display = ["username", "anzeigename", "email", "is_active", "letzter_besuch"]
    readonly_fields = ["letzter_besuch", "besuch_davor", "last_login", "date_joined"]
    fieldsets = UserAdmin.fieldsets + (
        ("Besuche", {"fields": ["letzter_besuch", "besuch_davor"]}),
    )
