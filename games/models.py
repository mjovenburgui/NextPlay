from django.db import models
from django.urls import reverse


class Genre(models.Model):
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Platform(models.Model):
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=100)
    abbreviation = models.CharField(max_length=30, blank=True)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def short_name(self):
        return self.abbreviation or self.name


class Game(models.Model):
    # Null for games that did not come from IGDB (demo seed, manual entries).
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=255, unique=True)
    summary = models.TextField(blank=True)
    cover_image_id = models.CharField(max_length=64, blank=True)
    first_release_date = models.DateField(null=True, blank=True)
    # IGDB total_rating_count, used only as a popularity signal for ordering.
    popularity = models.PositiveIntegerField(default=0, db_index=True)
    genres = models.ManyToManyField(Genre, related_name="games", blank=True)
    platforms = models.ManyToManyField(Platform, related_name="games", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-popularity", "name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("games:detail", args=[self.slug])

    @property
    def cover_url(self):
        if not self.cover_image_id:
            return ""
        return f"https://images.igdb.com/igdb/image/upload/t_cover_big/{self.cover_image_id}.jpg"

    @property
    def release_year(self):
        return self.first_release_date.year if self.first_release_date else None
