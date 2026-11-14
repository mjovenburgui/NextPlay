"""Formularios de lo social: el de publicar (PostForm, en la caja de Inicio y en Publicar) y el de comentar una
publicación (PostCommentForm, en la Página de la publicación).
"""

from django import forms
from django.core.exceptions import ValidationError

from accounts import images
from library.forms import CommentForm, MultipleImageField

from .models import POST_MAX_IMAGES, POST_MAX_LENGTH, Post, PostComment, PostImage


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
        if not data.get("text") and not uploads and not self.errors:
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

    def save(self, user):
        """Guarda la publicación de user con sus imágenes en el orden en que llegaron, cada una marcada como spoiler si
        su casilla iba marcada (spoiler_nueva), y la devuelve.

        Pantallas: Inicio (caja para publicar y feed), Publicar, Página de la publicación, Perfil (pestañas Actividad y
        Publicaciones), Ficha del juego (publicaciones), Guardados.
        """
        post = super().save(commit=False)
        post.user = user
        post.save()
        spoilers = set(self.cleaned_data.get("spoiler_nueva") or [])
        for position, (full, thumb) in enumerate(self.cleaned_data["processed"]):
            PostImage.objects.create(
                post=post, image=full, thumb=thumb, position=position, is_spoiler=str(position) in spoilers
            )
        return post
