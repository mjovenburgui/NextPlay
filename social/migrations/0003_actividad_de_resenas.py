"""Apunta en la actividad de cada autor las reseñas que ya estaban publicadas, con su fecha y su nota.

Es lo único que se rellena hacia atrás: las fechas de la biblioteca no dicen cuándo se cambió un estado o una
nota, porque updated_at cambia con cada sincronización.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    """Crea una actividad de juego por cada reseña publicada que aún no tenga la suya."""
    Activity = apps.get_model("social", "Activity")
    Review = apps.get_model("library", "Review")
    done = set(Activity.objects.exclude(review=None).values_list("review_id", flat=True))
    Activity.objects.bulk_create(
        Activity(
            user_id=review.user_id, kind="game", game_id=review.game_id, review_id=review.pk, rating=review.rating,
            created_at=review.published_at, updated_at=review.published_at,
        )
        for review in Review.objects.filter(is_draft=False).exclude(published_at=None)
        if review.pk not in done
    )


class Migration(migrations.Migration):

    dependencies = [
        ('library', '0012_deseados_de_steam'),
        ('social', '0002_actividad'),
    ]

    operations = [
        # Hacia atrás no hace falta deshacer nada: quitar 0002 ya borra la tabla de la actividad.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
