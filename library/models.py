from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from games.models import Game

REVIEW_MAX_LENGTH = 3000
# Los juegos importados que se han jugado en estos últimos días entran como «Jugando».
IMPORT_RECENT_DAYS = 14


class LibraryEntryQuerySet(models.QuerySet):
    def reviews_visible_to(self, viewer):
        """Entradas con reseña que viewer puede leer, de la más reciente a la más antigua.

        Las de perfiles privados solo las ve su autor, igual que su biblioteca.
        """
        visible = models.Q(user__profile__is_public=True)
        if viewer.is_authenticated:
            visible |= models.Q(user=viewer)
        return (
            self.exclude(review="")
            .filter(visible)
            .select_related("user__profile", "user__psn_account", "game")
            .prefetch_related("user__steam_accounts")
            .order_by("-reviewed_at")
        )


class LibraryEntry(models.Model):
    class Status(models.TextChoices):
        PLAYING = "playing", "Jugando"
        COMPLETED = "completed", "Completado"
        # Jugado pero sin saber si se terminó: el estado que da la importación de Steam a los que no se juegan hace tiempo.
        PLAYED = "played", "Jugado"
        BACKLOG = "backlog", "Pendiente"
        DROPPED = "dropped", "Abandonado"

    MAX_RATING = 5
    # Highest first: the widget lays the stars out in reverse so CSS can light up the hovered one and those before it.
    RATING_STARS = range(MAX_RATING, 0, -1)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="library")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="library_entries")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BACKLOG)
    # The user's own score for the game, 1-5 stars; null until they rate it.
    rating = models.PositiveSmallIntegerField(
        "valoración",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    # Reseña escrita que se muestra en la ficha del juego y en el perfil; vacía hasta que la escribe.
    review = models.TextField("reseña", max_length=REVIEW_MAX_LENGTH, blank=True)
    review_has_spoilers = models.BooleanField("contiene spoilers", default=False)
    reviewed_at = models.DateTimeField(null=True, blank=True)
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

    objects = LibraryEntryQuerySet.as_manager()

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
