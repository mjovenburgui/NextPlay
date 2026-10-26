from django import forms

from accounts.models import HEX_COLOR
from library.forms import CommentForm

from .models import (
    DEFAULT_TIERS, DESCRIPTION_MAX_LENGTH, MAX_TIERS, TIER_LABEL_MAX_LENGTH, TITLE_MAX_LENGTH, GameList, ListComment,
    default_tiers,
)

# Colores de los niveles que no están en DEFAULT_TIERS (los dos últimos de una tier list de MAX_TIERS niveles).
EXTRA_TIER_COLORS = ["#22e0f0", "#e6e8f5"]


class ListCommentForm(CommentForm):
    """Comentario en una lista: el mismo que el de las reseñas y las publicaciones (texto con su formato sencillo)."""

    class Meta(CommentForm.Meta):
        model = ListComment


class GameListForm(forms.ModelForm):
    """Datos de una lista: el título, la descripción (con el formato sencillo de las reseñas), si la ven los demás y su
    tipo: lista, numerada o tier list. Los juegos se añaden aparte, uno a uno (lists.views.add).

    Para las tier lists lleva también sus niveles: MAX_TIERS filas con nombre (nivel_<n>) y color (color_<n>), de
    mejor a peor. La fila n es el nivel con id n, así que renombrar uno no cambia los juegos que tiene; los que se dejan
    sin nombre se quitan y sus juegos quedan sin clasificar.
    """

    # Campos que la plantilla pinta como interruptores.
    SWITCHES = ("is_public",)

    class Meta:
        model = GameList
        fields = ("title", "description", "is_public", "mode")
        labels = {
            "title": "Título",
            "description": "Descripción",
            "is_public": "Visible para los demás",
            "mode": "Tipo de lista",
        }
        help_texts = {
            "is_public": "Si la quitas, solo la ves tú y la gente a la que invites a editarla. Si tu perfil es privado, "
                         "tus listas también lo son.",
        }
        error_messages = {"title": {"required": "Ponle un título a la lista."}}
        widgets = {
            "title": forms.TextInput(attrs={
                "maxlength": TITLE_MAX_LENGTH, "placeholder": "Mejores metroidvanias, Para jugar en pareja…",
            }),
            "description": forms.Textarea(attrs={
                "rows": 4, "maxlength": DESCRIPTION_MAX_LENGTH, "placeholder": "De qué va la lista o qué tienen en común sus juegos",
            }),
            "mode": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        """Añade las filas de los niveles, rellenas con los de la lista (o los de una tier list nueva).

        Sin tipo en lo enviado, la lista se queda con el que tenía (una nueva, lista sin más).
        """
        super().__init__(*args, **kwargs)
        self.fields["mode"].required = False
        tiers = {tier["id"]: tier for tier in (self.instance.tiers or default_tiers())}
        colors = [color for _, color in DEFAULT_TIERS] + EXTRA_TIER_COLORS
        for n in range(MAX_TIERS):
            tier = tiers.get(n, {})
            self.fields[f"nivel_{n}"] = forms.CharField(
                label=f"Nombre del nivel {n + 1}", max_length=TIER_LABEL_MAX_LENGTH, required=False,
                initial=tier.get("label", ""),
                widget=forms.TextInput(attrs={"maxlength": TIER_LABEL_MAX_LENGTH, "placeholder": "Sin usar"}),
            )
            self.fields[f"color_{n}"] = forms.CharField(
                label=f"Color del nivel {n + 1}", required=False, initial=tier.get("color", colors[n]),
                widget=forms.TextInput(attrs={"type": "color"}),
            )

    @property
    def switches(self):
        """Los campos de sí o no (visible para los demás), que la plantilla pinta como interruptores."""
        return [self[name] for name in self.SWITCHES]

    @property
    def tier_fields(self):
        """Las filas de los niveles para la plantilla: [(nombre, color)] de cada uno, de mejor a peor."""
        return [(self[f"nivel_{n}"], self[f"color_{n}"]) for n in range(MAX_TIERS)]

    def clean_title(self):
        """Deja un solo espacio entre palabras (el campo ya quita los del principio y el final, y sin título da error)."""
        return " ".join(self.cleaned_data["title"].split())

    def clean_description(self):
        """Quita los espacios del principio y del final."""
        return self.cleaned_data["description"].strip()

    def clean(self):
        """Arma los niveles con las filas que tienen nombre (cleaned_data["tiers"]); un color que no sea «#rrggbb»
        da error. Una tier list necesita al menos un nivel; las demás guardan los niveles por si vuelven a serlo."""
        data = super().clean()
        tiers = []
        for n in range(MAX_TIERS):
            label = " ".join(data.get(f"nivel_{n}", "").split())
            color = data.get(f"color_{n}", "").lower()
            if not label:
                continue
            if not HEX_COLOR.match(color):
                self.add_error(f"color_{n}", "Elige un color.")
                continue
            tiers.append({"id": n, "label": label, "color": color})
        if data.get("mode") == GameList.Mode.TIERS and not tiers and not self.errors:
            self.add_error("nivel_0", "Ponle nombre al menos a un nivel.")
        data["tiers"] = tiers or self.instance.tiers or default_tiers()
        return data

    def save(self, commit=True):
        """Guarda la lista con sus niveles."""
        game_list = super().save(commit=False)
        game_list.tiers = self.cleaned_data["tiers"]
        if commit:
            game_list.save()
        return game_list
