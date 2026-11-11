"""Modelos de la biblioteca: cada juego de la biblioteca de un usuario (estado, nota y horas de cada plataforma), sus
sesiones de juego (para los retos de horas), las reseñas con sus «me gusta», comentarios e imágenes, las denuncias y
la lista de deseos. Se ven en la Ficha del juego, el Perfil, la Página de la reseña, la Lista de deseos y, con sus
portadas y estados, en casi todas las pantallas que enseñan juegos.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.functional import cached_property

from accounts.achievements import progress_by_game
from accounts.models import Profile
from games.models import Game, Platform, igdb_image_url

REVIEW_MAX_LENGTH = 8000
COMMENT_MAX_LENGTH = 1000
# «Lo mejor» y «Lo peor» de una reseña: cuántos puntos caben en cada lista y lo largo que puede ser cada uno.
MAX_POINTS = 5
POINT_MAX_LENGTH = 90
# Imágenes que caben en una reseña, entre las subidas y las capturas oficiales del juego.
MAX_IMAGES = 4
REPORT_DETAIL_MAX_LENGTH = 500
# Si el autor de una reseña lleva el doble de horas que al escribirla, y al menos estas más, se le propone actualizarla.
UPDATE_MIN_EXTRA_HOURS = 10
# Las notas van de 1 a 10 medias estrellas: 1 es media estrella y 10, cinco estrellas.
MAX_RATING = 10
# Los juegos importados que se han jugado en estos últimos días entran como «Jugando».
IMPORT_RECENT_DAYS = 14


class LibraryEntry(models.Model):
    class Status(models.TextChoices):
        PLAYING = "playing", "Jugando"
        COMPLETED = "completed", "Completado"
        # Jugado pero sin saber si se terminó: el estado que da la importación de Steam a los que no se juegan hace tiempo.
        PLAYED = "played", "Jugado"
        BACKLOG = "backlog", "Pendiente"
        DROPPED = "dropped", "Abandonado"

    MAX_RATING = MAX_RATING
    # De la nota más alta a la más baja: el widget pinta las medias estrellas al revés para que el CSS
    # pueda encender la que está bajo el ratón y las de antes.
    RATING_STEPS = range(MAX_RATING, 0, -1)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="library")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="library_entries")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BACKLOG)
    # Nota del usuario para el juego, de 1 a 10 medias estrellas; nula hasta que lo valora. Es la que cuenta
    # para la media de la comunidad; publicar una reseña con nota la cambia.
    rating = models.PositiveSmallIntegerField(
        "valoración",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_RATING)],
    )
    # Los rellena la importación de Steam; nulos en los juegos añadidos a mano.
    steam_playtime_minutes = models.PositiveIntegerField("minutos jugados en Steam", null=True, blank=True)
    steam_last_played = models.DateTimeField(null=True, blank=True)
    # Los rellena la importación de PlayStation (PS4/PS5); nulos si el juego no viene de PSN.
    psn_playtime_minutes = models.PositiveIntegerField("minutos jugados en PlayStation", null=True, blank=True)
    psn_last_played = models.DateTimeField(null=True, blank=True)
    # Los rellena la importación de Xbox; nulos si el juego no viene de Xbox.
    xbox_playtime_minutes = models.PositiveIntegerField("minutos jugados en Xbox", null=True, blank=True)
    xbox_last_played = models.DateTimeField(null=True, blank=True)
    # Las horas de las plataformas sin columna propia (las de HOURS_SOURCES que no están en COLUMN_SOURCES), como
    # {clave: {"minutes": minutos o None, "last_played": fecha ISO o None}}: añadir una plataforma no necesita migración.
    extra_hours = models.JSONField("horas de otras plataformas", default=dict, blank=True)
    # Las horas de todas las plataformas sumadas y la partida más reciente de cualquiera, para ordenar y filtrar en la
    # base de datos. Las pone save (refresh_totals); nulas si el juego no tiene horas de ninguna.
    total_minutes = models.PositiveIntegerField("minutos jugados en total", null=True, blank=True)
    last_played = models.DateTimeField("última partida", null=True, blank=True)
    # Cuándo lo pasó a «Completado» (lo pone library.views.set_status y se vacía si cambia de estado). Las
    # importaciones nunca lo completan, así que siempre es una fecha que puso el jugador; desde el repaso de la
    # biblioteca puede ser aproximada: la de su último logro o su última partida (library.backlog). Lo miden los retos.
    completed_at = models.DateTimeField("terminado", null=True, blank=True)
    # Cuándo se contestó en el repaso de la biblioteca (library.backlog), para no volver a preguntarlo.
    reviewed_at = models.DateTimeField("repasado", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="unique_library_entry_per_user_game"),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "-total_minutes"], name="entry_user_total_minutes"),
        ]
        verbose_name_plural = "library entries"

    def __str__(self):
        """Texto de la entrada en el admin: «usuario · juego · estado».

        Pantallas: Panel de administración.
        """
        return f"{self.user} · {self.game} · {self.get_status_display()}"

    @classmethod
    def imported_status(cls, minutes, last_played):
        """Estado de un juego que llega de una importación (Steam, PlayStation o Xbox) según cuánto y cuándo se jugó.

        Sin minutos ni partida: «Pendiente». Jugado en los últimos IMPORT_RECENT_DAYS días: «Jugando».
        Si no: «Jugado».

        Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y
        horas: el estado con el que llega cada juego importado), Ficha del juego (tus horas), Juegos importados (los
        que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto (progreso).
        """
        if not minutes and not last_played:
            return cls.Status.BACKLOG
        if last_played and last_played >= timezone.now() - timedelta(days=IMPORT_RECENT_DAYS):
            return cls.Status.PLAYING
        return cls.Status.PLAYED

    def update_import(self, platform, minutes, last_played):
        """Guarda los minutos y la última partida de una plataforma (clave de HOURS_SOURCES) en una entrada existente.

        Conserva el estado que puso el usuario, salvo «Pendiente» cuando ya hay horas jugadas: entonces
        lo cambia a «Jugando» o «Jugado». Devuelve True si ha cambiado algo.

        Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y
        horas), Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la
        importación), Retos (progreso), Página del reto (progreso).
        """
        fields = self.set_platform(platform, minutes, last_played)
        if self.status == self.Status.BACKLOG and minutes:
            self.status = self.imported_status(minutes, last_played)
            fields.append("status")
        if fields:
            self.save(update_fields=fields + ["updated_at"])
        return bool(fields)

    def minutes_on(self, platform):
        """Minutos jugados en platform (clave de HOURS_SOURCES), de su columna o de extra_hours, o None si no vienen
        de ella.

        Pantallas: Perfil (horas de cada plataforma), Ficha del juego (tus horas en cada plataforma), Catálogo, Inicio,
        Página de la lista, Página del reto (horas en las portadas), Nueva tier list (plantillas por plataforma).
        """
        if platform in COLUMN_SOURCES:
            return getattr(self, f"{platform}_playtime_minutes")
        return (self.extra_hours.get(platform) or {}).get("minutes")

    def last_played_on(self, platform):
        """La última partida en platform (clave de HOURS_SOURCES), de su columna o de extra_hours, o None.

        Pantallas: Ficha del juego (tus horas: la última partida en cada plataforma), Perfil (pestaña Diario: tu
        última partida), Repasar la biblioteca (tu última partida), Nueva tier list (lo jugado este año).
        """
        if platform in COLUMN_SOURCES:
            return getattr(self, f"{platform}_last_played")
        value = (self.extra_hours.get(platform) or {}).get("last_played")
        return parse_datetime(value) if value else None

    def set_platform(self, platform, minutes, last_played):
        """Pone en la entrada, sin guardarla, los minutos y la última partida de platform: en su columna si la tiene y,
        si no, en extra_hours, de donde se quita si las dos cosas vienen vacías. Devuelve los campos que ha cambiado.

        Pantallas: Editar perfil (Cuentas vinculadas: al importar), Perfil (biblioteca y horas), Ficha del juego (tus
        horas).
        """
        if platform in COLUMN_SOURCES:
            changed = []
            for field, value in ((f"{platform}_playtime_minutes", minutes), (f"{platform}_last_played", last_played)):
                if getattr(self, field) != value:
                    setattr(self, field, value)
                    changed.append(field)
            return changed
        wanted = None
        if minutes is not None or last_played is not None:
            wanted = {"minutes": minutes, "last_played": last_played.isoformat() if last_played else None}
        if self.extra_hours.get(platform) == wanted:
            return []
        extra = {key: value for key, value in self.extra_hours.items() if key != platform}
        if wanted is not None:
            extra[platform] = wanted
        self.extra_hours = extra
        return ["extra_hours"]

    def refresh_totals(self):
        """Recalcula, sin guardar, las horas de todas las plataformas sumadas (total_minutes) y la partida más reciente
        (last_played). Lo hace save; quien crea entradas con bulk_create, que no pasa por save, lo llama antes.

        Pantallas: Perfil (biblioteca: horas y orden por horas), Nueva tier list (plantillas por horas), Ficha del juego
        (la media de quienes tienen horas jugadas).
        """
        minutes = [self.minutes_on(key) for key in HOURS_SOURCES]
        self.total_minutes = None if all(value is None for value in minutes) else sum(value or 0 for value in minutes)
        self.last_played = max((day for day in (self.last_played_on(key) for key in HOURS_SOURCES) if day), default=None)

    def save(self, *args, **kwargs):
        """Guarda la entrada con sus horas totales y su última partida al día (refresh_totals): si solo se guardan unos
        campos y entre ellos hay horas de alguna plataforma, también esos dos.

        Pantallas: Perfil (biblioteca: horas y orden por horas), Ficha del juego (tu biblioteca y tus horas), Nueva tier
        list (plantillas por horas), Editar perfil (Cuentas vinculadas: al importar).
        """
        self.refresh_totals()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and HOURS_FIELDS & set(update_fields):
            kwargs["update_fields"] = list(dict.fromkeys([*update_fields, "total_minutes", "last_played"]))
        super().save(*args, **kwargs)

    @property
    def playtime_minutes(self):
        """Minutos jugados sumando todas las plataformas de HOURS_SOURCES, o None si no viene de ninguna.

        Pantallas: Perfil (horas de la biblioteca), Ficha del juego (tus horas), Escribir reseña (horas de la
        partida), Catálogo, Inicio, Página de la lista, Página del reto (horas en las portadas).
        """
        values = [self.minutes_on(key) for key in HOURS_SOURCES]
        if all(value is None for value in values):
            return None
        return sum(value or 0 for value in values)

    @property
    def verified_minutes(self):
        """Minutos jugados en las plataformas de VERIFIED_SOURCES (las que se leen de la cuenta), o None si de ninguna.

        Pantallas: Escribir reseña (horas de la partida), Página de la reseña, Ficha del juego (reseñas: sello de horas
        verificadas), Avisos (reseñas que se han quedado atrás).
        """
        values = [self.minutes_on(key) for key in VERIFIED_SOURCES]
        if all(value is None for value in values):
            return None
        return sum(value or 0 for value in values)

    @property
    def playtime_hours(self):
        """Horas jugadas (Steam + PlayStation + Xbox) redondeadas a una décima, o None si no hay datos.

        Pantallas: Perfil (horas de la biblioteca), Ficha del juego (tus horas), Escribir reseña (horas de la
        partida), Catálogo, Inicio, Página de la lista, Página del reto (horas en las portadas).
        """
        minutes = self.playtime_minutes
        return None if minutes is None else round(minutes / 60, 1)

    @property
    def hours_by_platform(self):
        """Horas de cada plataforma de la que vienen, de la que tiene más a la que menos: [{"key", "name", "hours",
        "last_played"}]. key es la clave de HOURS_SOURCES («steam», «psn» o «xbox»), que da su logo
        (library/_platform_mark.html). Solo salen las que llegan a una décima de hora: abrir un juego no es jugarlo.

        Pantallas: Perfil (horas de cada plataforma), Ficha del juego (tus horas en cada plataforma), Catálogo,
        Inicio, Página de la lista, Página del reto (horas en las portadas).
        """
        found = []
        for key, name in HOURS_SOURCES.items():
            hours = round((self.minutes_on(key) or 0) / 60, 1)
            if hours:
                found.append({"key": key, "name": name, "hours": hours, "last_played": self.last_played_on(key)})
        return sorted(found, key=lambda item: -item["hours"])

    @property
    def playtime_sources(self):
        """Plataformas de las que vienen las horas jugadas, como claves de HOURS_SOURCES: p. ej. ["steam", "psn"].

        Pantallas: Escribir reseña (de dónde salen las horas al publicarla), Página de la reseña (sello de horas
        verificadas).
        """
        return [key for key in HOURS_SOURCES if self.minutes_on(key) is not None]

    @cached_property
    def achievements(self):
        """Cómo va con los logros de este juego en cada plataforma de la que se han importado (lo que da
        accounts.achievements.progress_by_game), o una lista vacía si no se conocen.

        Pantallas: Ficha del juego (tus logros en cada plataforma).
        """
        return progress_by_game(self.user_id, [self.game_id]).get(self.game_id, [])

    @cached_property
    def timeline(self):
        """Lo que dice el diario de partidas de este juego (library.diary.timeline): su primera partida o su primer
        logro, cuántas sesiones hay y la última, o None si no hay nada.

        Pantallas: Ficha del juego (Tu biblioteca: tu primera partida y tus sesiones).
        """
        # Se importa aquí: library.diary importa estos modelos.
        from . import diary

        return diary.timeline(self.user_id, self.game_id)

    @property
    def stars(self):
        """Nota en estrellas (de 0,5 a 5), o None sin nota.

        Pantallas: Perfil (nota media de las cifras de la cabecera).
        """
        return self.rating / 2 if self.rating else None


# Plataformas de las que se importan horas jugadas: clave → nombre. Las de COLUMN_SOURCES tienen sus columnas en
# LibraryEntry (<clave>_playtime_minutes y <clave>_last_played); cualquier otra va en LibraryEntry.extra_hours, así que
# añadir una plataforma no necesita migración.
HOURS_SOURCES = {
    "steam": "Steam", "psn": "PlayStation", "xbox": "Xbox", "epic": "Epic Games", "gog": "GOG", "ea": "EA app",
    "ubisoft": "Ubisoft Connect", "battlenet": "Battle.net", "amazon": "Amazon Games", "otras": "Otras tiendas",
}
COLUMN_SOURCES = ("steam", "psn", "xbox")
# Las horas que nadie puede tocar: las que se leen de la cuenta de cada plataforma. Las de un archivo (Playnite u otro
# tracker) son tus horas, pero no cuentan para el sello de horas verificadas de las reseñas ni para la media «con
# horas jugadas» de la ficha.
VERIFIED_SOURCES = ("steam", "psn", "xbox")
# Los campos de LibraryEntry que guardan horas: si se guardan, también las horas totales (LibraryEntry.save).
HOURS_FIELDS = {
    f"{key}_{part}" for key in COLUMN_SOURCES for part in ("playtime_minutes", "last_played")
} | {"extra_hours"}
# Logo de cada una, para el sello de horas verificadas de las reseñas.
HOURS_LOGOS = {
    "steam": "games/shop_icons/steam.svg", "psn": "games/shop_icons/playstation.svg", "xbox": "games/shop_icons/xbox.svg",
}
class PlaySession(models.Model):
    """Lo que ha jugado un usuario a un juego en una plataforma un día: lo que ha subido su tiempo entre dos
    sincronizaciones, apuntado el día de su última partida (varias el mismo día se suman). since es el día de la
    partida anterior: si es el mismo, todo se jugó ese día; si es antes, en algún momento entre los dos; vacío si no se
    sabe (las de antes del diario de partidas y las de cuentas que no dan la última partida).

    Lo apunta la importación (accounts.matching.update_library) solo si ya se conocían las horas de esa plataforma:
    lo que trae la primera importación, o una cuenta de Steam recién vinculada, es de antes y no se sabe cuándo se
    jugó. Es lo que miden los retos de horas y, más adelante, el diario de partidas.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="play_sessions")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="play_sessions", verbose_name="juego")
    platform = models.CharField("plataforma", max_length=10, choices=list(HOURS_SOURCES.items()))
    day = models.DateField("día")
    since = models.DateField("desde", null=True, blank=True)
    minutes = models.PositiveIntegerField("minutos")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-day", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "game", "platform", "day"], name="unique_session_per_day"),
        ]
        indexes = [models.Index(fields=["user", "day"], name="session_user_day")]
        verbose_name = "sesión de juego"
        verbose_name_plural = "sesiones de juego"

    def __str__(self):
        """Texto en el admin: «usuario · juego · día · minutos».

        Pantallas: Panel de administración.
        """
        return f"{self.user} · {self.game} · {self.day} · {self.minutes} min"

    @classmethod
    def add(cls, user, game_id, platform, day, minutes, since=None):
        """Suma minutes a lo que user ha jugado a game_id en platform el día day, jugados desde since (el día de la
        partida anterior; None si no se sabe). La crea si es la primera de ese día; si ya había una, se queda con el
        principio más temprano, o sin principio si alguna de las dos no lo sabe.

        Pantallas: Editar perfil (Cuentas vinculadas: al importar horas), Perfil (modal «Vincular cuentas» de su
        dueño; pestaña Diario), Retos (progreso de los de horas), Página del reto (progreso), Nueva tier list
        (plantillas por horas jugadas), Ficha del juego (Tu biblioteca: tus sesiones).
        """
        rows = cls.objects.filter(user=user, game_id=game_id, platform=platform, day=day)
        existing = rows.first()
        if existing is None:
            cls.objects.create(user=user, game_id=game_id, platform=platform, day=day, minutes=minutes, since=since)
            return
        start = min(existing.since, since) if existing.since and since else None
        rows.update(minutes=models.F("minutes") + minutes, since=start)


