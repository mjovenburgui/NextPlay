"""Formularios de las listas: el de sus datos (GameListForm, en Editar lista), el de Nueva lista (NewListForm), el de
Nueva tier list (TierListForm) y el de comentar una lista (ListCommentForm, en la Página de la lista).
"""

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

    Y si está abierta a la comunidad (is_open, solo si es pública; una versión no se puede abrir). Si ya tiene
    versiones, o es una, sus niveles quedan fijos (tiers_lock): se pueden renombrar y cambiar de color, pero no quitar
    ni añadir, y no puede dejar de ser tier list.
    """

    # Campos que la plantilla pinta como interruptores.
    SWITCHES = ("is_public", "is_open")
    # Si «Por niveles» está entre los tipos que se eligen (en «Nueva lista» no: ver NewListForm).
    offers_tiers = True

    class Meta:
        model = GameList
        fields = ("title", "description", "is_public", "is_open", "mode")
        labels = {
            "title": "Título",
            "description": "Descripción",
            "is_public": "Visible para los demás",
            "is_open": "Abierta a la comunidad",
            "mode": "Tipo de lista",
        }
        help_texts = {
            "is_public": "Si la quitas, solo la ves tú y la gente a la que invites a editarla. Si tu perfil es privado, "
                         "tus listas también lo son.",
            "is_open": "Cualquiera puede hacer su versión con estos juegos y niveles, y su página enseña la tier list "
                       "que sale de juntarlas todas. Puedes dejar los juegos sin colocar para que la haga la comunidad.",
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

        Sin tipo en lo enviado, la lista se queda con el que tenía (una nueva, lista sin más). Con los niveles fijos
        (tiers_lock), los que tiene no se pueden dejar sin nombre, las filas sin usar no se pueden rellenar y el tipo no
        se puede cambiar. «Abierta a la comunidad» solo sale donde se hacen tier lists, y no en las versiones.

        Pantallas: Nueva lista (con los niveles de las tier lists), Nueva tier list, Editar lista (los datos de la
        lista).
        """
        super().__init__(*args, **kwargs)
        self.fields["mode"].required = False
        self.tiers_lock = self._tiers_lock()
        if not self.offers_tiers or self.instance.parent_id:
            del self.fields["is_open"]
        if self.tiers_lock:
            self.fields["mode"].disabled = True
        tiers = {tier["id"]: tier for tier in (self.instance.tiers or default_tiers())}
        colors = [color for _, color in DEFAULT_TIERS] + EXTRA_TIER_COLORS
        for n in range(MAX_TIERS):
            tier = tiers.get(n, {})
            # Con los niveles fijos, los que hay no se quitan y en las filas sin usar no se escribe.
            unused = bool(self.tiers_lock) and n not in tiers
            self.fields[f"nivel_{n}"] = forms.CharField(
                label=f"Nombre del nivel {n + 1}", max_length=TIER_LABEL_MAX_LENGTH,
                required=bool(self.tiers_lock) and n in tiers, disabled=unused, initial=tier.get("label", ""),
                error_messages={"required": "Este nivel no se puede quitar: déjale un nombre."},
                widget=forms.TextInput(attrs={"maxlength": TIER_LABEL_MAX_LENGTH, "placeholder": "Sin usar"}),
            )
            self.fields[f"color_{n}"] = forms.CharField(
                label=f"Color del nivel {n + 1}", required=False, initial=tier.get("color", colors[n]), disabled=unused,
                widget=forms.TextInput(attrs={"type": "color"}),
            )

    def _tiers_lock(self):
        """Por qué no se le pueden quitar ni añadir niveles («» si se puede): es una versión de otra tier list, o ya hay
        versiones de esta. Así lo que ha colocado cada uno sigue contando igual en la de la comunidad.

        Pantallas: Editar lista (los niveles de una tier list abierta con versiones y los de una versión).
        """
        if self.instance.parent_id:
            return "Es una versión de otra tier list: sus niveles son los de esa. Puedes cambiarles el nombre y el color."
        if self.instance.pk and self.instance.versions.exists():
            return (
                "Ya hay versiones de esta tier list: puedes cambiar el nombre y el color de sus niveles, pero no quitar "
                "ni añadir ninguno."
            )
        return ""

    @property
    def switches(self):
        """Los campos de sí o no (visible para los demás y, donde se hacen tier lists, abierta a la comunidad), que la
        plantilla pinta como interruptores.

        Pantallas: Nueva lista (interruptor de si la ven los demás), Nueva tier list, Editar lista (los datos de la
        lista).
        """
        return [self[name] for name in self.SWITCHES if name in self.fields]

    @property
    def tier_fields(self):
        """Las filas de los niveles para la plantilla: [(nombre, color)] de cada uno, de mejor a peor.

        Pantallas: Nueva lista (filas de los niveles), Nueva tier list, Editar lista (los datos de la lista).
        """
        return [(self[f"nivel_{n}"], self[f"color_{n}"]) for n in range(MAX_TIERS)]

    def clean_title(self):
        """Deja un solo espacio entre palabras (el campo ya quita los del principio y el final, y sin título da error).

        Pantallas: Nueva lista (el título), Nueva tier list, Editar lista (los datos de la lista).
        """
        return " ".join(self.cleaned_data["title"].split())

    def clean_description(self):
        """Quita los espacios del principio y del final.

        Pantallas: Nueva lista (la descripción), Nueva tier list, Editar lista (los datos de la lista).
        """
        return self.cleaned_data["description"].strip()

    def clean(self):
        """Arma los niveles con las filas que tienen nombre (cleaned_data["tiers"]); un color que no sea «#rrggbb»
        da error. Una tier list necesita al menos un nivel; las demás guardan los niveles por si vuelven a serlo.
        Abrirla a la comunidad pide que sea visible para los demás, y la que deja de ser tier list deja de estar abierta.

        Pantallas: Nueva lista (los niveles y sus colores), Nueva tier list, Editar lista (los datos de la lista).
        """
        data = super().clean()
        mode = data.get("mode") or self.instance.mode
        if data.get("is_open") and mode != GameList.Mode.TIERS:
            data["is_open"] = False
        elif data.get("is_open") and not data.get("is_public"):
            self.add_error("is_open", "Para abrirla a la comunidad tiene que ser visible para los demás.")
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
        """Guarda la lista con sus niveles.

        Pantallas: Nueva lista (al guardar), Nueva tier list, Editar lista (los datos de la lista),
        Página de la lista, Perfil (pestañas Listas y Tier lists y escaparate), Ficha del juego (listas en las que
        sale), Listas del juego, Inicio (feed).
        """
        game_list = super().save(commit=False)
        game_list.tiers = self.cleaned_data["tiers"]
        if commit:
            game_list.save()
        return game_list


