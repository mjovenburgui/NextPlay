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
    """Comentarios de una publicación, para moderarlos desde ella."""

    model = PostComment
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("user", "__str__", "game", "created_at", "pending_reports")
    list_filter = ("has_spoilers",)
    search_fields = ("user__username", "text", "game__name")
    raw_id_fields = ("user", "game")
    inlines = [PostImageInline, PostCommentInline, ReportInline]

    def get_queryset(self, request):
        """Las publicaciones con sus denuncias pendientes contadas."""
        return super().get_queryset(request).annotate(
            pending=Count("reports", filter=Q(reports__resolved_at__isnull=True))
        )

    @admin.display(description="denuncias pendientes", ordering="pending")
    def pending_reports(self, post):
        """Cuántas denuncias de la publicación quedan por revisar."""
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
