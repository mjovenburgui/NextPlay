from django.contrib import admin
from django.db.models import Count, Q

from library.admin import ReportInline

from .models import GameList, ListCollaborator, ListComment, ListItem


class ListItemInline(admin.TabularInline):
    """Juegos de una lista, en su orden, para corregirlos o quitarlos desde ella."""

    model = ListItem
    extra = 0
    raw_id_fields = ("game", "added_by")
    fields = ("position", "game", "tier", "note", "added_by")


class ListCollaboratorInline(admin.TabularInline):
    """Gente invitada a editar la lista y si ha aceptado."""

    model = ListCollaborator
    extra = 0
    raw_id_fields = ("user",)
    readonly_fields = ("invited_at",)


class ListCommentInline(admin.TabularInline):
    """Comentarios de una lista, para moderarlos desde ella."""

    model = ListComment
    extra = 0
    raw_id_fields = ("user",)


@admin.register(GameList)
class GameListAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_public", "mode", "game_count", "updated_at", "pending_reports")
    list_filter = ("is_public", "mode")
    search_fields = ("title", "user__username")
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"
    inlines = [ListItemInline, ListCollaboratorInline, ListCommentInline, ReportInline]

    def get_queryset(self, request):
        """Las listas con cuántos juegos tiene cada una y cuántas denuncias suyas quedan por revisar."""
        return super().get_queryset(request).annotate(
            game_count=Count("items", distinct=True),
            pending=Count("reports", filter=Q(reports__resolved_at__isnull=True), distinct=True),
        )

    @admin.display(description="Juegos", ordering="game_count")
    def game_count(self, obj):
        """Cuántos juegos tiene la lista."""
        return obj.game_count

    @admin.display(description="denuncias pendientes", ordering="pending")
    def pending_reports(self, obj):
        """Cuántas denuncias de la lista quedan por revisar."""
        return obj.pending


@admin.register(ListComment)
class ListCommentAdmin(admin.ModelAdmin):
    list_display = ("user", "game_list", "created_at")
    search_fields = ("user__username", "text")
    raw_id_fields = ("user", "game_list")
    inlines = [ReportInline]
