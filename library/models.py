from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from games.models import Game


class LibraryEntry(models.Model):
    class Status(models.TextChoices):
        PLAYING = "playing", "Jugando"
        COMPLETED = "completed", "Completado"
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
