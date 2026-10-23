from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from games.models import Game
from library.models import LibraryEntry, Review, ReviewComment, ReviewLike

from ...models import PSNAccount, XboxAccount

User = get_user_model()

# Cuenta solo para desarrollo con la que probar el perfil y las reseñas; el comando no funciona sin DEBUG.
USERNAME = "tester"
PASSWORD = "nextplay-tester-2026"
EMAIL = "tester@nextplay.test"
# Cuenta vacía (sin juegos ni cuentas vinculadas) para probar desde cero la vinculación e importación
# de Steam, PlayStation y Xbox; usa la misma contraseña. Se crea con --vacio.
EMPTY_USERNAME = "tester_steam"
EMPTY_EMAIL = "tester_steam@nextplay.test"

# (estado, nota en medias estrellas de 1 a 10, minutos en Steam, reseña, toda ella spoiler) para los juegos
# más populares con portada, en orden. Las reseñas usan el formato (negrita, cursiva y ||spoilers||).
LIBRARY = [
    ("playing", 10, 3120, "Llevo más de 50 horas y **sigo descubriendo cosas**. El mundo está lleno de detalles.", False),
    (
        "completed", 9, 6150,
        "Una *obra maestra*. Las misiones secundarias están mejor escritas que muchas historias principales.\n\n"
        "Lo mejor llega al final: ||la última conversación con el barón me dejó tocado||.",
        False,
    ),
    ("completed", 8, None, "Al final resulta que el villano era su propio padre. Aun así, el combate es lo mejor.", True),
    ("played", 7, 1480, "", False),
    ("dropped", 3, 45, "No conseguí engancharme: el ritmo del principio es demasiado lento.", False),
    ("backlog", None, 0, "", False),
    ("completed", 6, 820, "", False),
    ("played", None, 95, "", False),
]

# Reseñas de los usuarios demo sobre los mismos juegos, para que las fichas muestren también las de otros:
# (usuario, posición del juego en LIBRARY, nota de 1 a 10, minutos jugados, estado, reseña).
OTHER_REVIEWS = [
    ("demo1", 0, 8, 600, "playing", "Muy divertido con amigos, aunque la parte online necesita mejoras."),
    ("demo2", 1, 10, 9000, "completed", "Imprescindible. De los mejores juegos que he jugado nunca."),
    ("demo3", 1, 7, 240, "playing", "Bonito y largo, pero el combate se me hizo repetitivo."),
]
# «Me gusta» y comentarios de los usuarios demo en las reseñas del usuario de prueba: (usuario, posición, comentario).
REACTIONS = [
    ("demo1", 1, "Totalmente de acuerdo con lo de las secundarias."),
    ("demo2", 1, ""),
    ("demo3", 0, "¿Merece la pena aunque no te gusten los mundos abiertos?"),
]


class Command(BaseCommand):
    help = (
        "Crea (o rehace) el usuario de prueba «tester» con biblioteca, valoraciones, horas y reseñas. "
        "Con --vacio crea en su lugar «tester_steam», sin juegos ni cuentas, para probar las importaciones."
    )

    def add_arguments(self, parser):
        """Opción --vacio para crear la cuenta vacía en vez de la de ejemplo."""
        parser.add_argument(
            "--vacio", action="store_true", help="Crea o vacía «tester_steam» (sin juegos ni cuentas vinculadas)."
        )

    def handle(self, *args, **options):
        """Crea el usuario de prueba con su biblioteca y reseñas, y añade reseñas de los usuarios demo.

        Solo funciona con DJANGO_DEBUG=1. Si el usuario ya existía, rehace su biblioteca desde cero.
        Con --vacio crea (o deja vacía) la cuenta «tester_steam».
        """
        if not settings.DEBUG:
            raise CommandError("Solo se puede usar en desarrollo (DJANGO_DEBUG=1).")
        if options["vacio"]:
            self.create_empty_user()
            return
        games = list(Game.objects.exclude(cover_image_id="")[: len(LIBRARY)])
        if len(games) < len(LIBRARY):
            games = list(Game.objects.all()[: len(LIBRARY)])
        if not games:
            raise CommandError("El catálogo está vacío: ejecuta antes seed_demo o import_igdb.")

        user, _ = User.objects.get_or_create(username=USERNAME, defaults={"email": EMAIL})
        user.set_password(PASSWORD)
        user.save()
        profile = user.profile
        profile.display_name = "Tester NextPlay"
        profile.bio = "Cuenta de prueba. Me gustan los RPG de mundo abierto y los indies con buena historia."
        profile.is_public = True
        profile.save()

        LibraryEntry.objects.filter(user=user).delete()
        Review.objects.filter(user=user).delete()
        now = timezone.now()
        reviews = {}
        for days, (game, (status, rating, minutes, text, spoilers)) in enumerate(zip(games, LIBRARY)):
            entry = LibraryEntry.objects.create(
                user=user, game=game, status=status, rating=rating, steam_playtime_minutes=minutes,
            )
            if text:
                reviews[days] = self.review(user, entry, text, spoilers, now - timedelta(days=days))

        for username, index, rating, minutes, status, text in OTHER_REVIEWS:
            other = User.objects.filter(username=username).first()
            if other is None or index >= len(games):
                continue
            entry, _ = LibraryEntry.objects.update_or_create(
                user=other, game=games[index],
                defaults={"rating": rating, "status": status, "steam_playtime_minutes": minutes},
            )
            Review.objects.filter(user=other, game=games[index]).delete()
            self.review(other, entry, text, False, now - timedelta(hours=index + 2))

        for username, index, comment in REACTIONS:
            other = User.objects.filter(username=username).first()
            if other is None or index not in reviews:
                continue
            ReviewLike.objects.get_or_create(user=other, review=reviews[index])
            if comment:
                ReviewComment.objects.create(user=other, review=reviews[index], text=comment)

        self.stdout.write(self.style.SUCCESS(
            f"Usuario de prueba «{USERNAME}» listo con {len(games)} juegos. "
            f"La contraseña está en accounts/management/commands/seed_test_profile.py."
        ))

    def review(self, user, entry, text, spoilers, when):
        """Publica una reseña de user sobre el juego de entry con fecha when, con su nota, horas y estado."""
        review = Review(user=user, game=entry.game, rating=entry.rating, text=text, has_spoilers=spoilers, published_at=when)
        review.snapshot(entry)
        review.save()
        return review

    def create_empty_user(self):
        """Crea «tester_steam» o lo deja como recién creado: sin juegos ni cuentas de Steam, PlayStation, Xbox u otras plataformas."""
        user, _ = User.objects.get_or_create(username=EMPTY_USERNAME, defaults={"email": EMPTY_EMAIL})
        user.set_password(PASSWORD)
        user.save()
        profile = user.profile
        profile.display_name = "Tester Steam"
        profile.bio = "Cuenta vacía para probar la importación de Steam, PlayStation y Xbox."
        profile.is_public = True
        profile.save()
        LibraryEntry.objects.filter(user=user).delete()
        user.steam_accounts.all().delete()
        PSNAccount.objects.filter(user=user).delete()
        XboxAccount.objects.filter(user=user).delete()
        user.platform_accounts.all().delete()
        self.stdout.write(self.style.SUCCESS(
            f"Usuario de prueba «{EMPTY_USERNAME}» listo y vacío. "
            f"La contraseña está en accounts/management/commands/seed_test_profile.py."
        ))