# Estado de la biblioteca al publicar una reseña → cómo se cuenta en ella (Pendiente no dice nada).
STATUS_NOTES = {
    LibraryEntry.Status.COMPLETED: "Lo terminó",
    LibraryEntry.Status.PLAYING: "Aún jugándolo",
    LibraryEntry.Status.PLAYED: "Lo jugó",
    LibraryEntry.Status.DROPPED: "Lo abandonó",
}


class ReviewQuerySet(models.QuerySet):
    def published(self):
        """Reseñas publicadas, sin los borradores.

        Pantallas: Ficha del juego (reseñas: sin los borradores), Perfil (pestaña Reseñas y reseña destacada),
        Página de la reseña, Inicio (feed).
        """
        return self.filter(is_draft=False)

    def visible_to(self, viewer):
        """Reseñas publicadas que viewer puede leer, de la más reciente a la más antigua, con sus «me gusta»
        (like_count) y comentarios (comment_count) contados y sus imágenes ya cargadas.

        Las de perfiles privados solo las ve su autor, igual que su biblioteca (Profile.visible_filter).

        Pantallas: Ficha del juego (reseñas: solo las que puede leer quien mira), Perfil (pestaña Reseñas y reseña
        destacada), Página de la reseña, Inicio (feed).
        """
        return (
            self.published()
            .filter(Profile.visible_filter(viewer))
            .select_related("user__profile", "user__psn_account", "user__xbox_account", "game", "platform")
            .prefetch_related("user__steam_accounts", "images")
            .annotate(
                like_count=models.Count("likes", distinct=True),
                comment_count=models.Count("comments", distinct=True),
            )
            .order_by("-published_at", "-id")
        )


