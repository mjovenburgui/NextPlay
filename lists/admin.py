from django.contrib import admin
from django.db.models import Count

from .models import GameList, ListItem


class ListItemInline(admin.TabularInline):
    """Juegos de una lista, en su orden, para corregirlos o quitarlos desde ella."""

    model = ListItem
    extra = 0
    raw_id_fields = ("game",)
    fields = ("position", "game", "note")


@admin.register(GameList)
class GameListAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_public", "is_ranked", "game_count", "updated_at")
    list_filter = ("is_public", "is_ranked")
    search_fields = ("title", "user__username")
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"
    inlines = [ListItemInline]

    def get_queryset(self, request):
        """Las listas con cuántos juegos tiene cada una, para la columna «Juegos»."""
        return super().get_queryset(request).annotate(game_count=Count("items"))

    @admin.display(description="Juegos", ordering="game_count")
    def game_count(self, obj):
        """Cuántos juegos tiene la lista."""
        return obj.game_count
