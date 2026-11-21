"""Modelos de lo social: quién sigue a quién, las publicaciones con sus imágenes, «me gusta», comentarios y quién se las
ha guardado (también los hilos y las preguntas de la comunidad de cada juego, que son publicaciones con título, y sus
respuestas), y la actividad de cada jugador (lo que hace con sus juegos, sus listas, sus importaciones y sus
publicaciones), que forma el feed de Inicio y la pestaña Actividad del Perfil.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from accounts import notices
from accounts.achievements import badge as achievement_badge
from accounts.achievements import progress_by_game
from accounts.models import Notification
from library.models import (
    COMMENT_MAX_LENGTH, HOURS_SOURCES, MAX_RATING, STATUS_NOTES, AttachedImage, LibraryEntry, played_snapshot,
    source_logos, source_names,
)

POST_MAX_LENGTH = 2000
# Título de un hilo o de una pregunta.
POST_TITLE_MAX_LENGTH = 120
# Imágenes que caben en una publicación.
POST_MAX_IMAGES = 4


class FollowManager(models.Manager):
    def follow(self, follower, followed):
        """Hace que follower siga a followed y avisa a followed en la campana; devuelve True si no lo seguía ya.

        El aviso llega solo la primera vez (accounts.notices): si follower deja de seguir a followed y vuelve, no se
        repite, y no llega si followed ha apagado los avisos de nuevos seguidores en Editar perfil. Lleva a
        la página de follower o, si followed no puede verla (perfil privado), a la lista de seguidores de
        followed, donde sale sin enlace. No comprueba si followed se puede seguir (eso lo mira social.views.follow).

        Pantallas: Perfil, Seguidores y seguidos, Inicio (botón «Seguir» de las sugerencias y feed «Siguiendo»),
        Avisos (aviso al seguido).
        """
        _, created = self.get_or_create(follower=follower, followed=followed)
        if created:
            profile = follower.profile
            if profile.visible_to(followed):
                url = profile.get_absolute_url()
            else:
                url = reverse("social:followers", args=[followed.username])
            notices.notify(
                followed, Notification.Kind.FOLLOW, f"{profile.name} ha empezado a seguirte.", url, actor=follower,
            )
        return created

    def unfollow(self, follower, followed):
        """Hace que follower deje de seguir a followed; devuelve True si lo seguía.

        Pantallas: Perfil, Seguidores y seguidos, Inicio (botón «Seguir» y feed «Siguiendo»).
        """
        deleted, _ = self.filter(follower=follower, followed=followed).delete()
        return bool(deleted)


class Follow(models.Model):
    """Un usuario (follower) sigue a otro (followed) para ver lo que hace.

    Va en un solo sentido y sin solicitudes: followed no tiene que aceptar ni seguir de vuelta. No se puede
    seguir dos veces a la misma persona ni seguirse a uno mismo (también lo impide la base de datos). Los
    perfiles privados no se pueden seguir, pero sus dueños sí pueden seguir a otros.
    """

    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="following", verbose_name="seguidor"
    )
    followed = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="followers", verbose_name="seguido"
    )
    created_at = models.DateTimeField("desde", auto_now_add=True)

    objects = FollowManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["follower", "followed"], name="unique_follow"),
            models.CheckConstraint(condition=~models.Q(follower=models.F("followed")), name="no_self_follow"),
        ]
        verbose_name = "seguimiento"
        verbose_name_plural = "seguimientos"

    def __str__(self):
        """Texto en el admin: «seguidor → seguido».

        Pantallas: Panel de administración.
        """
        return f"{self.follower} → {self.followed}"


class PlaySnapshot(models.Model):
    """Cómo iba quien escribe con el juego cuando lo escribió, como en las reseñas: su estado, sus horas verificadas y de
    dónde salen, y su insignia de logros («Platino» o «100 %»). Lo guardan las publicaciones (también los hilos y las
    preguntas) y sus respuestas, para enseñarlo junto a cada mensaje y plegar lo que va por delante de quien lo lee
    (social.community).
    """

    status = models.CharField("estado al escribirlo", max_length=20, choices=LibraryEntry.Status.choices, blank=True)
    hours = models.DecimalField("horas al escribirlo", max_digits=7, decimal_places=1, null=True, blank=True)
    hours_sources = models.CharField("de dónde salen las horas", max_length=30, blank=True)
    badge = models.CharField("logros al escribirlo", max_length=10, blank=True)

    class Meta:
        abstract = True

    def take_snapshot(self, user, game):
        """Apunta cómo va user con game ahora mismo: su estado y sus horas verificadas (library.models.played_snapshot) y
        su insignia de logros (accounts.achievements.badge). Sin juego se queda todo vacío; sin el juego en su biblioteca,
        sin estado ni horas, pero con la insignia si tiene sus logros importados.

        Pantallas: Publicar, Inicio (caja para publicar), Nuevo hilo o pregunta, Hilo o pregunta (al escribir se guarda
        cómo iba con el juego).
        """
        if game is None:
            self.status, self.hours, self.hours_sources, self.badge = "", None, "", ""
            return
        entry = LibraryEntry.objects.filter(user=user, game=game).first()
        self.status, self.hours, self.hours_sources = played_snapshot(entry)
        self.badge = achievement_badge(progress_by_game(user, [game.pk]).get(game.pk, []))

    @property
    def status_note(self):
        """Cómo iba su autor con el juego al escribirlo: «Lo terminó», «Aún jugándolo»…; vacía si no se sabe o lo tenía en
        Pendiente.

        Pantallas: Comunidad del juego, Hilo o pregunta, Ficha del juego (comunidad: cómo iba quien escribe).
        """
        return STATUS_NOTES.get(self.status, "")

    @property
    def finished(self):
        """Indica si su autor lo tenía como «Completado» al escribirlo.

        Pantallas: Comunidad del juego, Hilo o pregunta, Ficha del juego (comunidad: la marca de «Lo terminó»).
        """
        return self.status == LibraryEntry.Status.COMPLETED

    @property
    def hours_source_names(self):
        """Nombres de las plataformas de las que salen sus horas: «Steam» o «Steam y PlayStation».

        Pantallas: Comunidad del juego, Hilo o pregunta, Ficha del juego (comunidad: de dónde salen las horas).
        """
        return source_names(self.hours_sources)

    @property
    def hours_source_logos(self):
        """Plataformas de las que salen sus horas como [(nombre, plantilla de su logo)], para el sello de horas
        verificadas.

        Pantallas: Comunidad del juego, Hilo o pregunta, Ficha del juego (comunidad: sello de horas verificadas).
        """
        return source_logos(self.hours_sources)

    @property
    def has_snapshot(self):
        """Indica si hay algo que contar de cómo iba su autor: su estado, sus horas o su insignia de logros.

        Pantallas: Comunidad del juego, Hilo o pregunta, Ficha del juego (comunidad: cómo iba quien escribe).
        """
        return bool(self.status_note or self.hours is not None or self.badge)


class Post(PlaySnapshot):
    """Publicación de un jugador: lo que quiera contar, con hasta POST_MAX_IMAGES imágenes y, si quiere, sobre un juego.

    Sale en el feed de la portada y en la actividad de su perfil (social.activity.post_published) y tiene su propia
    página. La ve quien puede ver su perfil (Profile.visible_filter). El texto lleva el mismo formato sencillo que
    las reseñas (library.markup) y, si es un spoiler, el texto y las imágenes van tapados hasta que se pulsan.

    Hay tres tipos (kind): la publicación de siempre, un hilo, para hablar de algo con la comunidad del juego, y una
    pregunta, en la que quien pregunta puede elegir la respuesta que le ha servido (answer), que sale la primera. Los
    hilos y las preguntas llevan título, son siempre de un juego y viven en su Comunidad (social.community), ordenados
    por su última actividad (last_activity_at, que sube con cada respuesta); un administrador puede fijarlos arriba
    (pinned) o cerrarlos a más respuestas (closed). Todas guardan cómo iba su autor con el juego (PlaySnapshot) y él
    puede decir hasta qué logro destripa (spoiler_achievement): se pliega a quien aún no lo tiene. Las preguntas de
    «¿Cómo se consigue?», desde «Tus logros», van sobre un logro (achievement).
    """

    class Kind(models.TextChoices):
        POST = "post", "Publicación"
        THREAD = "thread", "Hilo"
        QUESTION = "question", "Pregunta"

    # Lo que hizo, en minúscula, para el feed: «Ana abrió un hilo sobre Hades».
    VERBS = {Kind.POST: "publicó", Kind.THREAD: "abrió un hilo", Kind.QUESTION: "preguntó"}

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts")
    kind = models.CharField("tipo", max_length=10, choices=Kind.choices, default=Kind.POST)
    # Los hilos y las preguntas lo llevan siempre (lo pide social.forms.ThreadForm); las publicaciones, no.
    title = models.CharField("título", max_length=POST_TITLE_MAX_LENGTH, blank=True)
    # Puede ir sin texto si lleva imágenes (lo comprueba social.forms.PostForm) o si es un hilo o una pregunta.
    text = models.TextField("texto", max_length=POST_MAX_LENGTH, blank=True)
    game = models.ForeignKey(
        "games.Game", on_delete=models.SET_NULL, null=True, blank=True, related_name="posts", verbose_name="juego"
    )
    has_spoilers = models.BooleanField("es un spoiler", default=False)
    # Hasta qué logro destripa, si lo dice su autor (de los que él tiene): se pliega a quien aún no lo tiene.
    spoiler_achievement = models.ForeignKey(
        "accounts.Achievement", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="destripa hasta el logro",
    )
    # El logro por el que pregunta, en las preguntas de «¿Cómo se consigue?».
    achievement = models.ForeignKey(
        "accounts.Achievement", on_delete=models.SET_NULL, null=True, blank=True, related_name="questions",
        verbose_name="pregunta por el logro",
    )
    # La respuesta que le ha servido a quien pregunta (solo en las preguntas). Si se borra, vuelve a estar sin resolver.
    answer = models.ForeignKey(
        "PostComment", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="respuesta que le ha servido",
    )
    pinned = models.BooleanField("fijado arriba", default=False)
    closed = models.BooleanField("cerrado a respuestas", default=False)
    created_at = models.DateTimeField("publicada", auto_now_add=True)
    # La de su última respuesta (o la de publicarla): la comunidad de cada juego se ordena por ella.
    last_activity_at = models.DateTimeField("última actividad", default=timezone.now)
    # Sus denuncias se borran con ella.
    reports = GenericRelation("library.Report", related_query_name="post")

    class Meta:
        ordering = ["-created_at", "-id"]
        # La comunidad de cada juego, de lo que tiene actividad más reciente a lo que menos.
        indexes = [models.Index(fields=["game", "-last_activity_at", "-id"], name="post_game_activity")]
        verbose_name = "publicación"
        verbose_name_plural = "publicaciones"

    def __str__(self):
        """Texto en el admin: «usuario: título o comienzo del texto».

        Pantallas: Panel de administración.
        """
        return f"{self.user}: {self.title or self.text[:40] or '(solo imágenes)'}"

    def get_absolute_url(self):
        """URL de su página: la de un hilo o una pregunta va en la comunidad de su juego (/juegos/<juego>/comunidad/<id>/);
        la de una publicación, en el perfil de su autor (/u/<usuario>/publicaciones/<id>/), igual que la de un hilo cuyo
        juego ya no existe.

        Pantallas: Inicio (feed), Perfil (pestaña Actividad), Página de la publicación, Hilo o pregunta, Comunidad del
        juego, Avisos (los enlaces a cada publicación).
        """
        if self.is_thread and self.game_id:
            return reverse("social:thread", args=[self.game.slug, self.pk])
        return reverse("social:post", args=[self.user.username, self.pk])

    @property
    def is_thread(self):
        """Indica si es un hilo o una pregunta (con título y en la comunidad de su juego) y no una publicación.

        Pantallas: Inicio (feed), Perfil (pestañas Actividad y Publicaciones), Guardados, Comunidad del juego, Ficha del
        juego (comunidad: cómo sale cada cosa), Hilo o pregunta.
        """
        return self.kind in (self.Kind.THREAD, self.Kind.QUESTION)

    @property
    def is_question(self):
        """Indica si es una pregunta, en la que quien pregunta elige la respuesta que le ha servido.

        Pantallas: Comunidad del juego, Hilo o pregunta («Me ha servido»), Ficha del juego (comunidad).
        """
        return self.kind == self.Kind.QUESTION

    @property
    def verb(self):
        """Lo que hizo su autor, en minúscula: «publicó», «abrió un hilo» o «preguntó».

        Pantallas: Inicio (feed), Perfil (pestañas Actividad y Publicaciones), Guardados, Comunidad del juego, Ficha del
        juego (comunidad: qué hizo cada uno).
        """
        return self.VERBS.get(self.kind, "publicó")


class PostImage(AttachedImage):
    """Imagen de una publicación (como mucho POST_MAX_IMAGES). Se guardan en publicaciones/<id del autor>/."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="images")

    class Meta(AttachedImage.Meta):
        verbose_name = "imagen de una publicación"
        verbose_name_plural = "imágenes de las publicaciones"

    def __str__(self):
        """Texto en el admin: «publicación · imagen n».

        Pantallas: Panel de administración.
        """
        return f"{self.post} · imagen {self.position + 1}"

    def upload_folder(self):
        """Las imágenes de las publicaciones van en publicaciones/<id del autor>.

        Pantallas: Inicio (caja para publicar), Publicar (carpeta de las imágenes de las publicaciones).
        """
        return f"publicaciones/{self.post.user_id}"