class Review(models.Model):
    """Reseña de un juego. Cada usuario puede escribir varias del mismo juego (una por partida, por ejemplo
    al rejugarlo); se ven en la ficha, en su perfil, en la portada y en su propia página.

    Al publicarla se guardan las horas que llevaba jugadas (de las importaciones de Steam, PlayStation y
    Xbox) y el estado del juego en su biblioteca, para enseñar «escrita a las 12 h», filtrar las de quien lo
    ha terminado y plegar las que van más adelante que quien las lee. La nota va de 1 a 10 medias estrellas.
    Los borradores solo los ve su autor.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField(
        "nota", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(MAX_RATING)]
    )
    text = models.TextField("reseña", max_length=REVIEW_MAX_LENGTH)
    # Puntos cortos de «Lo mejor» y «Lo peor» (hasta MAX_POINTS en cada lista), con el mismo formato que el texto.
    pros = models.JSONField("lo mejor", default=list, blank=True)
    cons = models.JSONField("lo peor", default=list, blank=True)
    has_spoilers = models.BooleanField("toda ella es un spoiler", default=False)
    platform = models.ForeignKey(
        Platform, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="plataforma"
    )
    started_on = models.DateField("empezado el", null=True, blank=True)
    finished_on = models.DateField("terminado el", null=True, blank=True)
    is_replay = models.BooleanField("es una rejugada", default=False)
    # Estado del juego en la biblioteca del autor y horas que llevaba (y de qué plataformas, claves de
    # HOURS_SOURCES separadas por comas) al publicarla. Las horas vacías: no venía de ninguna importación.
    status = models.CharField("estado al publicarla", max_length=20, choices=LibraryEntry.Status.choices, blank=True)
    hours = models.DecimalField("horas al publicarla", max_digits=7, decimal_places=1, null=True, blank=True)
    hours_sources = models.CharField("de dónde salen las horas", max_length=30, blank=True)
    is_draft = models.BooleanField("borrador", default=False)
    published_at = models.DateTimeField("publicada", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Sus denuncias se borran con ella.
    reports = GenericRelation("Report", related_query_name="review")

    objects = ReviewQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-id"]
        indexes = [models.Index(fields=["game", "is_draft", "published_at"])]
        verbose_name = "reseña"
        verbose_name_plural = "reseñas"

    def __str__(self):
        """Texto en el admin: «usuario · juego».

        Pantallas: Panel de administración.
        """
        return f"{self.user} · {self.game}"

    def get_absolute_url(self):
        """URL de la página de la reseña: /u/<usuario>/resenas/<id>/.

        Pantallas: Ficha del juego (reseñas), Perfil (pestaña Reseñas y reseña destacada), Página de la reseña, Inicio
        (feed), Avisos (los enlaces a cada reseña).
        """
        return reverse("review_detail", args=[self.user.username, self.pk])

    @property
    def stars(self):
        """Nota en estrellas (de 0,5 a 5), o None sin nota.

        Pantallas: ninguna (ahora no la usa nadie: las estrellas se pintan desde rating con library/_stars.html).
        """
        return self.rating / 2 if self.rating else None

    @property
    def finished(self):
        """Indica si el autor lo tenía como «Completado» al publicarla.

        Pantallas: Ficha del juego, Perfil, Página de la reseña, Página del reto (si lo terminó al publicarla).
        """
        return self.status == LibraryEntry.Status.COMPLETED

    @property
    def status_note(self):
        """Cómo iba el autor con el juego al publicarla, para la reseña: «Lo terminó», «Aún jugándolo»...; vacía si no se sabe.

        Pantallas: Ficha del juego, Perfil, Página de la reseña (cómo iba el autor con el juego al publicarla).
        """
        return STATUS_NOTES.get(self.status, "")

    @property
    def hours_source_names(self):
        """Nombres de las plataformas de las que salen las horas: «Steam» o «Steam y PlayStation».

        Pantallas: Ficha del juego, Perfil, Página de la reseña (de dónde salen las horas).
        """
        names = [HOURS_SOURCES[key] for key in self.hours_sources.split(",") if key in HOURS_SOURCES]
        return " y ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else "".join(names)

    @property
    def hours_source_logos(self):
        """Plataformas de las que salen las horas como [(nombre, plantilla de su logo)], para el sello de horas verificadas.

        Pantallas: Ficha del juego, Perfil, Página de la reseña (sello de horas verificadas).
        """
        return [(HOURS_SOURCES[key], HOURS_LOGOS.get(key, "")) for key in self.hours_sources.split(",") if key in HOURS_SOURCES]

    def hours_since(self, entry):
        """Horas que ha jugado el autor desde que la publicó, si son bastantes para proponerle actualizarla; si no, None.

        entry es su entrada de biblioteca del juego. Son bastantes si lleva al menos el doble que al escribirla
        y como mínimo UPDATE_MIN_EXTRA_HOURS más («Llevas 40 h más: ¿la actualizas?»). Los borradores y las
        reseñas escritas sin horas importadas no cuentan.

        Pantallas: Página de la reseña (propuesta de actualizarla), Avisos (reseñas que se han quedado atrás).
        """
        # Se compara con las mismas horas con las que se escribió: las verificadas (snapshot).
        minutes = entry.verified_minutes if entry is not None else None
        if self.is_draft or self.hours is None or minutes is None:
            return None
        written = float(self.hours)
        extra = round(minutes / 60, 1) - written
        return round(extra) if extra >= max(UPDATE_MIN_EXTRA_HOURS, written) else None

    def snapshot(self, entry):
        """Apunta el estado y las horas que tiene ahora la entrada de biblioteca entry (o nada si no hay): solo las
        verificadas, las de VERIFIED_SOURCES, que son las del sello de la reseña.

        Pantallas: Escribir reseña (al publicar se guardan el estado y las horas), Página de la reseña,
        Ficha del juego, Perfil (datos de la partida).
        """
        self.status = entry.status if entry else ""
        minutes = entry.verified_minutes if entry else None
        hours = None if minutes is None else round(minutes / 60, 1)
        self.hours = None if hours is None else Decimal(str(hours))
        self.hours_sources = (
            ",".join(key for key in VERIFIED_SOURCES if entry.minutes_on(key) is not None) if hours is not None else ""
        )


class ReviewLike(models.Model):
    """«Me gusta» de un usuario en una reseña (uno por usuario y reseña)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_likes")
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "review"], name="unique_like_per_user_review")]
        verbose_name = "me gusta"
        verbose_name_plural = "me gusta"

    def __str__(self):
        """Texto en el admin: «usuario → reseña».

        Pantallas: Panel de administración.
        """
        return f"{self.user} → {self.review}"


