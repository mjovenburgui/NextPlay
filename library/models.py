from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from games.models import Game, Platform

REVIEW_MAX_LENGTH = 8000
COMMENT_MAX_LENGTH = 1000
# «Lo mejor» y «Lo peor» de una reseña: cuántos puntos caben en cada lista y lo largo que puede ser cada uno.
MAX_POINTS = 5
POINT_MAX_LENGTH = 90
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="unique_library_entry_per_user_game"),
        ]
        indexes = [models.Index(fields=["user", "status"])]
        verbose_name_plural = "library entries"

    def __str__(self):
        """Texto de la entrada en el admin: «usuario · juego · estado»."""
        return f"{self.user} · {self.game} · {self.get_status_display()}"

    @classmethod
    def imported_status(cls, minutes, last_played):
        """Estado de un juego que llega de una importación (Steam, PlayStation o Xbox) según cuánto y cuándo se jugó.

        Sin minutos ni partida: «Pendiente». Jugado en los últimos IMPORT_RECENT_DAYS días: «Jugando».
        Si no: «Jugado».
        """
        if not minutes and not last_played:
            return cls.Status.BACKLOG
        if last_played and last_played >= timezone.now() - timedelta(days=IMPORT_RECENT_DAYS):
            return cls.Status.PLAYING
        return cls.Status.PLAYED

    def update_import(self, platform, minutes, last_played):
        """Guarda los minutos y la última partida de una plataforma ("steam", "psn" o "xbox") en una entrada existente.

        Conserva el estado que puso el usuario, salvo «Pendiente» cuando ya hay horas jugadas: entonces
        lo cambia a «Jugando» o «Jugado». Devuelve True si ha cambiado algo.
        """
        fields = []
        if getattr(self, f"{platform}_playtime_minutes") != minutes:
            setattr(self, f"{platform}_playtime_minutes", minutes)
            fields.append(f"{platform}_playtime_minutes")
        if getattr(self, f"{platform}_last_played") != last_played:
            setattr(self, f"{platform}_last_played", last_played)
            fields.append(f"{platform}_last_played")
        if self.status == self.Status.BACKLOG and minutes:
            self.status = self.imported_status(minutes, last_played)
            fields.append("status")
        if fields:
            self.save(update_fields=fields + ["updated_at"])
        return bool(fields)

    @property
    def playtime_minutes(self):
        """Minutos jugados sumando Steam, PlayStation y Xbox, o None si no viene de ninguna."""
        values = [self.steam_playtime_minutes, self.psn_playtime_minutes, self.xbox_playtime_minutes]
        if all(value is None for value in values):
            return None
        return sum(value or 0 for value in values)

    @property
    def playtime_hours(self):
        """Horas jugadas (Steam + PlayStation + Xbox) redondeadas a una décima, o None si no hay datos."""
        minutes = self.playtime_minutes
        return None if minutes is None else round(minutes / 60, 1)

    @property
    def steam_hours(self):
        """Horas jugadas en Steam redondeadas a una décima, o None si no viene de Steam."""
        if self.steam_playtime_minutes is None:
            return None
        return round(self.steam_playtime_minutes / 60, 1)

    @property
    def playtime_sources(self):
        """Plataformas de las que vienen las horas jugadas, como claves de HOURS_SOURCES: p. ej. ["steam", "psn"]."""
        return [key for key in HOURS_SOURCES if getattr(self, f"{key}_playtime_minutes") is not None]

    @property
    def stars(self):
        """Nota en estrellas (de 0,5 a 5), o None sin nota."""
        return self.rating / 2 if self.rating else None


# Plataformas de las que se importan horas jugadas: clave del campo *_playtime_minutes → nombre.
HOURS_SOURCES = {"steam": "Steam", "psn": "PlayStation", "xbox": "Xbox"}
# Estado de la biblioteca al publicar una reseña → cómo se cuenta en ella (Pendiente no dice nada).
STATUS_NOTES = {
    LibraryEntry.Status.COMPLETED: "Lo terminó",
    LibraryEntry.Status.PLAYING: "Aún jugándolo",
    LibraryEntry.Status.PLAYED: "Lo jugó",
    LibraryEntry.Status.DROPPED: "Lo abandonó",
}


class ReviewQuerySet(models.QuerySet):
    def published(self):
        """Reseñas publicadas, sin los borradores."""
        return self.filter(is_draft=False)

    def visible_to(self, viewer):
        """Reseñas publicadas que viewer puede leer, de la más reciente a la más antigua, con sus «me gusta»
        (like_count) y comentarios (comment_count) contados.

        Las de perfiles privados solo las ve su autor, igual que su biblioteca.
        """
        visible = models.Q(user__profile__is_public=True)
        if viewer.is_authenticated:
            visible |= models.Q(user=viewer)
        return (
            self.published()
            .filter(visible)
            .select_related("user__profile", "user__psn_account", "game", "platform")
            .prefetch_related("user__steam_accounts")
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

    objects = ReviewQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-id"]
        indexes = [models.Index(fields=["game", "is_draft", "published_at"])]
        verbose_name = "reseña"
        verbose_name_plural = "reseñas"

    def __str__(self):
        """Texto en el admin: «usuario · juego»."""
        return f"{self.user} · {self.game}"

    def get_absolute_url(self):
        """URL de la página de la reseña: /u/<usuario>/resenas/<id>/."""
        return reverse("review_detail", args=[self.user.username, self.pk])

    @property
    def stars(self):
        """Nota en estrellas (de 0,5 a 5), o None sin nota."""
        return self.rating / 2 if self.rating else None

    @property
    def finished(self):
        """Indica si el autor lo tenía como «Completado» al publicarla."""
        return self.status == LibraryEntry.Status.COMPLETED

    @property
    def status_note(self):
        """Cómo iba el autor con el juego al publicarla, para la reseña: «Lo terminó», «Aún jugándolo»...; vacía si no se sabe."""
        return STATUS_NOTES.get(self.status, "")

    @property
    def hours_source_names(self):
        """Nombres de las plataformas de las que salen las horas: «Steam» o «Steam y PlayStation»."""
        names = [HOURS_SOURCES[key] for key in self.hours_sources.split(",") if key in HOURS_SOURCES]
        return " y ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else "".join(names)

    def snapshot(self, entry):
        """Apunta el estado y las horas que tiene ahora la entrada de biblioteca entry (o nada si no hay)."""
        self.status = entry.status if entry else ""
        hours = entry.playtime_hours if entry else None
        self.hours = None if hours is None else Decimal(str(hours))
        self.hours_sources = ",".join(entry.playtime_sources) if entry and hours is not None else ""


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
        """Texto en el admin: «usuario → reseña»."""
        return f"{self.user} → {self.review}"


class ReviewComment(models.Model):
    """Comentario en una reseña. Lo pueden borrar quien lo escribió y el autor de la reseña."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_comments")
    text = models.TextField("comentario", max_length=COMMENT_MAX_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "comentario"
        verbose_name_plural = "comentarios"

    def __str__(self):
        """Texto en el admin: «usuario en reseña: comienzo del comentario»."""
        return f"{self.user} en {self.review}: {self.text[:40]}"

    def can_delete(self, user):
        """Indica si user puede borrar el comentario: es suyo o es el autor de la reseña."""
        return user.is_authenticated and user.pk in (self.user_id, self.review.user_id)


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

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["user", "game"], name="unique_wishlist_item_per_user_game")]
        verbose_name = "juego de la lista de deseos"
        verbose_name_plural = "listas de deseos"

    def __str__(self):
        """Texto en el admin: «usuario · juego»."""
        return f"{self.user} · {self.game}"
