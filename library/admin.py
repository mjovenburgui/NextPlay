from django.contrib import admin

from .models import LibraryEntry


@admin.register(LibraryEntry)
class LibraryEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("user__username", "game__name")
    autocomplete_fields = ("game",)
    raw_id_fields = ("user",)
