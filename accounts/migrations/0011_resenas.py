"""La reseña destacada del perfil pasa de la entrada de biblioteca (que guardaba la reseña) a la reseña
nueva (library.Review), y los avisos tienen dos tipos más: «me gusta» y comentarios en tus reseñas."""

import django.db.models.deletion
from django.db import migrations, models


def forwards(apps, schema_editor):
    """Pone de reseña destacada la que se creó con la reseña de la entrada que estaba destacada."""
    Profile = apps.get_model("accounts", "Profile")
    Review = apps.get_model("library", "Review")
    for profile in Profile.objects.exclude(pinned_entry=None).select_related("pinned_entry"):
        entry = profile.pinned_entry
        profile.pinned_review = (
            Review.objects.filter(user_id=entry.user_id, game_id=entry.game_id, is_draft=False)
            .order_by("-published_at").first()
        )
        profile.save(update_fields=["pinned_review"])


def backwards(apps, schema_editor):
    """Vuelve a destacar la entrada de biblioteca del juego de la reseña destacada."""
    Profile = apps.get_model("accounts", "Profile")
    LibraryEntry = apps.get_model("library", "LibraryEntry")
    for profile in Profile.objects.exclude(pinned_review=None).select_related("pinned_review"):
        review = profile.pinned_review
        profile.pinned_entry = LibraryEntry.objects.filter(user_id=review.user_id, game_id=review.game_id).first()
        profile.save(update_fields=["pinned_entry"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_encuadre_imagenes"),
        ("library", "0008_copiar_resenas"),
    ]

    operations = [
        migrations.RenameField(model_name="profile", old_name="pinned_review", new_name="pinned_entry"),
        migrations.AddField(
            model_name="profile",
            name="pinned_review",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+",
                to="library.review", verbose_name="reseña destacada",
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="profile", name="pinned_entry"),
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("price", "Bajada de precio"), ("like", "Me gusta en tu reseña"),
                    ("comment", "Comentario en tu reseña"),
                ],
                max_length=20, verbose_name="tipo",
            ),
        ),
    ]
