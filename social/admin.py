"""Panel de administración de lo social: quién sigue a quién, las publicaciones (con sus imágenes, comentarios y
denuncias pendientes), sus comentarios y la actividad de cada jugador.
"""

from django.contrib import admin
from django.db.models import Count, Q

from library.admin import ReportInline

from .models import Activity, Follow, Post, PostComment, PostImage


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("follower", "followed", "created_at")
    search_fields = ("follower__username", "followed__username")
    raw_id_fields = ("follower", "followed")
    date_hierarchy = "created_at"


class PostImageInline(admin.TabularInline):
    """Imágenes de una publicación, para quitar las que no deban estar."""

    model = PostImage
    extra = 0
    fields = ("image", "position")


class PostCommentInline(admin.TabularInline):
    """Comentarios de una publicación (o respuestas de un hilo), para moderarlos desde ella."""

    model = PostComment
    fk_name = "post"
    extra = 0
    fields = ("user", "text", "reply_to", "hours", "created_at")
    readonly_fields = ("hours", "created_at")
    raw_id_fields = ("user", "reply_to")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("user", "__str__", "kind", "game", "pinned", "closed", "last_activity_at", "pending_reports")
    list_filter = ("kind", "pinned", "closed", "has_spoilers")
    search_fields = ("user__username", "title", "text", "game__name")
    raw_id_fields = ("user", "game", "spoiler_achievement", "achievement", "answer")
    inlines = [PostImageInline, PostCommentInline, ReportInline]
    actions = ["pin", "unpin", "close", "reopen"]

    @admin.action(description="Fijar arriba en la comunidad de su juego")
    def pin(self, request, queryset):
        """Fija arriba los hilos y las preguntas elegidos en la comunidad de su juego.

        Pantallas: Panel de administración (publicaciones), Comunidad del juego (lo fijado, arriba).
        """
        queryset.update(pinned=True)

    @admin.action(description="Dejar de fijar")
    def unpin(self, request, queryset):
        """Los deja de fijar: vuelven a su sitio según su última actividad.

        Pantallas: Panel de administración (publicaciones), Comunidad del juego.
        """
        queryset.update(pinned=False)

    @admin.action(description="Cerrar a respuestas")
    def close(self, request, queryset):
        """Cierra los hilos y las preguntas elegidos: se pueden leer, pero ya no admiten respuestas.

        Pantallas: Panel de administración (publicaciones), Hilo o pregunta (hilo cerrado).
        """
        queryset.update(closed=True)

    @admin.action(description="Volver a abrir")
    def reopen(self, request, queryset):
        """Los vuelve a abrir a respuestas.

        Pantallas: Panel de administración (publicaciones), Hilo o pregunta.
        """
        queryset.update(closed=False)

    def get_queryset(self, request):
        """Las publicaciones con sus denuncias pendientes contadas.

        Pantallas: Panel de administración (publicaciones con sus denuncias pendientes).
        """
        return super().get_queryset(request).annotate(
            pending=Count("reports", filter=Q(reports__resolved_at__isnull=True))
        )

    @admin.display(description="denuncias pendientes", ordering="pending")
    def pending_reports(self, post):
        """Cuántas denuncias de la publicación quedan por revisar.

        Pantallas: Panel de administración (columna de denuncias pendientes de las publicaciones).
        """
        return post.pending


@admin.register(PostComment)
class PostCommentAdmin(admin.ModelAdmin):
    list_display = ("user", "post", "created_at")
    search_fields = ("user__username", "text")
    raw_id_fields = ("user", "post")
    inlines = [ReportInline]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "summary", "updated_at")
    list_filter = ("kind",)
    search_fields = ("user__username", "game__name")
    raw_id_fields = ("user", "game", "review", "game_list")
    date_hierarchy = "updated_at"
