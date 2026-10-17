from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import PlatformAccount, Profile, PSNAccount, SteamAccount, User, XboxAccount

admin.site.register(User, UserAdmin)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "is_public", "created_at")
    list_filter = ("is_public",)
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
