from django.contrib import admin

from .models import Challenge, Participation


class ParticipationInline(admin.TabularInline):
    """Quién se ha unido al reto y si lo ha conseguido."""

    model = Participation
    extra = 0
    raw_id_fields = ("user",)
    readonly_fields = ("joined_at",)


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = ("title", "creator", "kind", "target", "starts_on", "ends_on", "is_public", "created_at")
    list_filter = ("kind", "is_public")
    search_fields = ("title", "creator__username")
    raw_id_fields = ("creator", "source_list", "games")
    date_hierarchy = "created_at"
    inlines = [ParticipationInline]
