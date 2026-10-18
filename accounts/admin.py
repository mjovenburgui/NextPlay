from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .matching import update_library
from .models import ExternalTitle, PlatformAccount, Profile, PSNAccount, SteamAccount, User, XboxAccount

admin.site.register(User, UserAdmin)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "is_public", "auto_sync", "auto_synced_at", "created_at")
    list_filter = ("is_public", "auto_sync")
    search_fields = ("user__username", "display_name")


@admin.register(SteamAccount)
class SteamAccountAdmin(admin.ModelAdmin):
    list_display = ("persona", "steam_id", "user", "synced_at", "linked_at")
    search_fields = ("persona", "steam_id", "user__username")
    raw_id_fields = ("user",)


@admin.register(PSNAccount)
class PSNAccountAdmin(admin.ModelAdmin):
    list_display = ("online_id", "account_id", "user", "synced_at", "linked_at")
    search_fields = ("online_id", "account_id", "user__username")
    raw_id_fields = ("user",)


@admin.register(PlatformAccount)
class PlatformAccountAdmin(admin.ModelAdmin):
    list_display = ("handle", "platform", "user", "created_at")
    list_filter = ("platform",)
    search_fields = ("handle", "user__username")
    raw_id_fields = ("user",)


@admin.register(XboxAccount)
class XboxAccountAdmin(admin.ModelAdmin):
    list_display = ("gamertag", "xuid", "user", "synced_at", "linked_at")
    search_fields = ("gamertag", "xuid", "user__username")
    raw_id_fields = ("user",)


@admin.register(ExternalTitle)
class ExternalTitleAdmin(admin.ModelAdmin):
    """Revisión de los emparejamientos: los cambiados por usuarios se filtran por «emparejado por»."""

    list_display = ("name", "platform", "game", "source", "assigned_by", "locked", "owners", "updated_at")
    list_filter = ("platform", "source", "locked", ("game", admin.EmptyFieldListFilter))
    list_editable = ("locked",)
    search_fields = ("name", "external_id", "game__name")
    raw_id_fields = ("game", "assigned_by")
    readonly_fields = ("platform", "external_id", "source", "assigned_by", "updated_at")
    actions = ["lock", "unlock"]

    @admin.display(description="usuarios")
    def owners(self, obj):
        """Cuántos usuarios tienen este título importado."""
        return obj.imports.count()

    def save_model(self, request, obj, form, change):
        """Si cambia el juego, lo marca como corregido por el administrador y recoloca ya las horas de
        todos los usuarios que tienen el título (las de un usuario normal esperan a su próxima importación)."""
        if "game" in form.changed_data:
            obj.source = ExternalTitle.Source.ADMIN
            obj.assigned_by = request.user
        super().save_model(request, obj, form, change)
        if "game" in form.changed_data:
            for imported in obj.imports.select_related("user"):
                update_library(imported.user, obj.platform, opened_only=obj.platform != ExternalTitle.Platform.STEAM)

    @admin.action(description="Bloquear: los usuarios ya no podrán cambiarlos")
    def lock(self, request, queryset):
        """Bloquea los títulos elegidos."""
        queryset.update(locked=True)

    @admin.action(description="Desbloquear")
    def unlock(self, request, queryset):
        """Desbloquea los títulos elegidos."""
        queryset.update(locked=False)
