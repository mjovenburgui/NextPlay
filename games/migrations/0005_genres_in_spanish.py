from django.db import migrations

from games.models import GENRE_NAMES_ES


def translate_genres(apps, schema_editor):
    """Pasa al español los géneros ya importados de IGDB; los slugs (y con ellos las URLs) no cambian."""
    Genre = apps.get_model("games", "Genre")
    for genre in Genre.objects.filter(slug__in=GENRE_NAMES_ES):
        genre.name = GENRE_NAMES_ES[genre.slug]
        genre.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        ("games", "0004_price_history_by_shop"),
    ]

    # Al deshacerla no se vuelve al inglés: la siguiente importación de IGDB tampoco lo haría.
    operations = [migrations.RunPython(translate_genres, migrations.RunPython.noop)]
