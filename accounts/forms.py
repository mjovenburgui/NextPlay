import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Q
from django.urls import reverse

from games.models import Game
from library.models import Review

from . import images
from .models import (
    DEFAULT_ACCENT, FRAME_ZOOM_MAX, HEX_COLOR, SECTIONS, SOCIAL_LINKS, FavoriteGame, PlatformAccount, Profile,
    normalize_handle,
)

User = get_user_model()

# Los ID de imagen de IGDB son letras minúsculas y números (p. ej. «ar5l8» o «sc6lg9»).
IGDB_IMAGE_ID = re.compile(r"^[a-z0-9]{1,40}$")
# Imágenes del perfil: campo → tipo de imagen en accounts.images.
IMAGE_FIELDS = {"avatar": "avatar", "banner": "banner", "background": "background"}
# Dirección blob: con la que el navegador enseña una imagen elegida y aún sin subir (solo vale en ese navegador).
LOCAL_IMAGE = re.compile(r"^blob:https?://[^\s\"'<>]+$")
# Campos del encuadre de cada imagen (Profile.avatar_x...): la posición la pone el editor de Editar perfil al
# arrastrar la imagen (campos ocultos), el zoom es una barra y «entera», una casilla.
FRAMING_FIELDS = tuple(f"{name}_{part}" for name in IMAGE_FIELDS for part in ("x", "y", "zoom", "fit"))


class SignupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        """Pasa el correo a minúsculas y rechaza el registro si ya hay una cuenta con ese correo."""
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Ya existe una cuenta con este correo.")
        return email