class PostLike(models.Model):
    """«Me gusta» de un usuario en una publicación (uno por usuario y publicación)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_likes")
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "post"], name="unique_like_per_user_post")]
        verbose_name = "me gusta de una publicación"
        verbose_name_plural = "me gusta de las publicaciones"

    def __str__(self):
        """Texto en el admin: «usuario → publicación».

        Pantallas: Panel de administración.
        """
        return f"{self.user} → {self.post}"


class PostComment(PlaySnapshot):
    """Comentario en una publicación, como los de las reseñas; en un hilo o en una pregunta, una respuesta, que puede
    contestar a otra del mismo hilo (reply_to): sale con su cita, sin anidarse. Guarda cómo iba su autor con el juego al
    escribirlo (PlaySnapshot). Lo pueden borrar quien lo escribió y el autor de la publicación.
    """

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_comments")
    text = models.TextField("comentario", max_length=COMMENT_MAX_LENGTH)
    # La respuesta del mismo hilo a la que contesta; si se borra, esta se queda sin cita.
    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies", verbose_name="en respuesta a"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Sus denuncias se borran con él.
    reports = GenericRelation("library.Report", related_query_name="post_comment")

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "comentario de una publicación"
        verbose_name_plural = "comentarios de las publicaciones"

    def __str__(self):
        """Texto en el admin: «usuario en publicación: comienzo del comentario».

        Pantallas: Panel de administración.
        """
        return f"{self.user} en {self.post}: {self.text[:40]}"

    def get_absolute_url(self):
        """URL del comentario dentro de la página de su publicación o, en un hilo o una pregunta, de la respuesta.

        Pantallas: Avisos (enlace a un comentario de una publicación o a una respuesta), Hilo o pregunta.
        """
        anchor = "respuesta" if self.post.is_thread else "comentario"
        return f"{self.post.get_absolute_url()}#{anchor}-{self.pk}"

    def can_delete(self, user):
        """Indica si user puede borrar el comentario: es suyo o es el autor de la publicación.

        Pantallas: Página de la publicación (botón «Borrar» de los comentarios).
        """
        return user.is_authenticated and user.pk in (self.user_id, self.post.user_id)


class SavedPost(models.Model):
    """Publicación que un usuario se ha guardado para volver a ella, una vez por usuario y publicación. Solo la ve él, en
    Guardados, y su autor no se entera. Se borra con la publicación; si su autor hace privado su perfil, deja de salir.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_posts")
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="saves")
    created_at = models.DateTimeField("guardada", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["user", "post"], name="unique_saved_post")]
        verbose_name = "publicación guardada"
        verbose_name_plural = "publicaciones guardadas"

    def __str__(self):
        """Texto en el admin: «usuario guardó publicación».

        Pantallas: Panel de administración.
        """
        return f"{self.user} guardó {self.post}"


