from django.contrib import admin

from .models import LibraryEntry, Review, ReviewComment, WishlistItem


@admin.register(LibraryEntry)
class LibraryEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "status", "rating", "updated_at")
    list_filter = ("status",)
    search_fields = ("user__username", "game__name")
    autocomplete_fields = ("game",)
    raw_id_fields = ("user",)


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "target_price", "alerted_price", "created_at")
    search_fields = ("user__username", "game__name")
    raw_id_fields = ("user", "game")


class ReviewCommentInline(admin.TabularInline):
    """Comentarios de una reseña, para moderarlos desde ella."""

    model = ReviewComment
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("user", "game", "rating", "is_draft", "published_at")
    list_filter = ("is_draft", "has_spoilers", "is_replay")
    search_fields = ("user__username", "game__name", "text")
    raw_id_fields = ("user", "game", "platform")
    inlines = [ReviewCommentInline]


@admin.register(ReviewComment)
class ReviewCommentAdmin(admin.ModelAdmin):
    list_display = ("user", "review", "created_at")
    search_fields = ("user__username", "text")
    raw_id_fields = ("user", "review")
