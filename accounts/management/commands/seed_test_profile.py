from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from challenges.models import Challenge, Participation
from games.models import Game, Genre
from library.models import LibraryEntry, PlaySession, Review, ReviewComment, ReviewLike
from lists.models import GameList, ListCollaborator, ListComment, ListItem, ListLike
from social.activity import IMPORT_COVERS
from social.models import Activity, Follow

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
# Quién sigue a quién, para ver seguidores, «Te sigue» y los avisos de la campana: (seguidor, seguido). demo3 sigue
# al usuario de prueba pero no al revés, para ver el botón «Seguir también».
FOLLOWS = [
    ("demo1", USERNAME), ("demo2", USERNAME), ("demo3", USERNAME),
    (USERNAME, "demo1"), (USERNAME, "demo2"),
    ("demo1", "demo2"), ("demo2", "demo3"),
]
# Listas de ejemplo: (dueño, título, descripción, pública, tipo, [(posición del juego, comentario, nivel)]). Las
# posiciones van sobre los juegos de LIBRARY y, detrás, EXTRA_GAMES juegos más del catálogo que el usuario de prueba no
# tiene, para ver en la página de una lista lo que le falta y lo que cuesta. El nivel (el id de uno de los de
# lists.models.DEFAULT_TIERS, o None) solo cuenta en las tier lists.
EXTRA_GAMES = 3
LISTS = [
    (USERNAME, "Mis imprescindibles", "Los que **recomendaría a cualquiera**, en orden.", True, GameList.Mode.RANKED, [
        (1, "Las misiones secundarias mejor escritas que he jugado.", None), (0, "Cada rincón esconde algo.", None),
        (6, "", None), (2, "", None),
    ]),
    (USERNAME, "Para este invierno", "", False, GameList.Mode.LIST, [(5, "", None), (3, "", None), (8, "", None)]),
    (USERNAME, "Mis juegos, por niveles", "Mi tier list de lo que he jugado.", True, GameList.Mode.TIERS, [
        (1, "", 0), (6, "", 0), (0, "", 1), (2, "", 1), (3, "", 2), (7, "", 3), (4, "", 5), (5, "", None),
    ]),
    ("demo1", "Para jugar con amigos", "Cooperativos y competitivos que funcionan bien en grupo.", True, GameList.Mode.LIST, [
        (0, "Mejor con cuatro.", None), (3, "", None), (8, "Imprescindible en una noche de sofá.", None), (9, "", None),
        (10, "", None),
    ]),
]
# Gente que colabora en las listas de ejemplo: (título de la lista, usuario, posiciones en la lista de los juegos que
# añadió él). El usuario de prueba ayuda a demo1 con la suya.
LIST_COLLABORATORS = [("Para jugar con amigos", USERNAME, [3, 4])]
# Retos de ejemplo: (creador, título, tipo, objetivo, slug del género o None, días hasta que empieza y hasta que termina
# (desde hoy; None, sin fecha), quién más se une). El de juegos concretos sale de la lista «Mis imprescindibles».
CHALLENGES = [
    (USERNAME, "Pásate «Mis imprescindibles»", "games", None, None, None, None, ["demo1"]),
    ("demo1", "Tres de rol antes de Navidad", "count", 3, "role-playing-rpg", -30, 60, [USERNAME, "demo2"]),
    ("demo2", "Maratón de 20 horas", "hours", 20, None, -7, 23, [USERNAME]),
]
# Lo que ha jugado el usuario de prueba estos días, para el reto de horas: (posición del juego en LIBRARY, hace cuántos
# días, minutos).
SESSIONS = [(0, 1, 150), (0, 3, 90), (6, 2, 240), (7, 5, 60)]
# «Me gusta» en las listas de ejemplo, con un comentario si no está vacío: (usuario, título de la lista, comentario).
LIST_REACTIONS = [
    ("demo1", "Mis imprescindibles", "¡Gran lista! Me apunto el primero para estas vacaciones."),
    ("demo2", "Mis imprescindibles", ""),
    (USERNAME, "Para jugar con amigos", "Le añadiría alguno de carreras."),
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

        Activity.objects.filter(user=user).delete()
        LibraryEntry.objects.filter(user=user).delete()
        Review.objects.filter(user=user).delete()
        now = timezone.now()
        reviews = {}
        # Su actividad: primero la importación de Steam de la que salen sus horas y después un juego al día, con
        # su estado, su nota y su reseña juntos, como los junta social.activity.
        played = sorted((pair for pair in zip(games, LIBRARY) if pair[1][2]), key=lambda pair: -pair[1][2])
        when = now - timedelta(days=len(LIBRARY))
        Activity.objects.create(
            user=user, kind=Activity.Kind.IMPORT, created_at=when, updated_at=when,
            data={"platform": "steam", "count": len(played), "games": [game.pk for game, _ in played[:IMPORT_COVERS]]},
        )
        for days, (game, (status, rating, minutes, text, spoilers)) in enumerate(zip(games, LIBRARY)):
            when = now - timedelta(days=days)
            entry = LibraryEntry.objects.create(
                user=user, game=game, status=status, rating=rating, steam_playtime_minutes=minutes,
                completed_at=when if status == LibraryEntry.Status.COMPLETED else None,
            )
            if text:
                reviews[days] = self.review(user, entry, text, spoilers, when)
            self.activity(user, game, when, status, rating, reviews.get(days))

        for username, index, rating, minutes, status, text in OTHER_REVIEWS:
            other = User.objects.filter(username=username).first()
            if other is None or index >= len(games):
                continue
            entry, _ = LibraryEntry.objects.update_or_create(
                user=other, game=games[index],
                defaults={"rating": rating, "status": status, "steam_playtime_minutes": minutes},
            )
            Review.objects.filter(user=other, game=games[index]).delete()
            Activity.objects.filter(user=other, game=games[index]).delete()
            when = now - timedelta(hours=index + 2)
            self.activity(other, games[index], when, status, rating, self.review(other, entry, text, False, when))

        for username, index, comment in REACTIONS:
            other = User.objects.filter(username=username).first()
            if other is None or index not in reviews:
                continue
            ReviewLike.objects.get_or_create(user=other, review=reviews[index])
            if comment:
                ReviewComment.objects.create(user=other, review=reviews[index], text=comment)

        # Los seguidores y seguidos del usuario de prueba se rehacen; los avisos de seguidor no se repiten.
        Follow.objects.filter(follower=user).delete()
        Follow.objects.filter(followed=user).delete()
        people = {u.username: u for u in User.objects.filter(username__in={name for pair in FOLLOWS for name in pair})}
        for follower, followed in FOLLOWS:
            if follower in people and followed in people:
                Follow.objects.follow(people[follower], people[followed])

        extras = list(Game.objects.exclude(cover_image_id="").exclude(pk__in=[game.pk for game in games])[:EXTRA_GAMES])
        self.lists(user, games + extras)
        self.challenges(user, games)

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

    def lists(self, user, catalog):
        """Rehace las listas de ejemplo (LISTS) del usuario de prueba y de los usuarios demo que existan, con sus juegos
        (catalog: los de su biblioteca y los extra, en orden), sus comentarios y sus niveles, cada una con su actividad
        (creada hace unas horas, con sus juegos), con los «me gusta» y comentarios de LIST_REACTIONS y con quien colabora
        en ellas (LIST_COLLABORATORS)."""
        GameList.objects.filter(user=user).delete()
        names = {owner for owner, *_ in LISTS} | {username for username, *_ in LIST_REACTIONS}
        names |= {username for _, username, _ in LIST_COLLABORATORS}
        people = {u.username: u for u in User.objects.filter(username__in=names)}
        lists = {}
        for hours, (owner, title, description, public, mode, entries) in enumerate(LISTS, start=1):
            if owner not in people:
                continue
            GameList.objects.filter(user=people[owner], title=title).delete()
            game_list = GameList.objects.create(
                user=people[owner], title=title, description=description, is_public=public, mode=mode,
            )
            games = [catalog[index] for index, *_ in entries if index < len(catalog)]
            for position, (index, note, tier) in enumerate(entries):
                if index < len(catalog):
                    ListItem.objects.create(
                        game_list=game_list, game=catalog[index], position=position, note=note, tier=tier,
                        added_by=people[owner],
                    )
            when = timezone.now() - timedelta(hours=hours)
            Activity.objects.create(
                user=people[owner], kind=Activity.Kind.LIST, game_list=game_list,
                data={"created": True, "added": len(games)}, created_at=when, updated_at=when,
            )
            lists[title] = game_list
        for username, title, comment in LIST_REACTIONS:
            if username in people and title in lists:
                ListLike.objects.get_or_create(user=people[username], game_list=lists[title])
                if comment:
                    ListComment.objects.create(user=people[username], game_list=lists[title], text=comment)
        for title, username, positions in LIST_COLLABORATORS:
            if username in people and title in lists:
                ListCollaborator.objects.create(game_list=lists[title], user=people[username], accepted_at=timezone.now())
                lists[title].items.filter(position__in=positions).update(added_by=people[username])

    def challenges(self, user, games):
        """Rehace los retos de ejemplo (CHALLENGES) con quienes se unen a ellos, y las sesiones de juego del usuario de
        prueba (SESSIONS, sobre games, los juegos de su biblioteca) para que el reto de horas tenga progreso."""
        names = {creator for creator, *_ in CHALLENGES} | {name for *_, others in CHALLENGES for name in others}
        people = {u.username: u for u in User.objects.filter(username__in=names)}
        today = timezone.localdate()
        for creator, title, kind, target, genre, starts, ends, others in CHALLENGES:
            if creator not in people:
                continue
            Challenge.objects.filter(creator=people[creator], title=title).delete()
            challenge = Challenge.objects.create(
                creator=people[creator], title=title, kind=kind, target=target,
                genre=Genre.objects.filter(slug=genre).first() if genre else None,
                starts_on=today + timedelta(days=starts) if starts is not None else None,
                ends_on=today + timedelta(days=ends) if ends is not None else None,
            )
            if kind == Challenge.Kind.GAMES:
                source = GameList.objects.filter(user=people[creator], title="Mis imprescindibles").first()
                if source:
                    challenge.source_list = source
                    challenge.save(update_fields=["source_list"])
                    challenge.games.set(source.items.values_list("game_id", flat=True))
            for name in [creator, *others]:
                if name in people:
                    Participation.objects.create(challenge=challenge, user=people[name])
        PlaySession.objects.filter(user=user).delete()
        for index, days, minutes in SESSIONS:
            if index < len(games):
                PlaySession.add(user, games[index].pk, "steam", today - timedelta(days=days), minutes)

    def activity(self, user, game, when, status, rating, review):
        """Apunta en la actividad de user lo que hizo con game en el momento when: su estado, su nota y su reseña (o None)."""
        Activity.objects.create(
            user=user, kind=Activity.Kind.GAME, game=game, status=status, rating=rating, review=review,
            created_at=when, updated_at=when,
        )

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
        # Sin biblioteca no queda actividad, salvo la de sus reseñas, que siguen publicadas (como al quitar un juego).
        user.activities.filter(review__isnull=True).delete()
        user.activities.update(status="", rating=None)
        user.steam_accounts.all().delete()
        PSNAccount.objects.filter(user=user).delete()
        XboxAccount.objects.filter(user=user).delete()
        user.platform_accounts.all().delete()
        self.stdout.write(self.style.SUCCESS(
            f"Usuario de prueba «{EMPTY_USERNAME}» listo y vacío. "
            f"La contraseña está en accounts/management/commands/seed_test_profile.py."
        ))
