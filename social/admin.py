from django.contrib import admin

from .models import Activity, Follow


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("follower", "followed", "created_at")
    search_fields = ("follower__username", "followed__username")
    raw_id_fields = ("follower", "followed")
    date_hierarchy = "created_at"


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "summary", "updated_at")
    list_filter = ("kind",)
    search_fields = ("user__username", "game__name")
    raw_id_fields = ("user", "game", "review")
    date_hierarchy = "updated_at"
