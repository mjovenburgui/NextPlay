"""Panel de administración de los juegos: sus datos, los IDs manuales de Steam, Xbox y Nintendo para cuando IGDB o el
emparejado se equivocan, de qué juego es edición (para los recopilatorios que IGDB no enlaza), sus precios (solo para
mirarlos), los géneros y las plataformas.
"""

from django.contrib import admin

from .models import Game, GamePrice, Genre, Platform


class GamePriceInline(admin.TabularInline):
    model = GamePrice
    extra = 0
    can_delete = False
    readonly_fields = ("shop_name", "price", "regular_price", "currency", "cut", "url")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        """Impide añadir precios a mano: solo los escribe update_prices.

        Pantallas: Panel de administración (precios de cada juego: no se añaden a mano).
        """
        return False


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("name", "first_release_date", "game_type", "popularity", "critic_rating", "steam_app_id", "igdb_id")
    inlines = [GamePriceInline]
    readonly_fields = (
        "steam_app_id",
        "nintendo_nsuid",
        "store_ids",
        "store_alternatives",
        "prices_edition",
        "historical_low",
        "historical_low_currency",
        "prices_updated_at",
    )
    search_fields = ("name",)
    list_filter = ("game_type", "genres", "platforms")
    prepopulated_fields = {"slug": ("name",)}
    # «Es una edición de»: se pone a mano en los recopilatorios que IGDB no enlaza (GTA V Enhanced) y merge_editions
    # junta por la noche lo que la gente tenga de ellos.
    raw_id_fields = ("parent", "developers", "publishers")
    filter_horizontal = ("genres", "platforms", "modes", "themes", "perspectives")


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "igdb_id")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "slug", "igdb_id")
    prepopulated_fields = {"slug": ("name",)}
