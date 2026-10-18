from django.contrib import admin

from .models import LibraryEntry, WishlistItem


@admin.register(LibraryEntry)
class LibraryEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("user__username", "game__name")
    autocomplete_fields = ("game",)
    raw_id_fields = ("user",)


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "target_price", "alerted_price", "created_at")
    search_fields = ("user__username", "game__name")
    raw_id_fields = ("user", "game")
