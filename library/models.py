from django.conf import settings
from django.db import models

from games.models import Game


class LibraryEntry(models.Model):
    class Status(models.TextChoices):
        PLAYING = "playing", "Jugando"
        COMPLETED = "completed", "Completado"
        BACKLOG = "backlog", "Pendiente"
        DROPPED = "dropped", "Abandonado"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="library")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="library_entries")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BACKLOG)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="unique_library_entry_per_user_game"),
        ]
        indexes = [models.Index(fields=["user", "status"])]
        verbose_name_plural = "library entries"

    def __str__(self):
        return f"{self.user} · {self.game} · {self.get_status_display()}"
