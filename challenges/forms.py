from django import forms

from games.models import Genre, Platform
from lists.models import GameList

from .models import DESCRIPTION_MAX_LENGTH, MAX_TARGET_GAMES, MAX_TARGET_HOURS, TITLE_MAX_LENGTH, Challenge

# Años que se pueden pedir en el filtro de lanzamiento.
FIRST_YEAR, LAST_YEAR = 1950, 2100


class ChallengeEditForm(forms.ModelForm):
    """Lo que se puede cambiar de un reto ya creado: el título, la descripción (con el formato sencillo de las reseñas) y
    si es público. Sus reglas no, para que sea justo con quien ya se ha unido."""

    class Meta:
        model = Challenge
        fields = ("title", "description", "is_public")
        labels = {"title": "Título", "description": "Descripción", "is_public": "Público"}
        help_texts = {"is_public": "Cualquiera puede verlo y unirse. Si lo quitas, es un objetivo solo para ti."}
        error_messages = {"title": {"required": "Ponle un título al reto."}}
        widgets = {
            "title": forms.TextInput(attrs={
                "maxlength": TITLE_MAX_LENGTH, "placeholder": "Termina 5 juegos de terror en octubre, 20 juegos en 2027…",
            }),
            "description": forms.Textarea(attrs={
                "rows": 3, "maxlength": DESCRIPTION_MAX_LENGTH, "placeholder": "De qué va el reto (opcional)",
            }),
        }

    def clean_title(self):
        """Deja un solo espacio entre palabras (el campo ya quita los del principio y el final, y sin título da error)."""
        return " ".join(self.cleaned_data["title"].split())

    def clean_description(self):
        """Quita los espacios del principio y del final."""
        return self.cleaned_data["description"].strip()


class ChallengeForm(ChallengeEditForm):
    """Un reto nuevo: lo de ChallengeEditForm y sus reglas, según su tipo.

    De juegos concretos: los de una lista (source_list: una de las que puede editar quien lo crea o, desde «Convertir
    en reto», la que estaba mirando) y cuántos de ellos hay que terminar (target; vacío, todos). De número de juegos o
    de horas: cuántos (target) y, si se quiere, de qué género, plataforma y años. Para todos, las fechas, que son
    opcionales.
    """

    class Meta(ChallengeEditForm.Meta):
        fields = (
            "title", "description", "kind", "source_list", "target", "genre", "platform", "released_from",
            "released_to", "starts_on", "ends_on", "is_public",
        )
        labels = {
            **ChallengeEditForm.Meta.labels,
            "kind": "Tipo de reto",
            "source_list": "Juegos de la lista",
            "target": "Objetivo",
            "genre": "Género",
            "platform": "Plataforma",
            "released_from": "Lanzados desde",
            "released_to": "Lanzados hasta",
            "starts_on": "Empieza",
            "ends_on": "Termina",
        }
        widgets = {
            **ChallengeEditForm.Meta.widgets,
            "kind": forms.RadioSelect,
            "target": forms.NumberInput(attrs={"min": 1, "placeholder": "Cuántos"}),
            "released_from": forms.NumberInput(attrs={"min": FIRST_YEAR, "max": LAST_YEAR, "placeholder": "Año"}),
            "released_to": forms.NumberInput(attrs={"min": FIRST_YEAR, "max": LAST_YEAR, "placeholder": "Año"}),
            "starts_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "ends_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, user, game_list=None, **kwargs):
        """Prepara las listas que se pueden elegir (las que puede editar user y, si se da, game_list, desde «Convertir en
        reto») y los géneros y plataformas del catálogo."""
        super().__init__(*args, **kwargs)
        # Sin la opción vacía que Django añade a un campo con opciones y sin valor por defecto.
        self.fields["kind"].choices = Challenge.Kind.choices
        lists = GameList.objects.editable_by(user)
        if game_list is not None:
            lists = lists | GameList.objects.filter(pk=game_list.pk)
        self.fields["source_list"].queryset = lists.select_related("user__profile").order_by("title")
        self.fields["source_list"].empty_label = "Elige una lista"
        self.fields["source_list"].label_from_instance = lambda option: (
            option.title if option.user_id == user.pk else f"{option.title} (de {option.user.profile.name})"
        )
        self.fields["genre"].queryset = Genre.objects.filter(games__isnull=False).distinct()
        self.fields["genre"].empty_label = "Cualquiera"
        self.fields["platform"].queryset = Platform.objects.filter(games__isnull=False).distinct()
        self.fields["platform"].empty_label = "Cualquiera"

    def clean(self):
        """Comprueba lo que pide cada tipo y que las fechas y los años vayan en orden."""
        data = super().clean()
        kind, target, source = data.get("kind"), data.get("target"), data.get("source_list")
        if kind == Challenge.Kind.GAMES:
            total = source.items.count() if source else 0
            if source is None:
                self.add_error("source_list", "Elige la lista con los juegos del reto.")
            elif not total:
                self.add_error("source_list", "Esa lista aún no tiene juegos.")
            elif target is not None and not 1 <= target <= total:
                self.add_error("target", f"Tiene que ser entre 1 y {total}: los juegos de la lista.")
        elif kind in (Challenge.Kind.COUNT, Challenge.Kind.HOURS):
            limit = MAX_TARGET_GAMES if kind == Challenge.Kind.COUNT else MAX_TARGET_HOURS
            if target is None:
                self.add_error("target", "Di cuántos juegos hay que terminar." if kind == Challenge.Kind.COUNT else "Di cuántas horas hay que jugar.")
            elif not 1 <= target <= limit:
                self.add_error("target", f"Tiene que ser entre 1 y {limit}.")
        for field in ("released_from", "released_to"):
            year = data.get(field)
            if year is not None and not FIRST_YEAR <= year <= LAST_YEAR:
                self.add_error(field, f"Pon un año entre {FIRST_YEAR} y {LAST_YEAR}.")
        if data.get("released_from") and data.get("released_to") and data["released_from"] > data["released_to"]:
            self.add_error("released_to", "No puede ser antes del año de «desde».")
        if data.get("starts_on") and data.get("ends_on") and data["starts_on"] > data["ends_on"]:
            self.add_error("ends_on", "No puede terminar antes de empezar.")
        return data

    def save(self, user):
        """Guarda el reto de user. Uno de juegos concretos se queda con los que tiene su lista ahora (aunque la lista
        cambie después) y sin filtros; los demás, sin lista."""
        challenge = super().save(commit=False)
        challenge.creator = user
        if challenge.kind == Challenge.Kind.GAMES:
            challenge.genre = challenge.platform = challenge.released_from = challenge.released_to = None
        else:
            challenge.source_list = None
        challenge.save()
        if challenge.kind == Challenge.Kind.GAMES:
            challenge.games.set(self.cleaned_data["source_list"].items.values_list("game_id", flat=True))
        return challenge
