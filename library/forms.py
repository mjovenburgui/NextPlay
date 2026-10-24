from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts import images

from .models import (
    COMMENT_MAX_LENGTH, MAX_IMAGES, MAX_POINTS, MAX_RATING, POINT_MAX_LENGTH, REPORT_DETAIL_MAX_LENGTH,
    REVIEW_MAX_LENGTH, Report, Review, ReviewComment, ReviewImage,
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
        """Limita la plataforma a las del juego (y la Steam Deck si sale en PC, ver Game.review_platforms)."""
        super().__init__(*args, **kwargs)
        self.fields["platform"].queryset = game.review_platforms()
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


class MultipleImageInput(forms.ClearableFileInput):
    """Selector de archivos que deja elegir varias imágenes a la vez."""

    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    """Varias imágenes subidas en el mismo campo: devuelve la lista de archivos (vacía si no se sube ninguna)."""

    def __init__(self, **kwargs):
        """Con el selector múltiple y opcional."""
        kwargs.setdefault("widget", MultipleImageInput(attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}))
        super().__init__(required=False, **kwargs)

    def clean(self, data, initial=None):
        """Comprueba cada archivo como lo haría un FileField y los devuelve en una lista."""
        files = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultipleImageField, self).clean(file, initial) for file in files if file]


class ReviewImagesForm(forms.Form):
    """Imágenes de una reseña, como mucho MAX_IMAGES: las que ya tiene (se pueden marcar como spoiler o quitar), las que
    se suben (pasadas a WebP y sin EXIF por accounts.images) y las capturas y artes oficiales del juego, que se eligen
    sin subir nada. Las nuevas van detrás de las que había: primero las subidas y después las oficiales.
    """

    quitar = forms.MultipleChoiceField(required=False)
    spoiler = forms.MultipleChoiceField(required=False)
    imagenes = MultipleImageField(label="Subir imágenes")
    # Posiciones (0, 1...) de las subidas que son spoiler, en el orden en que llegan.
    spoiler_nueva = forms.MultipleChoiceField(required=False, choices=[(str(i), str(i)) for i in range(MAX_IMAGES)])
    captura = forms.MultipleChoiceField(required=False)
    spoiler_captura = forms.MultipleChoiceField(required=False)

    def __init__(self, data=None, files=None, *, review, official):
        """review es la reseña que se edita y official, las imágenes oficiales de su juego (games.media.official_images).

        En el elector solo salen las oficiales que la reseña aún no tiene.
        """
        super().__init__(data, files)
        self.current = list(review.images.all()) if review.pk else []
        used = {image.igdb_image_id for image in self.current if image.is_official}
        self.official = [item for item in official if item["id"] not in used]
        ids = [(str(image.pk), str(image.pk)) for image in self.current]
        self.fields["quitar"].choices = self.fields["spoiler"].choices = ids
        choices = [(item["id"], item["heading"]) for item in self.official]
        self.fields["captura"].choices = self.fields["spoiler_captura"].choices = choices

    @property
    def free_slots(self):
        """Cuántas imágenes más caben en la reseña sin quitar ninguna."""
        return MAX_IMAGES - len(self.current)

    def clean(self):
        """Comprueba que no pasen de MAX_IMAGES entre las que se quedan y las nuevas y prepara las subidas.

        Cada subida se convierte a WebP en dos tamaños (la grande para el visor y la miniatura para la
        reseña); las que no son una imagen válida, pesan demasiado o tienen demasiados píxeles dan un error
        con su nombre.
        """
        data = super().clean()
        removed = set(data.get("quitar") or [])
        kept = [image for image in self.current if str(image.pk) not in removed]
        uploads, chosen = data.get("imagenes") or [], data.get("captura") or []
        if len(kept) + len(uploads) + len(chosen) > MAX_IMAGES:
            raise ValidationError(f"Caben {MAX_IMAGES} imágenes por reseña: quita alguna antes de añadir más.")
        data["processed"] = []
        for upload in uploads:
            try:
                data["processed"].append((images.process(upload, "review"), images.process(upload, "review_thumb")))
            except ValidationError as exc:
                self.add_error("imagenes", f"«{upload.name}»: {exc.messages[0]}")
        return data

    def save(self, review):
        """Aplica los cambios a las imágenes de review, que ya debe estar guardada.

        Quita las marcadas (sus archivos se borran con ellas, ver library.signals), pone o quita el spoiler de
        las demás y añade las nuevas detrás.
        """
        data = self.cleaned_data
        removed, spoilers = set(data["quitar"]), set(data["spoiler"])
        position = 0
        for image in self.current:
            if str(image.pk) in removed:
                image.delete()
                continue
            is_spoiler = str(image.pk) in spoilers
            if (image.is_spoiler, image.position) != (is_spoiler, position):
                image.is_spoiler, image.position = is_spoiler, position
                image.save(update_fields=["is_spoiler", "position"])
            position += 1
        new_spoilers = set(data["spoiler_nueva"])
        for i, (full, thumb) in enumerate(data["processed"]):
            ReviewImage.objects.create(
                review=review, image=full, thumb=thumb, is_spoiler=str(i) in new_spoilers, position=position
            )
            position += 1
        official_spoilers = set(data["spoiler_captura"])
        for image_id in data["captura"]:
            ReviewImage.objects.create(
                review=review, igdb_image_id=image_id, is_spoiler=image_id in official_spoilers, position=position
            )
            position += 1


class ReportForm(forms.Form):
    """Denuncia de una reseña o un comentario: el motivo y, si se quiere, algo más para el administrador."""

    reason = forms.ChoiceField(label="Motivo", choices=Report.Reason.choices, widget=forms.RadioSelect)
    detail = forms.CharField(label="Algo más que debamos saber", required=False, max_length=REPORT_DETAIL_MAX_LENGTH)

    def clean_detail(self):
        """Los detalles sin espacios de sobra al principio ni al final."""
        return self.cleaned_data["detail"].strip()


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