class Activity(models.Model):
    """Algo que ha hecho un jugador: sale en la pestaña Actividad de su perfil y en el feed de la portada.

    La apunta social.activity desde lo que hace el jugador, nunca con señales: las importaciones también
    guardan juegos y les cambian el estado, y eso no lo ha hecho él. Hay tres tipos:

    - GAME: lo que hace con un juego de su biblioteca. Lo que hace con el mismo juego en poco rato se junta en
      una sola (ver social.activity.MERGE_WINDOW): el último estado al que lo ha pasado (status), la nota que le
      ha puesto (rating) y la reseña que ha publicado (review), las tres o solo alguna.
    - IMPORT: los juegos nuevos de una importación de Steam, PlayStation o Xbox, todos juntos. En data van la
      plataforma («platform», clave de HOURS_SOURCES), cuántos son («count») y los ids de los más jugados
      («games»), para enseñar sus portadas.
    - POST: una publicación (post), con su juego si lo tiene. Se borra con ella.
    - LIST: una lista (game_list): que la ha creado («created» en data) y cuántos juegos le ha añadido («added»),
      juntos si van seguidos (MERGE_WINDOW). Se borra con ella; si es privada, solo la ve su dueño.
    - CHALLENGE: un reto (challenge): que se ha unido a él, que lo ha creado («created» en data) o que lo ha
      conseguido («won»), juntos si van seguidos (MERGE_WINDOW). Se borra con el reto o si lo deja; si es privado,
      solo la ve su creador.
    - TIDY: lo que ha ordenado al repasar su biblioteca (library.backlog): cuántos juegos ha marcado como terminados
      («completed» en data) y como abandonados («dropped») y los ids de los primeros terminados («games»), para sus
      portadas; todo junto si va seguido (MERGE_WINDOW), y no una entrada por juego.
    - TROPHY: logros de un juego (game) que merecen salir, recién leídos de Steam o PlayStation
      (social.activity.achievements_read): en data, la plataforma («platform»), si ya los tiene todos («complete») o el
      platino («platinum»), cuántos ha conseguido («count») y los rarísimos («rare», con «name», «percent» y «hidden»,
      y cuántos son en «rare_count»).

    updated_at es la hora de lo último que se ha juntado: la que se enseña y por la que se ordena.
    """

    class Kind(models.TextChoices):
        GAME = "game", "Juego de la biblioteca"
        IMPORT = "import", "Importación"
        POST = "post", "Publicación"
        LIST = "list", "Lista"
        CHALLENGE = "challenge", "Reto"
        TIDY = "tidy", "Repaso de la biblioteca"
        TROPHY = "trophy", "Logros"

    # Estado al que pasa el juego → lo que dice la actividad, en minúscula y partido en lo que va antes del
    # nombre del juego y lo que va después: «terminó Hades», «añadió Hades a Pendiente».
    STATUS_VERBS = {
        LibraryEntry.Status.PLAYING: ("empezó a jugar a", ""),
        LibraryEntry.Status.COMPLETED: ("terminó", ""),
        LibraryEntry.Status.PLAYED: ("jugó a", ""),
        LibraryEntry.Status.BACKLOG: ("añadió", "a Pendiente"),
        LibraryEntry.Status.DROPPED: ("abandonó", ""),
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="activities")
    kind = models.CharField("tipo", max_length=10, choices=Kind.choices)
    game = models.ForeignKey(
        "games.Game", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="juego"
    )
    status = models.CharField("estado", max_length=20, choices=LibraryEntry.Status.choices, blank=True)
    rating = models.PositiveSmallIntegerField(
        "nota", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(MAX_RATING)]
    )
    # Si se borra la reseña se queda lo demás; si ya no queda nada, social.signals borra la actividad.
    review = models.ForeignKey(
        "library.Review", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="reseña"
    )
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="publicación"
    )
    game_list = models.ForeignKey(
        "lists.GameList", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="lista"
    )
    challenge = models.ForeignKey(
        "challenges.Challenge", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="reto"
    )
    data = models.JSONField("datos", default=dict, blank=True)
    created_at = models.DateTimeField("primera acción", default=timezone.now)
    updated_at = models.DateTimeField("última acción", default=timezone.now)

    class Meta:
        ordering = ["-updated_at", "-id"]
        # La actividad de cada uno (su pestaña y Siguiendo) y la de todos (Descubrir), de lo más nuevo a lo más viejo.
        indexes = [
            models.Index(fields=["user", "-updated_at", "-id"], name="activity_user_recent"),
            models.Index(fields=["-updated_at", "-id"], name="activity_recent"),
        ]
        verbose_name = "actividad"
        verbose_name_plural = "actividad"

    def __str__(self):
        """Texto en el admin: «usuario · lo que hizo».

        Pantallas: Panel de administración.
        """
        return f"{self.user} · {self.summary}"

    @property
    def is_empty(self):
        """Indica si una actividad de un juego se ha quedado sin nada (sin estado, nota ni reseña) y sobra.

        Pantallas: Inicio (feed: se borran las actividades que se quedan vacías), Perfil (pestaña Actividad).
        """
        return self.kind == self.Kind.GAME and not self.status and self.rating is None and self.review_id is None

    @property
    def has_content(self):
        """Indica si hay algo que enseñar de ella a quien la mira: los juegos de una importación, la publicación, la
        lista (si puede verla y tiene juegos) o, en las de un juego, el estado, la nota o la reseña (si su reseña ya no
        se puede leer, puede que nada).

        Pantallas: Inicio (feed: lo que se enseña de cada actividad), Perfil (pestaña Actividad).
        """
        if self.kind == self.Kind.IMPORT:
            return bool(self.import_count)
        if self.kind == self.Kind.POST:
            return self.post is not None
        if self.kind == self.Kind.CHALLENGE:
            return self.challenge is not None
        if self.kind == self.Kind.TIDY:
            return bool(self.data.get("completed") or self.data.get("dropped"))
        if self.kind == self.Kind.TROPHY:
            return self.game_id is not None
        if self.kind == self.Kind.LIST:
            game_list = self.game_list
            if game_list is None:
                return False
            count = getattr(game_list, "game_count", None)
            return game_list.items.exists() if count is None else count > 0
        return bool(self.status or self.rating or self.review)

    @property
    def list_created(self):
        """Indica si una actividad de una lista cuenta que la ha creado (si no, solo que le ha añadido juegos).

        Pantallas: Inicio (feed: actividades de listas), Perfil (pestaña Actividad).
        """
        return bool(self.data.get("created"))

    @property
    def list_added(self):
        """Cuántos juegos ha añadido a la lista en esta actividad.

        Pantallas: Inicio (feed: actividades de listas), Perfil (pestaña Actividad).
        """
        return self.data.get("added", 0)

    @property
    def challenge_verb(self):
        """Qué hizo con el reto, en minúscula y con su artículo: «consiguió el reto», «creó el reto» o «se unió al
        reto». Conseguirlo manda sobre lo demás si van juntos.

        Pantallas: Inicio (feed: actividades de retos), Perfil (pestaña Actividad).
        """
        if self.data.get("won"):
            return "consiguió el reto"
        if self.data.get("created"):
            return "creó el reto"
        return "se unió al reto"

    @property
    def trophy_verb(self):
        """Qué consiguió en el juego, en minúscula y con lo que va antes de su nombre: «consiguió el platino de»,
        «consiguió todos los logros de», «consiguió un logro rarísimo en» o «consiguió 3 logros rarísimos en» (trofeos
        en PlayStation).

        Pantallas: Inicio (feed: logros), Perfil (pestaña Actividad).
        """
        noun, one = ("trofeos", "trofeo") if self.data.get("platform") == "psn" else ("logros", "logro")
        if self.data.get("platinum"):
            return "consiguió el platino de"
        if self.data.get("complete"):
            return f"consiguió todos los {noun} de"
        count = self.data.get("rare_count", 0)
        return f"consiguió un {one} rarísimo en" if count == 1 else f"consiguió {count} {noun} rarísimos en"

    @property
    def verb(self):
        """Qué hizo con el juego, en minúscula: (lo que va antes del nombre del juego, lo que va después).

        Con un estado, lo de STATUS_VERBS; si no, «reseñó» si publicó una reseña que se puede leer, o «valoró».

        Pantallas: Inicio (feed: qué hizo con el juego), Perfil (pestaña Actividad).
        """
        if self.status in self.STATUS_VERBS:
            return self.STATUS_VERBS[self.status]
        return ("reseñó", "") if self.review else ("valoró", "")

    @property
    def shown_rating(self):
        """Nota que se enseña: la que le puso o, si no, la de su reseña (si la reseña se puede leer); o None.

        Pantallas: Inicio (feed: la nota que se enseña), Perfil (pestaña Actividad).
        """
        if self.rating:
            return self.rating
        review = self.review
        return review.rating if review else None

    @property
    def platform_name(self):
        """Nombre de la plataforma de una importación («Steam», «PlayStation» o «Xbox»).

        Pantallas: Inicio (feed: actividades de importación), Perfil (pestaña Actividad).
        """
        return HOURS_SOURCES.get(self.data.get("platform"), "")

    @property
    def import_count(self):
        """Cuántos juegos nuevos trajo una importación.

        Pantallas: Inicio (feed: actividades de importación), Perfil (pestaña Actividad).
        """
        return self.data.get("count", 0)

    @property
    def summary(self):
        """Lo que hizo, en una línea y sin formato, para el admin: «terminó Hades», «importó 12 juegos de Steam»,
        «publicó» o «creó la lista Indies».

        Pantallas: Panel de administración (lo que hizo cada actividad, en una línea).
        """
        if self.kind == self.Kind.IMPORT:
            return f"importó {self.import_count} juegos de {self.platform_name}"
        if self.kind == self.Kind.POST:
            return f"publicó sobre {self.game}" if self.game_id else "publicó"
        if self.kind == self.Kind.LIST:
            title = self.game_list.title if self.game_list else "(borrada)"
            return f"creó la lista {title}" if self.list_created else f"añadió {self.list_added} juegos a la lista {title}"
        if self.kind == self.Kind.CHALLENGE:
            return f"{self.challenge_verb} {self.challenge.title if self.challenge else '(borrado)'}"
        if self.kind == self.Kind.TIDY:
            return (
                f"ordenó su biblioteca: {self.data.get('completed', 0)} terminados y "
                f"{self.data.get('dropped', 0)} abandonados"
            )
        if self.kind == self.Kind.TROPHY:
            return f"{self.trophy_verb} {self.game}"
        before, after = self.verb
        return " ".join(part for part in (before, str(self.game), after) if part)
