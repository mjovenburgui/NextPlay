from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Profile, User

admin.site.register(User, UserAdmin)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "is_public", "created_at")
    list_filter = ("is_public",)
    search_fields = ("user__username", "display_name")