class ProfileForm(forms.ModelForm):
    """Todo lo que se cambia en Editar perfil salvo las tiendas y las cuentas vinculadas.

    Datos (nombre, estado, pronombres, ubicación, biografía), apariencia (foto, cabecera, fondo, color y
    diseño), escaparate (favoritos, reseña destacada y secciones), redes sociales y privacidad. Las
    redes llegan en campos link_<red> y se guardan juntas en Profile.links. Los favoritos y las
    secciones llegan como listas en el orden en que están en la página.
    """

    # Campos con valor por defecto que pueden no venir en el envío: si faltan se deja lo que había.
    OPTIONAL = (
        "avatar_source", "avatar_shape", "banner_mode", "background_mode", "background_dim", "layout",
        *(name for name in FRAMING_FIELDS if not name.endswith("_fit")),
    )
    # Interruptores de «Privacidad y avisos».
    PRIVACY = ("is_public", "price_alert_emails")

    favoritos = forms.TypedMultipleChoiceField(coerce=int, required=False)
    quitar_favoritos = forms.TypedMultipleChoiceField(coerce=int, required=False)
    secciones = forms.MultipleChoiceField(choices=SECTIONS, required=False)
    secciones_visibles = forms.MultipleChoiceField(choices=SECTIONS, required=False)

    class Meta:
        model = Profile
        fields = (
            "display_name", "bio", "status_emoji", "status_text", "pronouns", "location",
            "avatar", "avatar_source", "avatar_shape", "banner_mode", "banner", "banner_game", "banner_art",
            "background_mode", "background", "background_dim", "accent_color", "layout", "pinned_review",
            "is_public", "price_alert_emails", *FRAMING_FIELDS,
        )
        widgets = {
            **{f"{name}_{part}": forms.HiddenInput for name in IMAGE_FIELDS for part in ("x", "y")},
            **{f"{name}_zoom": forms.NumberInput(attrs={"type": "range", "step": 5}) for name in IMAGE_FIELDS},
            "bio": forms.Textarea(attrs={"rows": 4}),
            "status_emoji": forms.TextInput(attrs={"placeholder": "🎮", "class": "emoji-input"}),
            "status_text": forms.TextInput(attrs={"placeholder": "Platineando Bloodborne"}),
            "pronouns": forms.TextInput(attrs={"placeholder": "él, ella, elle…"}),
            "location": forms.TextInput(attrs={"placeholder": "Madrid"}),
            "avatar": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}),
            "banner": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}),
            "background": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}),
            "avatar_source": forms.RadioSelect,
            "avatar_shape": forms.RadioSelect,
            "banner_mode": forms.RadioSelect,
            "background_mode": forms.RadioSelect,
            "layout": forms.RadioSelect,
            "banner_art": forms.HiddenInput,
            "background_dim": forms.NumberInput(attrs={"type": "range", "min": 0, "max": 90, "step": 5}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, user, **kwargs):
        """Prepara el formulario para user.

        Los juegos de la cabecera son los de su biblioteca (y el que ya tenga puesto), las reseñas que
        puede destacar son las suyas y las fotos que puede elegir son las de las cuentas que tiene
        vinculadas. Añade un campo por red social con lo que tenga guardado.
        """
        super().__init__(*args, **kwargs)
        self.user = user
        profile = self.instance
        for name in self.OPTIONAL:
            self.fields[name].required = False
        for name in IMAGE_FIELDS:
            # Límites de la barra del zoom (Django le pondría min="0" por ser un entero positivo).
            self.fields[f"{name}_zoom"].widget.attrs.update({"min": 100, "max": FRAME_ZOOM_MAX})

        sources = {Profile.AvatarSource.AUTO, Profile.AvatarSource.UPLOAD, Profile.AvatarSource.INITIAL}
        if profile.steam_linked:
            sources.add(Profile.AvatarSource.STEAM)
        if profile.psn:
            sources.add(Profile.AvatarSource.PSN)
        if profile.xbox:
            sources.add(Profile.AvatarSource.XBOX)
        field = self.fields["avatar_source"]
        field.choices = [choice for choice in field.choices if choice[0] in sources]

        field = self.fields["banner_game"]
        field.queryset = Game.objects.filter(
            Q(library_entries__user=user) | Q(pk=profile.banner_game_id or 0)
        ).distinct().order_by("name")
        field.empty_label = "Elige un juego de tu biblioteca"
        # Al cambiar de juego, HTMX trae sus imágenes para elegir la de la cabecera.
        field.widget.attrs.update({
            "hx-get": reverse("banner_arts"), "hx-trigger": "change", "hx-target": "#banner-arts", "hx-swap": "outerHTML",
        })

        field = self.fields["pinned_review"]
        field.queryset = (
            Review.objects.filter(user=user).published().select_related("game").order_by("game__name", "-published_at")
        )
        # Con varias reseñas del mismo juego, la fecha las distingue.
        field.label_from_instance = lambda review: f"{review.game.name} ({review.published_at:%d/%m/%Y})"
        field.empty_label = "Ninguna"

        self.favorites = list(user.favorite_games.select_related("game"))
        choices = [(favorite.game_id, favorite.game.name) for favorite in self.favorites]
        self.fields["favoritos"].choices = choices
        self.fields["quitar_favoritos"].choices = choices

        # El selector de color no admite «vacío»: sin color propio enseña el violeta de NextPlay.
        self.initial["accent_color"] = profile.accent
        links = profile.links or {}
        for key, (name, _, _) in SOCIAL_LINKS.items():
            self.fields[f"link_{key}"] = forms.CharField(
                label=name, required=False, max_length=200, initial=links.get(key, "")
            )
        # Campo de texto y no type=url: así el navegador acepta «miweb.com» y Django le pone https://.
        self.fields["link_web"] = forms.URLField(
            label="Web", required=False, max_length=200, assume_scheme="https", initial=links.get("web", ""),
            widget=forms.TextInput(attrs={"inputmode": "url", "placeholder": "https://…"}),
        )
        # Nombres de las imágenes guardadas, para borrarlas del disco si se sustituyen o se quitan.
        self._old_images = {name: getattr(profile, name).name for name in IMAGE_FIELDS}

    @property
    def privacy_fields(self):
        """Los campos de «Privacidad y avisos», que la plantilla pinta como interruptores."""
        return [self[name] for name in self.PRIVACY]

    def _clean_image(self, name):
        """Pasa la imagen nueva del campo name por accounts.images; sin imagen nueva deja lo que había (False = quitarla)."""
        value = self.cleaned_data.get(name)
        if isinstance(value, UploadedFile):
            return images.process(value, IMAGE_FIELDS[name])
        return value

    def clean_avatar(self):
        """Foto de perfil nueva, recortada en cuadrado y en WebP."""
        return self._clean_image("avatar")

    def clean_banner(self):
        """Imagen de cabecera nueva, recortada en apaisado (3:1) y en WebP."""
        return self._clean_image("banner")

    def clean_background(self):
        """Imagen de fondo nueva, reducida a 1920 × 1200 como mucho y en WebP."""
        return self._clean_image("background")

    def clean_accent_color(self):
        """Color en minúsculas; vacío si es el violeta por defecto. Rechaza lo que no sea #rrggbb."""
        value = (self.cleaned_data.get("accent_color") or "").strip().lower()
        if value and not HEX_COLOR.match(value):
            raise forms.ValidationError("Elige un color de la paleta o escribe uno como #9d5cff.")
        return "" if value == DEFAULT_ACCENT else value

    def clean_banner_art(self):
        """ID de la imagen de IGDB elegida para la cabecera; solo minúsculas y números, como los de IGDB."""
        value = (self.cleaned_data.get("banner_art") or "").strip()
        if value and not IGDB_IMAGE_ID.match(value):
            raise forms.ValidationError("La imagen del juego no es válida.")
        return value

    def clean(self):
        """Comprueba las redes sociales y que la cabecera y el fondo elegidos tengan su imagen o su juego.

        Deja en cleaned_data["links"] el diccionario que se guardará en Profile.links.
        """
        data = super().clean()
        links = {}
        for key, (name, pattern, _) in SOCIAL_LINKS.items():
            value = normalize_handle(key, data.get(f"link_{key}"))
            if value and not re.match(pattern, value):
                self.add_error(f"link_{key}", f"No parece un usuario de {name} válido.")
            elif value:
                links[key] = value
        web = data.get("link_web")
        if web and not web.lower().startswith(("http://", "https://")):
            self.add_error("link_web", "La web tiene que empezar por http:// o https://.")
        elif web:
            links["web"] = web
        data["links"] = links

        banner_mode = data.get("banner_mode") or self.instance.banner_mode
        if banner_mode == Profile.BannerMode.IMAGE and not data.get("banner") and "banner" not in self.errors:
            self.add_error("banner", "Sube la imagen de la cabecera o elige otro tipo de cabecera.")
        if banner_mode == Profile.BannerMode.GAME and not data.get("banner_game") and "banner_game" not in self.errors:
            self.add_error("banner_game", "Elige el juego de la cabecera.")
        background_mode = data.get("background_mode") or self.instance.background_mode
        if background_mode == Profile.BackgroundMode.IMAGE and not data.get("background") and "background" not in self.errors:
            self.add_error("background", "Sube la imagen de fondo o elige otro fondo.")
        game = data.get("banner_game")
        if not game:
            data["banner_art"] = ""
        elif game.pk != self.instance.banner_game_id and data.get("banner_art") == self.instance.banner_art:
            # Ha cambiado de juego pero la imagen es la del anterior (p. ej. sin JavaScript): su portada.
            data["banner_art"] = ""
        return data

    def save(self, commit=True):
        """Guarda el perfil con sus redes y el orden de sus secciones.

        Con commit también borra del disco las imágenes sustituidas o quitadas y guarda el orden de los
        favoritos (quitando los marcados).
        """
        profile = super().save(commit=False)
        self._apply_extras(profile)
        if commit:
            profile.save()
            self._delete_replaced_images(profile)
            self._save_favorites()
        return profile

    def preview(self):
        """Perfil y favoritos tal como quedarían con lo enviado, para la vista previa de Editar perfil.

        No guarda nada: valida el formulario y lo que no sea válido se queda como estaba. Las imágenes
        nuevas no llegan, porque no se suben hasta guardar; en su lugar vienen en local_avatar,
        local_banner y local_background las direcciones blob: con las que el navegador ya las enseña.
        Devuelve (perfil sin guardar, favoritos en su nuevo orden).
        """
        self.is_valid()
        profile = self.instance
        self._apply_extras(profile)
        profile.preview_images = {
            name: url for name in IMAGE_FIELDS if LOCAL_IMAGE.match(url := self.data.get(f"local_{name}", ""))
        }
        return profile, self._favorite_changes()[1]

    def _apply_extras(self, profile):
        """Pone en profile lo que no se guarda campo a campo: las redes (en links) y el orden y la visibilidad de las secciones.

        Si las secciones enviadas no son válidas, se dejan como estaban.
        """
        profile.links = self.cleaned_data.get("links", {})
        sections, visible = self.cleaned_data.get("secciones"), self.cleaned_data.get("secciones_visibles")
        if "secciones" in self.data and sections is not None and visible is not None:
            profile.sections = [[key, key in visible] for key in dict.fromkeys(sections)]

    def _delete_replaced_images(self, profile):
        """Borra del disco las imágenes que tenía el perfil y ya no usa (sustituidas por otra o quitadas)."""
        for name, old in self._old_images.items():
            image = getattr(profile, name)
            if old and image.name != old:
                image.storage.delete(old)

    def _save_favorites(self):
        """Quita los favoritos marcados y guarda el orden en que llegaron los demás (los que no llegaron, al final)."""
        if "favoritos" not in self.data and "quitar_favoritos" not in self.data:
            return
        removed, kept = self._favorite_changes()
        FavoriteGame.objects.filter(user=self.user, game_id__in=removed).delete()
        for position, favorite in enumerate(kept):
            favorite.position = position
        FavoriteGame.objects.bulk_update(kept, ["position"])

    def _favorite_changes(self):
        """(ids de los juegos que se quitan, favoritos que quedan) según el envío, sin guardar nada.

        Los que quedan van en el orden en que llegaron en favoritos y los que no llegaron, al final.
        """
        removed = set(self.cleaned_data.get("quitar_favoritos", []))
        kept = [favorite for favorite in self.favorites if favorite.game_id not in removed]
        order = [game_id for game_id in dict.fromkeys(self.cleaned_data.get("favoritos", [])) if game_id not in removed]
        order += [favorite.game_id for favorite in kept if favorite.game_id not in order]
        kept.sort(key=lambda favorite: order.index(favorite.game_id))
        return removed, kept


class PlatformAccountForm(forms.Form):
    platform = forms.ChoiceField(label="Plataforma", choices=PlatformAccount.Platform.choices)
    handle = forms.CharField(label="Nombre o código", max_length=40)

    def clean(self):
        """Normaliza el nombre o código y comprueba que tiene el formato de la plataforma elegida."""
        data = super().clean()
        platform, handle = data.get("platform"), data.get("handle")
        if platform and handle is not None:
            handle = PlatformAccount.normalize(platform, handle)
            if not PlatformAccount.is_valid_handle(platform, handle):
                label, help_text = PlatformAccount.RULES[platform][:2]
                raise forms.ValidationError(f"{label} no válido. {help_text}")
            data["handle"] = handle
        return data
