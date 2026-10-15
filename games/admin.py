from django.contrib import admin

from .models import Game, GamePrice, Genre, Platform


class GamePriceInline(admin.TabularInline):
    model = GamePrice
    extra = 0
    can_delete = False
    readonly_fields = ("shop_name", "price", "regular_price", "currency", "cut", "url")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        """Impide añadir precios a mano: solo los escribe update_prices."""
        return False


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("name", "first_release_date", "popularity", "critic_rating", "steam_app_id", "igdb_id")
    inlines = [GamePriceInline]
    readonly_fields = (
        "steam_app_id",
        "store_ids",
        "store_alternatives",
        "prices_edition",
        "historical_low",
        "historical_low_currency",
        "prices_updated_at",
    )
    search_fields = ("name",)
    list_filter = ("genres", "platforms")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("genres", "platforms")


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "igdb_id")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "slug", "igdb_id")
    prepopulated_fields = {"slug": ("name",)}
