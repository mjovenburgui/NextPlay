"""Crea la plataforma Steam Deck, que no está en IGDB, para las reseñas de lo jugado en una (ver Game.review_platforms).

No se asigna a ningún juego: se ofrece en el editor de reseñas de los juegos de PC.
"""

from django.db import migrations

SLUG = "steam-deck"


def forwards(apps, schema_editor):
    """Crea la plataforma Steam Deck si no existe."""
    Platform = apps.get_model("games", "Platform")
    Platform.objects.get_or_create(slug=SLUG, defaults={"name": "Steam Deck"})


def backwards(apps, schema_editor):
    """Borra la plataforma Steam Deck (las reseñas que la tenían se quedan sin plataforma)."""
    Platform = apps.get_model("games", "Platform")
    Platform.objects.filter(slug=SLUG, igdb_id=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0007_imagenes_y_videos'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
