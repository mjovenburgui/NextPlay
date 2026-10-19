from datetime import date

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from games.models import Game, Genre, Platform, spanish_genre_name

PLATFORMS = {
    "PC": "PC (Microsoft Windows)",
    "PS4": "PlayStation 4",
    "PS5": "PlayStation 5",
    "XONE": "Xbox One",
    "Series X|S": "Xbox Series X|S",
    "Switch": "Nintendo Switch",
}

# (name, release date, genres, platform abbreviations)
GAMES = [
    ("The Legend of Zelda: Breath of the Wild", date(2017, 3, 3), ["Adventure"], ["Switch"]),
    ("Elden Ring", date(2022, 2, 25), ["Role-playing (RPG)", "Adventure"], ["PC", "PS4", "PS5", "XONE", "Series X|S"]),
    ("The Witcher 3: Wild Hunt", date(2015, 5, 19), ["Role-playing (RPG)", "Adventure"], ["PC", "PS4", "XONE", "Switch"]),
    ("Hades", date(2020, 9, 17), ["Role-playing (RPG)", "Indie"], ["PC", "PS4", "PS5", "Switch"]),
    ("Hollow Knight", date(2017, 2, 24), ["Platform", "Adventure", "Indie"], ["PC", "PS4", "XONE", "Switch"]),
    ("Red Dead Redemption 2", date(2018, 10, 26), ["Adventure", "Shooter"], ["PC", "PS4", "XONE"]),
    ("Stardew Valley", date(2016, 2, 26), ["Simulator", "Role-playing (RPG)", "Indie"], ["PC", "PS4", "XONE", "Switch"]),
    ("Celeste", date(2018, 1, 25), ["Platform", "Indie"], ["PC", "PS4", "XONE", "Switch"]),
    ("Portal 2", date(2011, 4, 19), ["Puzzle", "Platform", "Shooter"], ["PC"]),
    ("Baldur's Gate III", date(2023, 8, 3), ["Role-playing (RPG)", "Strategy"], ["PC", "PS5", "Series X|S"]),
    ("Super Mario Odyssey", date(2017, 10, 27), ["Platform", "Adventure"], ["Switch"]),
    ("Disco Elysium", date(2019, 10, 15), ["Role-playing (RPG)", "Indie"], ["PC", "PS4", "PS5", "Switch"]),
]


class Command(BaseCommand):
    help = "Crea un catálogo mínimo de demostración para probar la app sin credenciales de IGDB."

    def handle(self, *args, **options):
        """Crea los 12 juegos de GAMES con sus géneros y plataformas; se salta los que ya existen."""
        created = 0
        for position, (name, released, genre_names, platform_keys) in enumerate(GAMES):
            game, was_created = Game.objects.get_or_create(
                slug=slugify(name),
                defaults={
                    "name": name,
                    "first_release_date": released,
                    "popularity": len(GAMES) - position,
                },
            )
            if not was_created:
                continue
            created += 1
            game.genres.set(
                Genre.objects.get_or_create(slug=slugify(g), defaults={"name": spanish_genre_name(g)})[0]
                for g in genre_names
            )
            game.platforms.set(
                Platform.objects.get_or_create(
                    slug=slugify(PLATFORMS[key]),
                    defaults={"name": PLATFORMS[key], "abbreviation": key},
                )[0]
                for key in platform_keys
            )
        self.stdout.write(self.style.SUCCESS(f"{created} juegos de demostración creados."))
