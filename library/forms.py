from django import forms
from django.utils import timezone

from .models import (
    COMMENT_MAX_LENGTH, MAX_POINTS, MAX_RATING, POINT_MAX_LENGTH, REVIEW_MAX_LENGTH, Review, ReviewComment,
)


def stars_label(value):
    """Texto de una nota de 1 a 10 en estrellas, como se lee en España: «3,5 estrellas», «1 estrella»."""
    stars = value / 2
    number = f"{stars:g}".replace(".", ",")
    return f"{number} estrella" if stars == 1 else f"{number} estrellas"


class PointsWidget(forms.MultiWidget):
    """MAX_POINTS casillas de texto para los puntos de «Lo mejor» o «Lo peor» (se llaman <campo>_0, <campo>_1...)."""

    def __init__(self, attrs=None):
        """Una casilla por punto, con el largo máximo de un punto."""
        super().__init__([forms.TextInput(attrs={"maxlength": POINT_MAX_LENGTH}) for _ in range(MAX_POINTS)], attrs)

    def decompress(self, value):
        """La lista guardada repartida en las casillas; las que sobran, vacías."""
        value = list(value or [])[:MAX_POINTS]
        return value + [""] * (MAX_POINTS - len(value))


class PointsField(forms.MultiValueField):
    """Lista de puntos de «Lo mejor» o «Lo peor»: hasta MAX_POINTS frases de hasta POINT_MAX_LENGTH caracteres."""

    widget = PointsWidget

    def __init__(self, **kwargs):
        """Un CharField opcional por casilla; la lista entera también es opcional."""
        fields = [forms.CharField(max_length=POINT_MAX_LENGTH, required=False) for _ in range(MAX_POINTS)]
        super().__init__(fields, require_all_fields=False, required=False, **kwargs)

    def compress(self, data_list):
        """Los puntos escritos, en orden, sin espacios de sobra, vacíos ni repetidos."""
        points, seen = [], set()
        for point in data_list or []:
            point = " ".join((point or "").split())
            if point and point.casefold() not in seen:
                seen.add(point.casefold())
                points.append(point)
        return points


class ReviewForm(forms.ModelForm):
    """Reseña de un juego: nota en medias estrellas, texto con formato, lo mejor y lo peor, spoilers, plataforma,
    fechas de la partida y si es una rejugada. La plataforma solo puede ser una de las del juego."""

    rating = forms.TypedChoiceField(
        label="Tu nota",
        required=False,
        coerce=int,
        empty_value=None,
        choices=[("", "Sin nota")] + [(value, stars_label(value)) for value in range(1, MAX_RATING + 1)],
    )
    pros = PointsField(label="Lo mejor")
    cons = PointsField(label="Lo peor")

    class Meta:
        model = Review
        fields = (
            "rating", "text", "pros", "cons", "has_spoilers", "platform", "started_on", "finished_on", "is_replay",
        )
        labels = {
            "text": "Tu reseña",
            "has_spoilers": "Toda la reseña es un spoiler",
            "platform": "Plataforma en la que jugaste",
            "started_on": "Lo empecé el",
            "finished_on": "Lo terminé el",
            "is_replay": "Es una rejugada",
        }
        help_texts = {
            "text": (
                f"Hasta {REVIEW_MAX_LENGTH} caracteres. **negrita**, *cursiva*, ||spoiler|| y "
                "[texto](https://enlace). Deja una línea en blanco para empezar otro párrafo."
            ),
            "has_spoilers": "Se verá plegada. Para ocultar solo una parte, rodéala con ||dos barras||.",
        }
        widgets = {
            "text": forms.Textarea(attrs={
                "rows": 12, "maxlength": REVIEW_MAX_LENGTH,
                "placeholder": "¿Qué te ha parecido? Cuéntaselo a otros jugadores…",
            }),
            "started_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "finished_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, game, **kwargs):
        """Limita la plataforma a las del juego."""
        super().__init__(*args, **kwargs)
        self.fields["platform"].queryset = game.platforms.all()
        self.fields["platform"].empty_label = "Sin decir"

    def clean_text(self):
        """Quita los espacios del principio y del final: una reseña en blanco no vale."""
        text = self.cleaned_data["text"].strip()
        if not text:
            raise forms.ValidationError("Escribe algo en la reseña.")
        return text

    def point_slots(self, name):
        """Casillas de «Lo mejor» (name="pros") o «Lo peor» ("cons") para pintarlas una a una: [(nombre, id, valor)].

        Llevan lo que se acaba de enviar o, si no, lo guardado.
        """
        bound = self[name]
        values = bound.value() if self.is_bound else self.fields[name].widget.decompress(bound.value())
        return [(f"{bound.html_name}_{i}", f"{bound.auto_id}_{i}", value or "") for i, value in enumerate(values)]

    @property
    def pro_slots(self):
        """Casillas de «Lo mejor» (ver point_slots)."""
        return self.point_slots("pros")

    @property
    def con_slots(self):
        """Casillas de «Lo peor» (ver point_slots)."""
        return self.point_slots("cons")

    def clean(self):
        """Comprueba que las fechas no sean del futuro y que no se termine antes de empezar."""
        data = super().clean()
        today = timezone.localdate()
        for field in ("started_on", "finished_on"):
            if data.get(field) and data[field] > today:
                self.add_error(field, "Esa fecha aún no ha llegado.")
        started, finished = data.get("started_on"), data.get("finished_on")
        if started and finished and finished < started:
            self.add_error("finished_on", "No puede ser antes del día en que lo empezaste.")
        return data


class CommentForm(forms.ModelForm):
    """Comentario en una reseña, con el mismo formato sencillo que las reseñas."""

    class Meta:
        model = ReviewComment
        fields = ("text",)
        labels = {"text": "Tu comentario"}
        widgets = {
            "text": forms.Textarea(attrs={
                "rows": 3, "maxlength": COMMENT_MAX_LENGTH, "placeholder": "Escribe un comentario…",
            }),
        }

    def clean_text(self):
        """Quita los espacios del principio y del final: un comentario en blanco no vale."""
        text = self.cleaned_data["text"].strip()
        if not text:
            raise forms.ValidationError("Escribe algo en el comentario.")
        return text
