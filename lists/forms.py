from django import forms

from library.forms import CommentForm

from .models import DESCRIPTION_MAX_LENGTH, TITLE_MAX_LENGTH, GameList, ListComment


class ListCommentForm(CommentForm):
    """Comentario en una lista: el mismo que el de las reseñas y las publicaciones (texto con su formato sencillo)."""

    class Meta(CommentForm.Meta):
        model = ListComment


class GameListForm(forms.ModelForm):
    """Datos de una lista: el título, la descripción (con el formato sencillo de las reseñas), si la ven los demás y
    si va numerada. Los juegos se añaden aparte, uno a uno (lists.views.add)."""

    # Campos que la plantilla pinta como interruptores.
    SWITCHES = ("is_public", "is_ranked")

    class Meta:
        model = GameList
        fields = ("title", "description", "is_public", "is_ranked")
        labels = {
            "title": "Título",
            "description": "Descripción",
            "is_public": "Visible para los demás",
            "is_ranked": "Lista numerada",
        }
        help_texts = {
            "is_public": "Si la quitas, solo la ves tú. Si tu perfil es privado, tus listas también lo son.",
            "is_ranked": "Cada juego lleva su puesto (1, 2, 3…), para rankings como «Mis 10 favoritos».",
        }
        error_messages = {"title": {"required": "Ponle un título a la lista."}}
        widgets = {
            "title": forms.TextInput(attrs={
                "maxlength": TITLE_MAX_LENGTH, "placeholder": "Mejores metroidvanias, Para jugar en pareja…",
            }),
            "description": forms.Textarea(attrs={
                "rows": 4, "maxlength": DESCRIPTION_MAX_LENGTH, "placeholder": "De qué va la lista o qué tienen en común sus juegos",
            }),
        }

    @property
    def switches(self):
        """Los campos de sí o no (visible y numerada), que la plantilla pinta como interruptores."""
        return [self[name] for name in self.SWITCHES]

    def clean_title(self):
        """Deja un solo espacio entre palabras (el campo ya quita los del principio y el final, y sin título da error)."""
        return " ".join(self.cleaned_data["title"].split())

    def clean_description(self):
        """Quita los espacios del principio y del final."""
        return self.cleaned_data["description"].strip()
