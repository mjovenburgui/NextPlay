from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.urls import reverse

from games.models import Game
from library.models import COMMENT_MAX_LENGTH

# Lo que cabe en una lista: cuántos juegos y lo largo que pueden ser el título, la descripción y el comentario de cada juego.
LIST_MAX_GAMES = 250
TITLE_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 2000
NOTE_MAX_LENGTH = 300
# Portadas del mosaico que hace de portada de cada lista (dos filas de dos).
MOSAIC_COVERS = 4


class GameListQuerySet(models.QuerySet):
    def visible_to(self, viewer):
        """Listas que viewer puede ver: las suyas y las públicas de perfiles públicos.

        Es la regla de GameList.visible_to como condición de consulta: tienen que decir lo mismo.
        """
        visible = models.Q(is_public=True, user__profile__is_public=True)
        if viewer.is_authenticated:
            visible |= models.Q(user=viewer)
        return self.filter(visible)

    def with_preview(self):
        """Listas con lo que enseñan sus tarjetas ya cargado: su autor (con su foto), cuántos juegos tienen
        (game_count), sus «me gusta» (like_count) y comentarios (comment_count) y los MOSAIC_COVERS primeros juegos
        para el mosaico (preview_items).

        Van de la más reciente a la más antigua: con los recuentos, Django ya no aplica el orden de Meta.
        """
        first = ListItem.objects.select_related("game").order_by("position", "id")[:MOSAIC_COVERS]
        return (
            self.select_related("user__profile", "user__psn_account", "user__xbox_account")
            .prefetch_related("user__steam_accounts", models.Prefetch("items", queryset=first, to_attr="preview_items"))
            .annotate(
                game_count=models.Count("items", distinct=True),
                like_count=models.Count("likes", distinct=True),
                comment_count=models.Count("comments", distinct=True),
            )
            .order_by("-updated_at", "-id")
        )


