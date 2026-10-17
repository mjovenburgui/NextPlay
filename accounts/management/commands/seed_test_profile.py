from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from games.models import Game
from library.models import LibraryEntry

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

# (estado, estrellas, minutos en Steam, reseña, spoilers) para los juegos más populares con portada, en orden.
LIBRARY = [
    ("playing", 5, 3120, "Llevo más de 50 horas y sigo descubriendo cosas. El mundo está lleno de detalles.", False),
    ("completed", 5, 6150, "Una obra maestra. Las misiones secundarias están mejor escritas que muchas historias principales.", False),
    ("completed", 4, None, "Al final resulta que el villano era su propio padre. Aun así, el combate es lo mejor.", True),
    ("played", 4, 1480, "", False),
    ("dropped", 2, 45, "No conseguí engancharme: el ritmo del principio es demasiado lento.", False),
    ("backlog", None, 0, "", False),
    ("completed", 3, 820, "", False),
    ("played", None, 95, "", False),
]

# Reseñas de los usuarios demo sobre los mismos juegos, para que las fichas muestren también las de otros.
OTHER_REVIEWS = [
    ("demo1", 0, 4, "Muy divertido con amigos, aunque la parte online necesita mejoras."),
    ("demo2", 1, 5, "Imprescindible. De los mejores juegos que he jugado nunca."),
    ("demo3", 1, 3, "Bonito y largo, pero el combate se me hizo repetitivo."),
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
        now = timezone.now()
        for days, (game, (status, rating, minutes, review, spoilers)) in enumerate(zip(games, LIBRARY)):
            LibraryEntry.objects.create(
                user=user, game=game, status=status, rating=rating,
                steam_playtime_minutes=minutes,
                review=review, review_has_spoilers=spoilers,
                reviewed_at=now - timedelta(days=days) if review else None,
            )

        for username, index, rating, review in OTHER_REVIEWS:
            other = User.objects.filter(username=username).first()
            if other is None or index >= len(games):
                continue
            LibraryEntry.objects.update_or_create(
                user=other, game=games[index],
                defaults={"rating": rating, "review": review, "reviewed_at": now - timedelta(hours=index + 2)},
            )

        self.stdout.write(self.style.SUCCESS(
            f"Usuario de prueba «{USERNAME}» listo con {len(games)} juegos. "
            f"La contraseña está en accounts/management/commands/seed_test_profile.py."
        ))

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
