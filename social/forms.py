"""Formularios de lo social: el de publicar (PostForm, en la caja de Inicio y en Publicar), el de comentar una
publicación (PostCommentForm, en la Página de la publicación) y, en la comunidad de cada juego, el de abrir un hilo o
hacer una pregunta (ThreadForm) y el de responder en ellos (ReplyForm).
"""

from django import forms
from django.core.exceptions import ValidationError

from accounts import images
from accounts.models import Achievement
from library.forms import CommentForm, MultipleImageField
from library.models import COMMENT_MAX_LENGTH

from .models import POST_MAX_IMAGES, POST_MAX_LENGTH, POST_TITLE_MAX_LENGTH, Post, PostComment, PostImage


class PostCommentForm(CommentForm):
    """Comentario en una publicación: el mismo que el de las reseñas (texto con su formato sencillo, sin espacios de sobra)."""

    class Meta(CommentForm.Meta):
        model = PostComment


class PostForm(forms.ModelForm):
    """Publicación nueva: el texto (con el formato sencillo de las reseñas), el juego del que habla si quiere,
    si toda ella es un spoiler y hasta POST_MAX_IMAGES imágenes, que se pasan a WebP y sin EXIF con su miniatura
    (accounts.images) y que se pueden marcar como spoiler una a una (spoiler_nueva). Tiene que llevar texto o alguna
    imagen.
    """

    imagenes = MultipleImageField(label="Imágenes")
    # Posiciones (0, 1...) de las imágenes que son spoiler, en el orden en que llegan: las casillas de cada miniatura
    # de la caja de publicar. Las miniaturas las pinta el JavaScript, así que sin él solo queda el spoiler de toda ella.
    spoiler_nueva = forms.MultipleChoiceField(
        required=False, choices=[(str(i), str(i)) for i in range(POST_MAX_IMAGES)]
    )
    # Si tiene que llevar texto o alguna imagen: los hilos y las preguntas ya dicen algo con su título.
    requires_body = True

    class Meta:
        model = Post
        fields = ("text", "game", "has_spoilers")
        labels = {"text": "Tu publicación", "game": "Sobre qué juego"}
        widgets = {
            "text": forms.Textarea(attrs={
                "rows": 3, "maxlength": POST_MAX_LENGTH, "placeholder": "¿Qué estás jugando? Cuenta lo que quieras…",
            }),
        }

    def clean_text(self):
        """Quita los espacios del principio y del final.

        Pantallas: Inicio (caja para publicar), Publicar (el texto).
        """
        return self.cleaned_data["text"].strip()

    def clean(self):
        """Comprueba que lleve texto o imágenes y que no pasen de POST_MAX_IMAGES, y prepara las subidas.

        Las que no son una imagen válida, pesan demasiado o tienen demasiados píxeles dan un error con su nombre.

        Pantallas: Inicio (caja para publicar), Publicar (texto e imágenes).
        """
        data = super().clean()
        uploads = data.get("imagenes") or []
        if self.requires_body and not data.get("text") and not uploads and not self.errors:
            raise ValidationError("Escribe algo o añade alguna imagen.")
        if len(uploads) > POST_MAX_IMAGES:
            raise ValidationError(f"Caben {POST_MAX_IMAGES} imágenes por publicación: quita alguna.")
        data["processed"] = []
        for upload in uploads:
            try:
                data["processed"].append((images.process(upload, "post"), images.process(upload, "post_thumb")))
            except ValidationError as exc:
                self.add_error("imagenes", f"«{upload.name}»: {exc.messages[0]}")
        return data

    def prepare(self, post):
        """Lo que le falta a post antes de guardarla y que no llega en el formulario: en las publicaciones, nada (hasta
        su juego va en él).

        Pantallas: Inicio (caja para publicar), Publicar, Nuevo hilo o pregunta.
        """

    def save(self, user):
        """Guarda la publicación de user con cómo iba con su juego (Post.take_snapshot) y sus imágenes en el orden en
        que llegaron, cada una marcada como spoiler si su casilla iba marcada (spoiler_nueva), y la devuelve.

        Pantallas: Inicio (caja para publicar y feed), Publicar, Página de la publicación, Perfil (pestañas Actividad y
        Publicaciones), Ficha del juego (comunidad), Guardados, Nuevo hilo o pregunta, Comunidad del juego.
        """
        post = super().save(commit=False)
        post.user = user
        self.prepare(post)
        post.take_snapshot(user, post.game)
        post.save()
        spoilers = set(self.cleaned_data.get("spoiler_nueva") or [])
        for position, (full, thumb) in enumerate(self.cleaned_data["processed"]):
            PostImage.objects.create(
                post=post, image=full, thumb=thumb, position=position, is_spoiler=str(position) in spoilers
            )
        return post