class GameList(models.Model):
    """Lista de juegos de un usuario («Mejores metroidvanias», «Para jugar en pareja»), con su descripción.

    Los juegos van en el orden que elija (ListItem.position), cada uno con su comentario si quiere. Si es
    numerada, cada juego lleva su puesto, para rankings. La ven los demás si es pública y su perfil también
    (visible_to); las privadas solo las ve su dueño. Los demás pueden darle «me gusta», comentarla y denunciarla,
    y sale en la actividad de su dueño (social.activity.list_created).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_lists")
    title = models.CharField("título", max_length=TITLE_MAX_LENGTH)
    # Con el formato sencillo de las reseñas (library.markup).
    description = models.TextField("descripción", max_length=DESCRIPTION_MAX_LENGTH, blank=True)
    is_public = models.BooleanField("visible para los demás", default=True)
    is_ranked = models.BooleanField("numerada", default=False)
    created_at = models.DateTimeField("creada", auto_now_add=True)
    # Cambia también al añadir, quitar, mover o comentar sus juegos (touch).
    updated_at = models.DateTimeField("actualizada", auto_now=True)
    # Sus denuncias se borran con ella.
    reports = GenericRelation("library.Report", related_query_name="game_list")

    objects = GameListQuerySet.as_manager()

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["user", "-updated_at"], name="gamelist_user_recent")]
        verbose_name = "lista"
        verbose_name_plural = "listas"

    def __str__(self):
        """Texto en el admin: «título (usuario)»."""
        return f"{self.title} ({self.user})"

    def get_absolute_url(self):
        """URL de la página de la lista: /u/<usuario>/listas/<id>/."""
        return reverse("lists:detail", args=[self.user.username, self.pk])

    def visible_to(self, viewer):
        """Indica si viewer puede ver la lista: siempre su dueño; los demás, si es pública y pueden ver su perfil.

        En las consultas la aplica GameListQuerySet.visible_to, que tiene que decir lo mismo.
        """
        return viewer == self.user or (self.is_public and self.user.profile.visible_to(viewer))

    @property
    def mosaic_gaps(self):
        """Huecos vacíos del mosaico cuando tiene menos de MOSAIC_COVERS juegos (para recorrerlos en la plantilla)."""
        return range(MOSAIC_COVERS - len(getattr(self, "preview_items", [])))

    def touch(self):
        """Da la lista por actualizada ahora (sin tocar nada más)."""
        self.save(update_fields=["updated_at"])

    def add(self, game):
        """Añade game al final de la lista; devuelve True si no estaba ya. No mira LIST_MAX_GAMES: eso lo hace quien la llama."""
        last = self.items.aggregate(last=models.Max("position"))["last"]
        _, created = ListItem.objects.get_or_create(
            game_list=self, game=game, defaults={"position": 0 if last is None else last + 1}
        )
        if created:
            self.touch()
        return created

    def remove(self, game):
        """Quita game de la lista y corre los de detrás para no dejar hueco; devuelve True si estaba."""
        deleted, _ = self.items.filter(game=game).delete()
        if deleted:
            self._save_order(list(self.items.order_by("position", "id")))
            self.touch()
        return bool(deleted)

    def move(self, game, position):
        """Lleva game al puesto position (desde 0; si se pasa, al principio o al final) y corre los demás.

        Devuelve True si game está en la lista.
        """
        items = list(self.items.order_by("position", "id"))
        current = next((item for item in items if item.game_id == game.pk), None)
        if current is None:
            return False
        items.remove(current)
        items.insert(max(0, min(position, len(items))), current)
        self._save_order(items)
        self.touch()
        return True

    @staticmethod
    def _save_order(items):
        """Guarda como puestos seguidos (0, 1, 2...) el orden de items, solo en los que cambian."""
        changed = []
        for position, item in enumerate(items):
            if item.position != position:
                item.position = position
                changed.append(item)
        ListItem.objects.bulk_update(changed, ["position"])


class ListItem(models.Model):
    """Un juego de una lista, en su puesto y con el comentario de quien la hace («por qué está aquí»), si lo tiene."""

    game_list = models.ForeignKey(GameList, on_delete=models.CASCADE, related_name="items", verbose_name="lista")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="list_items", verbose_name="juego")
    position = models.PositiveIntegerField("puesto", default=0)
    note = models.CharField("comentario", max_length=NOTE_MAX_LENGTH, blank=True)
    added_at = models.DateTimeField("añadido", auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["game_list", "game"], name="unique_game_per_list")]
        verbose_name = "juego de una lista"
        verbose_name_plural = "juegos de las listas"

    def __str__(self):
        """Texto en el admin: «lista · juego»."""
        return f"{self.game_list.title} · {self.game}"


class ListLike(models.Model):
    """«Me gusta» de un usuario en una lista de otro (uno por usuario y lista)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="list_likes")
    game_list = models.ForeignKey(GameList, on_delete=models.CASCADE, related_name="likes", verbose_name="lista")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "game_list"], name="unique_like_per_user_list")]
        verbose_name = "me gusta de una lista"
        verbose_name_plural = "me gusta de las listas"

    def __str__(self):
        """Texto en el admin: «usuario → lista»."""
        return f"{self.user} → {self.game_list}"


class ListComment(models.Model):
    """Comentario en una lista, como los de las reseñas y las publicaciones. Lo pueden borrar quien lo escribió y el
    dueño de la lista."""

    game_list = models.ForeignKey(GameList, on_delete=models.CASCADE, related_name="comments", verbose_name="lista")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="list_comments")
    text = models.TextField("comentario", max_length=COMMENT_MAX_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)
    # Sus denuncias se borran con él.
    reports = GenericRelation("library.Report", related_query_name="list_comment")

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "comentario de una lista"
        verbose_name_plural = "comentarios de las listas"

    def __str__(self):
        """Texto en el admin: «usuario en lista: comienzo del comentario»."""
        return f"{self.user} en {self.game_list.title}: {self.text[:40]}"

    def get_absolute_url(self):
        """URL del comentario dentro de la página de su lista."""
        return f"{self.game_list.get_absolute_url()}#comentario-{self.pk}"

    def can_delete(self, user):
        """Indica si user puede borrar el comentario: es suyo o es el dueño de la lista."""
        return user.is_authenticated and user.pk in (self.user_id, self.game_list.user_id)
