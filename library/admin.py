from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import LibraryEntry, Report, Review, ReviewComment, ReviewImage, WishlistItem


@admin.register(LibraryEntry)
class LibraryEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "status", "rating", "updated_at")
    list_filter = ("status",)
    search_fields = ("user__username", "game__name")
    autocomplete_fields = ("game",)
    raw_id_fields = ("user",)


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "target_price", "alerted_price", "from_steam", "steam_added_at", "created_at")
    list_filter = ("from_steam",)
    search_fields = ("user__username", "game__name")
    raw_id_fields = ("user", "game")


class ReviewCommentInline(admin.TabularInline):
    """Comentarios de una reseña, para moderarlos desde ella."""

    model = ReviewComment
    extra = 0
    raw_id_fields = ("user",)


class ReviewImageInline(admin.TabularInline):
    """Imágenes de una reseña, con su miniatura, para quitar las que no deban estar."""

    model = ReviewImage
    extra = 0
    fields = ("preview", "is_spoiler", "position")
    readonly_fields = ("preview",)

    @admin.display(description="imagen")
    def preview(self, image):
        """Miniatura de la imagen, que lleva a la grande."""
        return format_html(
            '<a href="{}" target="_blank" rel="noopener"><img src="{}" alt="" style="height: 80px"></a>',
            image.url, image.thumb_url,
        )


class ReportInline(GenericTabularInline):
    """Denuncias de una reseña o un comentario."""

    model = Report
    extra = 0
    fields = ("reporter", "reason", "detail", "created_at", "resolved_at")
    readonly_fields = ("reporter", "reason", "detail", "created_at")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "rating", "is_draft", "published_at", "pending_reports")
    list_filter = ("is_draft", "has_spoilers", "is_replay")
    search_fields = ("user__username", "game__name", "text")
    raw_id_fields = ("user", "game", "platform")
    inlines = [ReviewImageInline, ReviewCommentInline, ReportInline]

    def get_queryset(self, request):
        """Las reseñas con sus denuncias pendientes contadas."""
        return super().get_queryset(request).annotate(
            pending=Count("reports", filter=Q(reports__resolved_at__isnull=True))
        )

    @admin.display(description="denuncias pendientes", ordering="pending")
    def pending_reports(self, review):
        """Cuántas denuncias de la reseña quedan por revisar."""
        return review.pending


@admin.register(ReviewComment)
class ReviewCommentAdmin(admin.ModelAdmin):
    list_display = ("user", "review", "created_at")
    search_fields = ("user__username", "text")
    raw_id_fields = ("user", "review")
    inlines = [ReportInline]


class PendingFilter(admin.SimpleListFilter):
    """Filtro de las denuncias por si ya se han revisado."""

    title = "estado"
    parameter_name = "estado"

    def lookups(self, request, model_admin):
        """Las dos opciones del filtro."""
        return [("pendientes", "Pendientes"), ("revisadas", "Revisadas")]

    def queryset(self, request, queryset):
        """Las denuncias sin revisar o las ya revisadas, según la opción elegida."""
        if self.value() == "pendientes":
            return queryset.filter(resolved_at__isnull=True)
        if self.value() == "revisadas":
            return queryset.filter(resolved_at__isnull=False)
        return queryset


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """Denuncias de reseñas y comentarios. Desde cada una se va a lo denunciado (en la web o en el admin) para
    borrarlo si hace falta, y después se marca como revisada."""

    list_display = ("created_at", "kind", "target_link", "reason", "reporter", "resolved_at")
    list_filter = (PendingFilter, "reason", "content_type")
    list_select_related = ("reporter", "content_type")
    search_fields = ("reporter__username", "detail")
    fields = ("target_link", "reporter", "reason", "detail", "created_at", "resolved_at")
    readonly_fields = ("target_link", "reporter", "reason", "detail", "created_at")
    actions = ["mark_resolved", "mark_pending"]

    def has_add_permission(self, request):
        """Las denuncias solo las crean los usuarios desde la web."""
        return False

    @admin.display(description="qué es")
    def kind(self, report):
        """Qué se ha denunciado: «reseña» o «comentario»."""
        return report.target_kind

    @admin.display(description="denunciado")
    def target_link(self, report):
        """Enlaces a lo denunciado: su página en la web y su ficha en el admin; «borrado» si ya no existe."""
        target = report.target
        if target is None:
            return "(borrado)"
        meta = target._meta
        admin_url = reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[target.pk])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a> · <a href="{}">en el admin</a>',
            target.get_absolute_url(), str(target)[:80], admin_url,
        )

    @admin.action(description="Marcar como revisadas")
    def mark_resolved(self, request, queryset):
        """Marca las denuncias elegidas como revisadas ahora."""
        updated = queryset.filter(resolved_at__isnull=True).update(resolved_at=timezone.now())
        self.message_user(request, f"{updated} denuncia(s) marcada(s) como revisada(s).")

    @admin.action(description="Volver a dejar pendientes")
    def mark_pending(self, request, queryset):
        """Vuelve a dejar pendientes las denuncias elegidas."""
        updated = queryset.update(resolved_at=None)
        self.message_user(request, f"{updated} denuncia(s) pendiente(s) otra vez.")
