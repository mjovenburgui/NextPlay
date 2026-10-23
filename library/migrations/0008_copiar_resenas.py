"""Pasa las notas de la biblioteca a medias estrellas (de 1-5 a 1-10) y cada reseña que había dentro de
LibraryEntry (una por juego) a una Review publicada.

De las reseñas viejas no se sabe con cuántas horas se escribieron: se guardan las que llevaba al migrar,
que en reseñas de pocos días apenas cambian.
"""

from decimal import Decimal

from django.db import migrations

SOURCES = ("steam", "psn", "xbox")


def forwards(apps, schema_editor):
    """Dobla las notas de la biblioteca y crea una reseña publicada por cada entrada que tenía reseña."""
    LibraryEntry = apps.get_model("library", "LibraryEntry")
    Review = apps.get_model("library", "Review")
    for entry in LibraryEntry.objects.exclude(rating=None):
        entry.rating = min(entry.rating * 2, 10)
        entry.save(update_fields=["rating"])
    for entry in LibraryEntry.objects.exclude(review=""):
        minutes = [getattr(entry, f"{source}_playtime_minutes") for source in SOURCES]
        known = [value for value in minutes if value is not None]
        date = entry.reviewed_at or entry.updated_at
        review = Review.objects.create(
            user_id=entry.user_id,
            game_id=entry.game_id,
            rating=entry.rating,
            text=entry.review,
            has_spoilers=entry.review_has_spoilers,
            status=entry.status,
            hours=Decimal(str(round(sum(known) / 60, 1))) if known else None,
            hours_sources=",".join(source for source, value in zip(SOURCES, minutes) if value is not None),
            published_at=date,
        )
        # created_at se rellena solo al crearla: se le pone la fecha de la reseña a mano.
        Review.objects.filter(pk=review.pk).update(created_at=date)


def backwards(apps, schema_editor):
    """Devuelve a la biblioteca la última reseña publicada de cada juego y las notas a estrellas enteras."""
    LibraryEntry = apps.get_model("library", "LibraryEntry")
    Review = apps.get_model("library", "Review")
    for review in Review.objects.filter(is_draft=False).order_by("published_at"):
        LibraryEntry.objects.filter(user_id=review.user_id, game_id=review.game_id).update(
            review=review.text[:3000], review_has_spoilers=review.has_spoilers, reviewed_at=review.published_at
        )
    for entry in LibraryEntry.objects.exclude(rating=None):
        entry.rating = max(1, (entry.rating + 1) // 2)
        entry.save(update_fields=["rating"])


class Migration(migrations.Migration):
    dependencies = [
        ("library", "0007_resenas"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