class NewListForm(GameListForm):
    """GameListForm de «Nueva lista»: una lista sin más o numerada. Las tier lists se crean en su página (TierListForm),
    aunque una lista ya creada puede pasar a serlo en su editor."""

    offers_tiers = False

    def __init__(self, *args, **kwargs):
        """Quita «Por niveles» de los tipos.

        Pantallas: Nueva lista (los tipos que se pueden elegir).
        """
        super().__init__(*args, **kwargs)
        mode = self.fields["mode"]
        mode.choices = [(value, label) for value, label in mode.choices if value != GameList.Mode.TIERS]


class TierListForm(GameListForm):
    """GameListForm de «Nueva tier list»: el tipo va fijo y oculto, y sus niveles se ven siempre."""

    def __init__(self, *args, **kwargs):
        """Empieza con una tier list nueva (con los niveles de serie), oculta el tipo y pone ejemplos de tier list.

        Pantallas: Nueva tier list (niveles de serie y ejemplos).
        """
        kwargs.setdefault("instance", GameList(mode=GameList.Mode.TIERS))
        super().__init__(*args, **kwargs)
        self.fields["mode"].widget = forms.HiddenInput()
        self.fields["title"].widget.attrs["placeholder"] = "Los Zelda, Roguelikes, Lo que jugué este verano…"
        self.fields["description"].widget.attrs["placeholder"] = "Qué juegos entran o con qué criterio los colocas"

    def clean_mode(self):
        """Siempre tier list, aunque se mande otro tipo.

        Pantallas: Nueva tier list (siempre tier list).
        """
        return GameList.Mode.TIERS