class ReviewComment(models.Model):
    """Comentario en una reseña. Lo pueden borrar quien lo escribió y el autor de la reseña."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_comments")
    text = models.TextField("comentario", max_length=COMMENT_MAX_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)
    # Sus denuncias se borran con él.
    reports = GenericRelation("Report", related_query_name="comment")

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "comentario"
        verbose_name_plural = "comentarios"

    def __str__(self):
        """Texto en el admin: «usuario en reseña: comienzo del comentario».

        Pantallas: Panel de administración.
        """
        return f"{self.user} en {self.review}: {self.text[:40]}"

    def get_absolute_url(self):
        """URL del comentario dentro de la página de su reseña.

        Pantallas: Avisos (enlace a un comentario de una reseña), Inicio (feed).
        """
        return f"{self.review.get_absolute_url()}#comentario-{self.pk}"

    def can_delete(self, user):
        """Indica si user puede borrar el comentario: es suyo o es el autor de la reseña.

        Pantallas: Página de la reseña (botón «Borrar» de los comentarios).
        """
        return user.is_authenticated and user.pk in (self.user_id, self.review.user_id)


def attached_image_upload_to(instance, filename):
    """Ruta de una imagen adjunta: la carpeta la decide cada modelo (upload_folder) y el nombre aleatorio, accounts.images.

    Pantallas: Escribir reseña, Publicar, Inicio (dónde se guardan las imágenes subidas a reseñas y publicaciones).
    """
    return f"{instance.upload_folder()}/{filename}"


class AttachedImage(models.Model):
    """Imagen adjunta a algo que escribe un usuario: de momento, las reseñas; más adelante, también las publicaciones.

    O la ha subido él (image, pasada a WebP y sin EXIF por accounts.images, con su miniatura en thumb) o es
    una captura o un arte oficial del juego de IGDB (igdb_image_id), que no se sube. Cada una se puede marcar
    como spoiler: entonces va borrosa hasta que se pulsa.
    """

    image = models.ImageField("imagen", upload_to=attached_image_upload_to, blank=True)
    thumb = models.ImageField("miniatura", upload_to=attached_image_upload_to, blank=True)
    igdb_image_id = models.CharField("imagen oficial de IGDB", max_length=40, blank=True)
    is_spoiler = models.BooleanField("spoiler", default=False)
    position = models.PositiveSmallIntegerField("orden", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["position", "id"]

    def upload_folder(self):
        """Carpeta de MEDIA_ROOT en la que se guardan las imágenes subidas; la pone cada modelo.

        Pantallas: Escribir reseña, Publicar, Inicio (carpeta de las imágenes subidas).
        """
        raise NotImplementedError

    @property
    def is_official(self):
        """Indica si es una imagen oficial del juego (de IGDB) y no una subida por el usuario.

        Pantallas: Escribir reseña, Página de la reseña, Ficha del juego, Perfil, Inicio, Página de la publicación
        (imágenes de la ficha frente a las subidas).
        """
        return bool(self.igdb_image_id)

    @property
    def url(self):
        """URL de la imagen grande, para el visor a pantalla completa.

        Pantallas: Página de la reseña, Página de la publicación, Ficha del juego, Perfil, Inicio (el visor a pantalla
        completa de las imágenes).
        """
        return igdb_image_url(self.igdb_image_id, "t_1080p") if self.is_official else self.image.url

    @property
    def thumb_url(self):
        """URL de la miniatura: la de IGDB o la reducida al subirla (la grande si no la hay).

        Pantallas: Escribir reseña, Página de la reseña, Ficha del juego, Perfil, Inicio, Página de la publicación
        (miniaturas de las imágenes).
        """
        if self.is_official:
            return igdb_image_url(self.igdb_image_id, "t_screenshot_big")
        return (self.thumb or self.image).url

    def delete_files(self):
        """Borra del disco la imagen subida y su miniatura (las oficiales no tienen archivo).

        Pantallas: Escribir reseña, Página de la publicación (al quitar una imagen o borrar lo que la lleva),
        Panel de administración.
        """
        for file in (self.image, self.thumb):
            if file:
                file.delete(save=False)


class ReviewImage(AttachedImage):
    """Imagen de una reseña (como mucho MAX_IMAGES por reseña). Las subidas se guardan en resenas/<id del autor>/."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="images")

    class Meta(AttachedImage.Meta):
        verbose_name = "imagen de una reseña"
        verbose_name_plural = "imágenes de las reseñas"

    def __str__(self):
        """Texto en el admin: «reseña · imagen n».

        Pantallas: Panel de administración.
        """
        return f"{self.review} · imagen {self.position + 1}"

    def upload_folder(self):
        """Las imágenes de las reseñas van en resenas/<id del autor>.

        Pantallas: Escribir reseña (carpeta de las imágenes de las reseñas).
        """
        return f"resenas/{self.review.user_id}"


