from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import formats, timezone

from games.models import Game, Genre, Platform

# Lo largo que pueden ser el título y la descripción de un reto, y lo más que se puede pedir en uno de juegos o de horas.
TITLE_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 2000
MAX_TARGET_GAMES = 500
MAX_TARGET_HOURS = 10000


def long_date(day):
    """Una fecha como se escribe en España: «1 de octubre de 2026»."""
    return formats.date_format(day, r"j \d\e F \d\e Y")


class ChallengeQuerySet(models.QuerySet):
    def visible_to(self, viewer):
        """Retos que viewer puede ver: los públicos y los suyos. Es la regla de Challenge.visible_to en una consulta."""
        visible = models.Q(is_public=True)
        if viewer.is_authenticated:
            visible |= models.Q(creator=viewer)
        return self.filter(visible)

    def with_counts(self):
        """Retos con su creador cargado y cuánta gente se ha unido (participant_count)."""
        return self.select_related("creator__profile").annotate(participant_count=models.Count("participations"))


class Challenge(models.Model):
    """Un reto que crea un jugador y al que se puede unir cualquiera: terminar unos juegos concretos, terminar un
    número de juegos (de un género, una plataforma o unos años, si se quiere) o jugar un número de horas, entre unas
    fechas o sin ellas.

    El progreso de cada uno se cuenta solo con su biblioteca (challenges.progress): los juegos que pasa a «Completado»
    entre las fechas (LibraryEntry.completed_at) o lo que juega en ellas (library.models.PlaySession). Sus reglas no
    cambian después de crearlo, para que sea justo con quien ya se ha unido. Los públicos los ve cualquiera; los
    privados, solo quien los crea (un objetivo propio, como «20 juegos en 2027»).
    """

    class Kind(models.TextChoices):
        GAMES = "games", "Terminar unos juegos concretos"
        COUNT = "count", "Terminar un número de juegos"
        HOURS = "hours", "Jugar un número de horas"

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_challenges", verbose_name="creador",
    )
    title = models.CharField("título", max_length=TITLE_MAX_LENGTH)
    # Con el formato sencillo de las reseñas (library.markup).
    description = models.TextField("descripción", max_length=DESCRIPTION_MAX_LENGTH, blank=True)
    kind = models.CharField("tipo", max_length=10, choices=Kind.choices)
    # Juegos que hay que terminar (GAMES; vacío, todos los de games) o que hay que terminar (COUNT) u horas (HOURS).
    target = models.PositiveIntegerField("objetivo", null=True, blank=True)
    # Los de un reto de juegos concretos: los que tenía la lista de la que sale (source_list) al crearlo.
    games = models.ManyToManyField(Game, blank=True, related_name="challenges", verbose_name="juegos")
    source_list = models.ForeignKey(
        "lists.GameList", on_delete=models.SET_NULL, null=True, blank=True, related_name="challenges",
        verbose_name="lista de la que sale",
    )
    # Qué juegos cuentan en los de número de juegos o de horas; vacío, cualquiera.
    genre = models.ForeignKey(
        Genre, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="género",
    )
    platform = models.ForeignKey(
        Platform, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="plataforma",
    )
    released_from = models.PositiveSmallIntegerField("lanzados desde el año", null=True, blank=True)
    released_to = models.PositiveSmallIntegerField("lanzados hasta el año", null=True, blank=True)
    # Cuenta lo de esos días, los dos incluidos; sin fechas, todo (sin principio, también lo de antes de unirse).
    starts_on = models.DateField("empieza", null=True, blank=True)
    ends_on = models.DateField("termina", null=True, blank=True)
    is_public = models.BooleanField("público", default=True)
    created_at = models.DateTimeField("creado", auto_now_add=True)

    objects = ChallengeQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "reto"
        verbose_name_plural = "retos"

    def __str__(self):
        """Texto en el admin: su título."""
        return self.title

    def get_absolute_url(self):
        """URL de la página del reto: /retos/<id>/."""
        return reverse("challenges:detail", args=[self.pk])

    def visible_to(self, viewer):
        """Indica si viewer puede ver el reto: los públicos, cualquiera; los privados, solo quien los crea."""
        return self.is_public or viewer == self.creator

    @property
    def goal(self):
        """Lo que hay que llegar a tener, en la unidad de challenges.progress: juegos terminados o minutos jugados."""
        if self.kind == self.Kind.HOURS:
            return (self.target or 0) * 60
        if self.kind == self.Kind.GAMES:
            return self.target or self.games.count()
        return self.target or 0

    @property
    def is_upcoming(self):
        """Indica si aún no ha empezado."""
        return self.starts_on is not None and self.starts_on > timezone.localdate()

    @property
    def is_over(self):
        """Indica si ya ha terminado: desde entonces no se puede unir nadie."""
        return self.ends_on is not None and self.ends_on < timezone.localdate()

    @property
    def days_left(self):
        """Días que le quedan si está en marcha y tiene fecha de fin (hoy incluido); si no, None."""
        if self.ends_on is None or self.is_over or self.is_upcoming:
            return None
        return (self.ends_on - timezone.localdate()).days + 1

    @property
    def filters_text(self):
        """De qué juegos va, en palabras: «de Rol de PC lanzados entre 2000 y 2010»; vacío si de cualquiera."""
        parts = []
        if self.genre_id:
            parts.append(f"de {self.genre.name}")
        if self.platform_id:
            parts.append(f"de {self.platform.short_name}")
        if self.released_from and self.released_to:
            years = f"entre {self.released_from} y {self.released_to}" if self.released_from != self.released_to else f"en {self.released_from}"
            parts.append(f"lanzados {years}")
        elif self.released_from:
            parts.append(f"lanzados desde {self.released_from}")
        elif self.released_to:
            parts.append(f"lanzados hasta {self.released_to}")
        return " ".join(parts)

    @property
    def rules_text(self):
        """Lo que hay que hacer, en una frase: «Termina 5 juegos de Terror», «Juega 50 horas», «Termina los 6 juegos»."""
        if self.kind == self.Kind.HOURS:
            what = f"Juega {self.target} hora{'s' if self.target != 1 else ''}"
            return f"{what} a juegos {self.filters_text}" if self.filters_text else what
        if self.kind == self.Kind.COUNT:
            what = f"Termina {self.target} juego{'s' if self.target != 1 else ''}"
            return f"{what} {self.filters_text}" if self.filters_text else what
        total = self.games.count()
        if self.target and self.target < total:
            return f"Termina {self.target} de estos {total} juegos"
        return f"Termina {'el juego' if total == 1 else f'los {total} juegos'}"

    @property
    def dates_text(self):
        """Cuándo cuenta, en palabras: «del 1 de octubre al 31 de octubre de 2026», «desde…», «hasta…» o «sin fechas»."""
        if self.starts_on and self.ends_on:
            return f"del {long_date(self.starts_on)} al {long_date(self.ends_on)}"
        if self.starts_on:
            return f"desde el {long_date(self.starts_on)}"
        if self.ends_on:
            return f"hasta el {long_date(self.ends_on)}"
        return "sin fechas"


class Participation(models.Model):
    """Un jugador que se ha unido a un reto. Su progreso se calcula con su biblioteca (challenges.progress); cuando
    llega al objetivo se apunta cuándo (completed_at), una vez."""

    challenge = models.ForeignKey(Challenge, on_delete=models.CASCADE, related_name="participations", verbose_name="reto")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="participations")
    joined_at = models.DateTimeField("se unió", auto_now_add=True)
    completed_at = models.DateTimeField("lo consiguió", null=True, blank=True)

    class Meta:
        ordering = ["joined_at", "id"]
        constraints = [models.UniqueConstraint(fields=["challenge", "user"], name="unique_participation")]
        verbose_name = "participación en un reto"
        verbose_name_plural = "participaciones en retos"

    def __str__(self):
        """Texto en el admin: «usuario en reto»."""
        return f"{self.user} en {self.challenge}"
