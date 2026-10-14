from django.contrib import admin

from .models import Game, Genre, Platform


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("name", "first_release_date", "popularity", "igdb_id")
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