class Report(models.Model):
    """Denuncia de un usuario sobre algo que ha escrito otro (una reseña o un comentario) para que la revise un administrador.

    Va por contenttypes para que sirva también para lo que se añada después (publicaciones, grupos...). Hay
    una por usuario y cosa denunciada: volver a denunciarla cambia el motivo y la deja otra vez pendiente.
    """

    class Reason(models.TextChoices):
        SPAM = "spam", "Spam o publicidad"
        ABUSE = "abuse", "Insultos, acoso u odio"
        SPOILER = "spoiler", "Destripa la historia sin avisar"
        IMAGE = "image", "Imagen inapropiada"
        OTHER = "other", "Otro motivo"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports", verbose_name="denunciante"
    )
    reason = models.CharField("motivo", max_length=20, choices=Reason.choices)
    detail = models.CharField("detalles", max_length=REPORT_DETAIL_MAX_LENGTH, blank=True)
    created_at = models.DateTimeField("denunciada", auto_now_add=True)
    resolved_at = models.DateTimeField("revisada", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["content_type", "object_id", "reporter"], name="unique_report_per_user"),
        ]
        indexes = [models.Index(fields=["content_type", "object_id"])]
        verbose_name = "denuncia"
        verbose_name_plural = "denuncias"

    def __str__(self):
        """Texto en el admin: «denunciante → lo denunciado: motivo».

        Pantallas: Panel de administración.
        """
        return f"{self.reporter} → {self.target or '(borrado)'}: {self.get_reason_display()}"

    @property
    def target_kind(self):
        """Qué se ha denunciado, en palabras: «reseña» o «comentario» (el nombre del modelo de lo denunciado).

        Pantallas: Panel de administración (columna de qué se ha denunciado).
        """
        return self.content_type.name