class AchievementChoiceField(forms.ModelChoiceField):
    """Desplegable de logros con solo su nombre: «Capítulo 5», sin el juego ni la plataforma."""

    def label_from_instance(self, achievement):
        """El nombre del logro, que es lo que reconoce quien lo tiene.

        Pantallas: Nuevo hilo o pregunta («¿Hasta dónde destripa?»).
        """
        return achievement.name


class ThreadForm(PostForm):
    """Hilo o pregunta nuevos en la comunidad de un juego (game): el tipo, el título (que se ve siempre, así que no puede
    destripar nada), el texto si se quiere, imágenes como en las publicaciones, si todo es un spoiler y hasta qué logro
    destripa (spoiler_achievement): de los que tiene quien escribe (user) en ese juego, en el orden del juego. Las
    preguntas de «¿Cómo se consigue?» llevan su logro (achievement), de los del juego.
    """

    requires_body = False

    class Meta(PostForm.Meta):
        fields = ("kind", "title", "text", "has_spoilers", "spoiler_achievement", "achievement")
        field_classes = {"spoiler_achievement": AchievementChoiceField}
        labels = {
            "kind": "Qué es", "title": "Título", "text": "Cuéntalo", "spoiler_achievement": "¿Hasta dónde destripa?",
        }
        widgets = {
            "kind": forms.RadioSelect,
            "title": forms.TextInput(attrs={"maxlength": POST_TITLE_MAX_LENGTH, "autocomplete": "off"}),
            "text": forms.Textarea(attrs={
                "rows": 6, "maxlength": POST_MAX_LENGTH, "placeholder": "Cuenta más, si quieres: qué te pasa, dónde vas…",
            }),
            "achievement": forms.HiddenInput,
        }
        error_messages = {
            "title": {"required": "Ponle un título: es lo que se ve en la comunidad."},
            "achievement": {"invalid_choice": "Ese logro no es de este juego."},
            "spoiler_achievement": {"invalid_choice": "Elige uno de los logros que tienes de este juego."},
        }

    def __init__(self, *args, user, game, **kwargs):
        """Prepara el formulario para que user escriba en la comunidad de game: solo hilo o pregunta, el título
        obligatorio y los logros que puede elegir.

        Pantallas: Nuevo hilo o pregunta.
        """
        super().__init__(*args, **kwargs)
        self.game = game
        self.fields["kind"].choices = [(Post.Kind.THREAD, "Hilo"), (Post.Kind.QUESTION, "Pregunta")]
        self.fields["title"].required = True
        owned = self.fields["spoiler_achievement"]
        owned.queryset = (
            Achievement.objects.filter(unlocks__imported__user=user, title__game=game)
            .distinct().order_by("title__platform", "position", "pk")
        )
        owned.required = False
        owned.empty_label = "No destripa nada"
        self.fields["achievement"].queryset = Achievement.objects.filter(title__game=game)
        self.fields["achievement"].required = False

    def clean_title(self):
        """Quita los espacios del principio y del final del título.

        Pantallas: Nuevo hilo o pregunta (el título).
        """
        return self.cleaned_data["title"].strip()

    def prepare(self, post):
        """Los hilos y las preguntas son del juego de su comunidad.

        Pantallas: Nuevo hilo o pregunta, Comunidad del juego.
        """
        post.game = self.game


class ReplyForm(CommentForm):
    """Respuesta en un hilo o en una pregunta (post): el texto, con el formato sencillo de las reseñas, y, si contesta a
    otra respuesta del mismo hilo, cuál (reply_to, que pone el botón «Responder» de cada una).
    """

    class Meta(CommentForm.Meta):
        model = PostComment
        fields = ("text", "reply_to")
        labels = {"text": "Tu respuesta"}
        widgets = {
            "text": forms.Textarea(attrs={
                "rows": 3, "maxlength": COMMENT_MAX_LENGTH, "placeholder": "Escribe tu respuesta…",
            }),
            "reply_to": forms.HiddenInput,
        }
        error_messages = {
            "text": {"required": "Escribe algo en la respuesta."},
            "reply_to": {"invalid_choice": "La respuesta a la que contestabas ya no está."},
        }

    def __init__(self, *args, post, **kwargs):
        """Solo se puede contestar a las respuestas de post.

        Pantallas: Hilo o pregunta (responder).
        """
        super().__init__(*args, **kwargs)
        self.fields["reply_to"].queryset = post.comments.all()
        self.fields["reply_to"].required = False