class WishlistItem(models.Model):
    """Juego que el usuario quiere («Lo quiero»), con un precio por debajo del cual avisarle (opcional).

    Los avisos los crea library.wishlist.check_alerts después de actualizar los precios; también avisa
    cuando el juego está rebajado a su mínimo histórico, aunque no haya precio objetivo.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="wishlisted_by")
    target_price = models.DecimalField(
        "avisar por debajo de", max_digits=8, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    # Precio del que se avisó la última vez: no se repite el aviso hasta que baje más. Se vacía cuando el
    # precio vuelve a subir, para avisar de la siguiente bajada.
    alerted_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Lista de deseos de Steam (accounts.steam.import_wishlist): from_steam si lo trajo la importación, y entonces
    # se quita cuando deja de estar allí; steam_added_at, mientras está en la lista de Steam, la fecha en que se
    # añadió allí, y steam_priority su puesto (vacío si no lo ha ordenado), para ordenar «Como en Steam».
    from_steam = models.BooleanField("traído de Steam", default=False)
    steam_added_at = models.DateTimeField("añadido en Steam", null=True, blank=True)
    steam_priority = models.PositiveIntegerField("puesto en Steam", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["user", "game"], name="unique_wishlist_item_per_user_game")]
        verbose_name = "juego de la lista de deseos"
        verbose_name_plural = "listas de deseos"

    def __str__(self):
        """Texto en el admin: «usuario · juego».

        Pantallas: Panel de administración.
        """
        return f"{self.user} · {self.game}"

    @property
    def on_steam(self):
        """Indica si el juego está también en la lista de deseos de Steam del usuario (según la última importación).

        Pantallas: Lista de deseos (sello de la lista de Steam).
        """
        return self.steam_added_at is not None
