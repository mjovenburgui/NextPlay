import re
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse

from games.models import igdb_image_url

# Color de los perfiles que no eligen otro (el violeta de NextPlay) y paleta que se ofrece en Editar perfil.
DEFAULT_ACCENT = "#9d5cff"
ACCENT_PRESETS = [
    ("#9d5cff", "Violeta"), ("#4f8bff", "Azul"), ("#22e0f0", "Cian"), ("#3fbf3f", "Verde"),
    ("#ffc84a", "Oro"), ("#ff8a3d", "Naranja"), ("#ff4d5e", "Rojo"), ("#ff4fa3", "Rosa"), ("#e6e8f5", "Blanco"),
]
HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$")
# Zoom máximo (en %) del encuadre de las imágenes del perfil.
FRAME_ZOOM_MAX = 400

# Secciones del escaparate del perfil, en su orden por defecto: (clave, nombre).
SECTIONS = [
    ("favoritos", "Juegos favoritos"),
    ("jugando", "Jugando ahora"),
    ("resena", "Reseña destacada"),
    ("estadisticas", "Estadísticas"),
    ("generos", "Géneros que más juega"),
]

# Redes sociales que se pueden enseñar en el perfil: clave → (nombre, expresión que debe cumplir el usuario,
# URL del perfil o vacía si la red no tiene páginas públicas). La web personal va aparte, con la clave «web».
SOCIAL_LINKS = {
    "twitch": ("Twitch", r"^[A-Za-z0-9_]{3,25}$", "https://www.twitch.tv/{}"),
    "youtube": ("YouTube", r"^[A-Za-z0-9._-]{3,30}$", "https://www.youtube.com/@{}"),
    "x": ("X", r"^[A-Za-z0-9_]{1,15}$", "https://x.com/{}"),
    "bluesky": ("Bluesky", r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$", "https://bsky.app/profile/{}"),
    "instagram": ("Instagram", r"^[A-Za-z0-9._]{1,30}$", "https://www.instagram.com/{}/"),
    "tiktok": ("TikTok", r"^[A-Za-z0-9._]{2,24}$", "https://www.tiktok.com/@{}"),
    "discord": ("Discord", r"^[a-z0-9_.]{2,32}$", ""),
}


def normalize_handle(network, value):
    """Limpia el usuario de una red social tal como lo escribe la gente.

    Quita los espacios y la @ del principio y, si pega la URL de su perfil, se queda con el usuario
    (en Bluesky, lo que va después de /profile/). El usuario de Discord se pasa a minúsculas.
    """
    value = (value or "").strip()
    if value.lower().startswith(("http://", "https://")):
        segments = [segment for segment in urlparse(value).path.split("/") if segment]
        if network == "bluesky" and segments[:1] == ["profile"]:
            segments = segments[1:]
        value = segments[0] if segments else ""
    value = value.removeprefix("@")
    return value.lower() if network == "discord" else value


def profile_upload_to(instance, filename):
    """Ruta de las imágenes de un perfil: perfiles/<id del usuario>/<nombre aleatorio que pone accounts.images>."""
    return f"perfiles/{instance.user_id}/{filename}"


class User(AbstractUser):
    # Custom from day one: swapping the user model after the first migration is painful.
    email = models.EmailField("correo electrónico", unique=True)


class Profile(models.Model):
    class AvatarSource(models.TextChoices):
        AUTO = "auto", "Automática: la tuya si la subes; si no, la de Steam, PlayStation o Xbox"
        UPLOAD = "upload", "La que he subido"
        STEAM = "steam", "La de Steam"
        PSN = "psn", "La de PlayStation"
        XBOX = "xbox", "La de Xbox"
        INITIAL = "initial", "Solo mi inicial"

    class AvatarShape(models.TextChoices):
        CIRCLE = "circle", "Redonda"
        ROUNDED = "rounded", "Redondeada"
        HEXAGON = "hexagon", "Hexágono"

    class BannerMode(models.TextChoices):
        GRADIENT = "gradient", "Degradado con el color del perfil"
        IMAGE = "image", "Una imagen mía"
        GAME = "game", "El arte de un juego"

    class BackgroundMode(models.TextChoices):
        NONE = "none", "Sin fondo"
        BANNER = "banner", "La imagen de la cabecera"
        IMAGE = "image", "Una imagen mía"

    class Layout(models.TextChoices):
        CLASSIC = "classic", "Clásica: foto a la izquierda"
        CENTERED = "centered", "Centrada"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField("nombre visible", max_length=50, blank=True)
    bio = models.TextField("biografía", max_length=300, blank=True)
    is_public = models.BooleanField(
        "perfil público",
        default=True,
        help_text="Si lo desactivas, solo tú podrás ver tu perfil y tu biblioteca.",
    )
    # La biblioteca de Steam se vuelve a importar sola cada noche (comando sync_libraries) salvo que lo desactive.
    auto_sync = models.BooleanField("sincronizar Steam cada noche", default=True)
    auto_synced_at = models.DateTimeField("última sincronización automática", null=True, blank=True)
    # Al importar Steam también se trae su lista de deseos a «Lo quiero» (accounts.steam.import_wishlist), salvo
    # que lo desactive. Los juegos de esa lista que quitó a mano de «Lo quiero» no se vuelven a traer (ids de Game).
    steam_wishlist = models.BooleanField("traer la lista de deseos de Steam", default=True)
    steam_wishlist_skip = models.JSONField("deseados de Steam que no traer", default=list, blank=True)
    # Ids de las tiendas en las que compra (los de GamePrice.shop_id; 0 es Xbox). Vacía = todas.
    shops = models.JSONField("tiendas en las que compro", default=list, blank=True)
    price_alert_emails = models.BooleanField(
        "avisarme también por correo",
        default=True,
        help_text="Cuando un juego de tu lista de deseos baje de tu precio o llegue a su mínimo histórico.",
    )
    # Personalización. Las imágenes llegan ya tratadas por accounts.images (recortadas, en WebP y sin EXIF).
    status_emoji = models.CharField("emoji del estado", max_length=16, blank=True)
    status_text = models.CharField("estado", max_length=80, blank=True)
    pronouns = models.CharField("pronombres", max_length=30, blank=True)
    location = models.CharField("ubicación", max_length=60, blank=True)
    avatar = models.ImageField("foto de perfil", upload_to=profile_upload_to, blank=True)
    avatar_source = models.CharField(
        "foto que se enseña", max_length=10, choices=AvatarSource.choices, default=AvatarSource.AUTO
    )
    avatar_shape = models.CharField(
        "forma de la foto", max_length=10, choices=AvatarShape.choices, default=AvatarShape.CIRCLE
    )
    banner = models.ImageField("imagen de la cabecera", upload_to=profile_upload_to, blank=True)
    banner_mode = models.CharField("cabecera", max_length=10, choices=BannerMode.choices, default=BannerMode.GRADIENT)
    banner_game = models.ForeignKey(
        "games.Game", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="juego de la cabecera",
    )
    # ID de imagen de IGDB (arte o captura) del juego de la cabecera; vacío = su portada, desenfocada.
    banner_art = models.CharField("imagen del juego", max_length=40, blank=True)
    background = models.ImageField("imagen de fondo", upload_to=profile_upload_to, blank=True)
    background_mode = models.CharField(
        "fondo de la página", max_length=10, choices=BackgroundMode.choices, default=BackgroundMode.NONE
    )
    background_dim = models.PositiveSmallIntegerField(
        "oscurecer el fondo", default=60, validators=[MinValueValidator(0), MaxValueValidator(90)],
        help_text="Tanto por ciento: cuanto más oscuro, mejor se lee el texto encima.",
    )
    # Encuadre de cada imagen (foto, cabecera y fondo), que se guardan enteras: qué parte se ve y con cuánto
    # zoom, o «entera» (toda la imagen encogida, con una copia suya desenfocada en el hueco que sobra).
    # x e y van de 0 a 100 como object-position de CSS: 0 pega la imagen al borde izquierdo (o al de
    # arriba), 50 la centra y 100 la pega al derecho (o al de abajo). El zoom va en % (100 = justo cubre).
    avatar_x = models.FloatField("foto: posición horizontal", default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])
    avatar_y = models.FloatField("foto: posición vertical", default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])
    avatar_zoom = models.PositiveSmallIntegerField(
        "foto: zoom", default=100, validators=[MinValueValidator(100), MaxValueValidator(FRAME_ZOOM_MAX)]
    )
    avatar_fit = models.BooleanField("foto entera", default=False)
    banner_x = models.FloatField("cabecera: posición horizontal", default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])
    banner_y = models.FloatField("cabecera: posición vertical", default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])
    banner_zoom = models.PositiveSmallIntegerField(
        "cabecera: zoom", default=100, validators=[MinValueValidator(100), MaxValueValidator(FRAME_ZOOM_MAX)]
    )
    banner_fit = models.BooleanField("cabecera entera", default=False)
    background_x = models.FloatField("fondo: posición horizontal", default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])
    background_y = models.FloatField("fondo: posición vertical", default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])
    background_zoom = models.PositiveSmallIntegerField(
        "fondo: zoom", default=100, validators=[MinValueValidator(100), MaxValueValidator(FRAME_ZOOM_MAX)]
    )
    background_fit = models.BooleanField("fondo entero", default=False)
    accent_color = models.CharField("color del perfil", max_length=7, blank=True)
    layout = models.CharField("diseño de la cabecera", max_length=10, choices=Layout.choices, default=Layout.CLASSIC)
    # Usuario de cada red de SOCIAL_LINKS y, con la clave «web», la URL de su web: {"twitch": "usuario", ...}.
    links = models.JSONField("redes sociales", default=dict, blank=True)
    pinned_review = models.ForeignKey(
        "library.Review", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="reseña destacada",
    )
    # Orden y visibilidad del escaparate: [[clave de SECTIONS, visible], ...]. Vacía = el orden por defecto.
    sections = models.JSONField("secciones del perfil", default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Solo en la vista previa de Editar perfil: imágenes elegidas en el navegador y aún sin subir,
    # {campo: dirección blob:}. ProfileForm.preview pone un diccionario nuevo; este no se modifica.
    preview_images = {}

    def __str__(self):
        """Texto del perfil en el admin y en la consola: su nombre visible."""
        return self.name

    @property
    def name(self):
        """Nombre que se muestra: el nombre visible si lo tiene, si no el nombre de usuario."""
        return self.display_name or self.user.username

    def get_absolute_url(self):
        """URL del perfil público del usuario (/u/<usuario>/)."""
        return reverse("profile", args=[self.user.username])

    def visible_to(self, viewer):
        """Indica si viewer puede ver el perfil: siempre si es público, y si es privado solo su dueño.

        Es la regla de quién ve qué en todo NextPlay (perfil, biblioteca, reseñas, seguidores...); en las
        consultas la aplica visible_filter, que tiene que decir lo mismo.
        """
        return self.is_public or viewer == self.user

    @staticmethod
    def visible_filter(viewer, path="user"):
        """La regla de visible_to como condición de consulta (Q): lo de los perfiles públicos y lo de viewer.

        path es el camino desde lo que se filtra hasta su usuario: «user» en las reseñas (el de por defecto),
        «follower» en los seguidores...; vacío para filtrar los propios usuarios.
        """
        prefix = f"{path}__" if path else ""
        visible = models.Q(**{f"{prefix}profile__is_public": True})
        if viewer.is_authenticated:
            visible |= models.Q(**{path or "pk": viewer.pk})
        return visible

    @property
    def steam_accounts(self):
        """Cuentas de Steam vinculadas, de la primera a la última (aprovecha el prefetch si lo hay)."""
        return list(self.user.steam_accounts.all())

    @property
    def steam_linked(self):
        """Indica si el usuario tiene al menos una cuenta de Steam vinculada."""
        return bool(self.steam_accounts)

    @property
    def steam_avatar(self):
        """Avatar de la primera cuenta de Steam vinculada, o cadena vacía si no tiene ninguna."""
        accounts = self.steam_accounts
        return accounts[0].avatar if accounts else ""

    @property
    def psn(self):
        """Cuenta de PlayStation vinculada, o None si no tiene."""
        try:
            return self.user.psn_account
        except PSNAccount.DoesNotExist:
            return None

    @property
    def platforms(self):
        """Cuentas de otras plataformas (Xbox, Switch, Epic...) que muestra en su perfil."""
        return list(self.user.platform_accounts.all())

    @property
    def xbox(self):
        """Cuenta de Xbox vinculada, o None si no tiene."""
        try:
            return self.user.xbox_account
        except XboxAccount.DoesNotExist:
            return None

    @property
    def avatar_url(self):
        """Foto del perfil según avatar_source: la subida o la de Steam, PlayStation o Xbox; vacía para la inicial.

        En automático (o si la elegida ya no existe, p. ej. porque desvinculó la cuenta) prueba por orden
        la subida, la de Steam, la de PlayStation y la de Xbox. Cada una se mira solo si hace falta.
        """
        if self.avatar_source == self.AvatarSource.INITIAL:
            return ""
        options = {
            self.AvatarSource.UPLOAD: lambda: self._uploaded_url("avatar"),
            self.AvatarSource.STEAM: lambda: self.steam_avatar,
            self.AvatarSource.PSN: lambda: self.psn.avatar if self.psn else "",
            self.AvatarSource.XBOX: lambda: self.xbox.avatar if self.xbox else "",
        }
        chosen = options.get(self.avatar_source)
        if chosen and (url := chosen()):
            return url
        return next((url for get in options.values() if (url := get())), "")

    @property
    def accent(self):
        """Color del perfil en hexadecimal; el violeta de NextPlay si no ha elegido otro."""
        return self.accent_color if HEX_COLOR.match(self.accent_color or "") else DEFAULT_ACCENT

    @property
    def accent_rgb(self):
        """El color del perfil como «r, g, b», para usarlo con transparencia en el CSS: rgba(var(--accent-rgb), .3)."""
        return ", ".join(str(int(self.accent[i:i + 2], 16)) for i in (1, 3, 5))

    @property
    def accent_text(self):
        """Color del texto encima del color del perfil: blanco, como en el resto de la web, salvo en los
        colores claros (contraste con el blanco por debajo de 3:1, el mínimo de WCAG para texto en
        negrita), que llevan texto casi negro."""

        def linear(channel):
            """Canal sRGB (0-255) en luz lineal, como pide la fórmula de luminancia de WCAG."""
            value = channel / 255
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        r, g, b = (linear(int(self.accent[i:i + 2], 16)) for i in (1, 3, 5))
        luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return "#ffffff" if 1.05 / (luminance + 0.05) >= 3 else "#0b0d18"

    @property
    def banner_url(self):
        """Imagen de la cabecera según banner_mode, o vacía para el degradado con el color del perfil.

        Con un juego usa el arte de IGDB elegido o, si no hay, su portada (que se enseña desenfocada).
        """
        if self.banner_mode == self.BannerMode.IMAGE and (url := self._uploaded_url("banner")):
            return url
        if self.banner_mode == self.BannerMode.GAME and self.banner_game_id:
            return igdb_image_url(self.banner_art or self.banner_game.cover_image_id, "t_1080p")
        return ""

    @property
    def banner_blurred(self):
        """Indica si la cabecera es la portada de un juego: es vertical y se desenfoca para que no se vea pixelada."""
        return self.banner_mode == self.BannerMode.GAME and bool(self.banner_game_id) and not self.banner_art

    @property
    def background_url(self):
        """Imagen de fondo de la página del perfil según background_mode, o vacía si no tiene."""
        if self.background_mode == self.BackgroundMode.BANNER:
            return self.banner_url
        if self.background_mode == self.BackgroundMode.IMAGE:
            return self._uploaded_url("background")
        return ""

    def _uploaded_url(self, name):
        """URL de la imagen subida del campo name (avatar, banner o background), o vacía si no hay.

        En la vista previa de Editar perfil manda la que se acaba de elegir y aún no se ha subido.
        """
        image = getattr(self, name)
        return self.preview_images.get(name) or (image.url if image else "")

    @property
    def background_blurred(self):
        """Indica si el fondo es la portada de un juego (la de la cabecera) y hay que desenfocarlo."""
        return self.background_mode == self.BackgroundMode.BANNER and self.banner_blurred

    @property
    def background_dim_ratio(self):
        """Lo que se oscurece el fondo como número entre 0 y 1 con punto decimal, para el CSS."""
        return f"{self.background_dim / 100:.2f}"

    @property
    def avatar_image(self):
        """La foto que se enseña, para accounts/_avatar.html: {"url", "framing", "fit"}, o {} si va la inicial.

        El encuadre (ver _framing) solo vale para la foto subida: las de Steam, PlayStation y Xbox se
        enseñan tal cual.
        """
        url = self.avatar_url
        if not url:
            return {}
        if url != self._uploaded_url("avatar"):
            return {"url": url, "framing": "", "fit": False}
        return {"url": url, "framing": self._framing("avatar"), "fit": self.avatar_fit}

    @property
    def banner_framing(self):
        """Encuadre de la cabecera como variables de CSS (ver _framing)."""
        return self._framing("banner")

    @property
    def background_framing(self):
        """Encuadre del fondo de la página como variables de CSS (ver _framing)."""
        return self._framing("background")

    def _framing(self, name):
        """Encuadre de la imagen name (avatar, banner o background) como variables de CSS para su atributo style.

        «--focus-x: 30%; --focus-y: 20%; --zoom: 1.5;», que app.css pasa a object-position y a la escala
        de la imagen. Vacío con el encuadre por defecto: centrada y sin zoom.
        """
        x, y, zoom = (getattr(self, f"{name}_{part}") for part in ("x", "y", "zoom"))
        if (x, y, zoom) == (50, 50, 100):
            return ""
        return f"--focus-x: {x:g}%; --focus-y: {y:g}%; --zoom: {zoom / 100:g};"

    @property
    def section_layout(self):
        """Secciones del escaparate en el orden elegido: [(clave, nombre, visible)].

        Las que no estén en sections (nuevas o nunca configuradas) van al final y visibles; las claves
        que ya no existen o repetidas se ignoran.
        """
        names = dict(SECTIONS)
        layout, seen = [], set()
        for item in self.sections or []:
            if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] in names and item[0] not in seen:
                layout.append((item[0], names[item[0]], bool(item[1])))
                seen.add(item[0])
        return layout + [(key, name, True) for key, name in SECTIONS if key not in seen]

    @property
    def visible_sections(self):
        """Claves de las secciones del escaparate que se enseñan, en su orden."""
        return [key for key, _, visible in self.section_layout if visible]

    @property
    def social_links(self):
        """Redes del perfil para enseñarlas: [(clave, nombre, usuario o dominio, URL o vacía)].

        Van en el orden de SOCIAL_LINKS y la web personal al final, con su dominio como texto.
        """
        links = self.links or {}
        result = [
            (key, name, links[key], url.format(links[key]) if url else "")
            for key, (name, _, url) in SOCIAL_LINKS.items()
            if links.get(key)
        ]
        if links.get("web"):
            result.append(("web", "Web", urlparse(links["web"]).netloc.removeprefix("www."), links["web"]))
        return result


class FavoriteGame(models.Model):
    """Juego que el usuario enseña en «Juegos favoritos» de su perfil; como mucho MAX_PER_USER, en el orden que elija."""

    MAX_PER_USER = 5

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorite_games")
    game = models.ForeignKey("games.Game", on_delete=models.CASCADE, related_name="+", verbose_name="juego")
    position = models.PositiveSmallIntegerField("posición", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["user", "game"], name="unique_favorite_game_per_user")]
        verbose_name = "juego favorito"
        verbose_name_plural = "juegos favoritos"

    def __str__(self):
        """Texto en el admin: «usuario · juego»."""
        return f"{self.user} · {self.game}"


class SteamAccount(models.Model):
    """Cuenta de Steam vinculada a un usuario; un usuario puede tener varias."""

    MAX_PER_USER = 5

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="steam_accounts")
    # Se vincula con el inicio de sesión OpenID de Steam; el SteamID64 es lo único que Steam garantiza.
    # Único: una misma cuenta de Steam no puede estar en dos usuarios de NextPlay.
    steam_id = models.CharField("SteamID", max_length=20, unique=True)
    persona = models.CharField("nombre en Steam", max_length=100, blank=True)
    avatar = models.URLField("avatar de Steam", max_length=300, blank=True)
    profile_url = models.URLField("perfil de Steam", max_length=300, blank=True)
    synced_at = models.DateTimeField("última importación", null=True, blank=True)
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["linked_at", "id"]
        verbose_name = "cuenta de Steam"
        verbose_name_plural = "cuentas de Steam"

    def __str__(self):
        """Texto de la cuenta en el admin: su nombre en Steam, o el SteamID si no se conoce."""
        return self.persona or self.steam_id

    @property
    def name(self):
        """Nombre que se muestra: el de Steam, o el SteamID si aún no se ha podido leer."""
        return self.persona or self.steam_id

    @property
    def url(self):
        """URL del perfil en Steam; la construye con el SteamID si Steam no la ha dado."""
        return self.profile_url or f"https://steamcommunity.com/profiles/{self.steam_id}/"


class PSNAccount(models.Model):
    """Cuenta de PlayStation Network vinculada a un usuario (una por usuario).

    No se guarda ningún código ni token de Sony: el usuario pega su código NPSSO cada vez que sincroniza.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="psn_account")
    # ID interno de la cuenta en PSN; único para que una cuenta no esté en dos usuarios de NextPlay.
    account_id = models.CharField("ID de cuenta de PSN", max_length=32, unique=True)
    online_id = models.CharField("PSN ID", max_length=32, blank=True)
    avatar = models.URLField("avatar de PSN", max_length=300, blank=True)
    synced_at = models.DateTimeField("última importación", null=True, blank=True)
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "cuenta de PlayStation"
        verbose_name_plural = "cuentas de PlayStation"

    def __str__(self):
        """Texto de la cuenta en el admin: su PSN ID, o el ID de cuenta si no se conoce."""
        return self.name

    @property
    def name(self):
        """Nombre que se muestra: el PSN ID, o «PlayStation» si Sony no lo ha devuelto."""
        return self.online_id or "PlayStation"

    @property
    def url(self):
        """Perfil público en PSNProfiles (Sony no tiene página pública de perfiles), o vacío sin PSN ID."""
        return f"https://psnprofiles.com/{self.online_id}" if self.online_id else ""


class PlatformAccount(models.Model):
    """Cuenta de otra plataforma que el usuario muestra en su perfil (sin importar juegos).

    No se verifica que sea suya: es lo que escribe, como en la mayoría de webs. Una por plataforma.
    """

    class Platform(models.TextChoices):
        XBOX = "xbox", "Xbox"
        NINTENDO = "nintendo", "Nintendo Switch"
        EPIC = "epic", "Epic Games"
        GOG = "gog", "GOG"
        BATTLENET = "battlenet", "Battle.net"

    # Por plataforma: (etiqueta del campo, ayuda, expresión que debe cumplir, plantilla del logo).
    RULES = {
        Platform.XBOX: (
            "Gamertag", "De 1 a 15 letras, números o espacios; los nuevos llevan #1234 al final.",
            r"^[A-Za-z][A-Za-z0-9 ]{0,14}(#\d{1,4})?$", "games/shop_icons/xbox.svg",
        ),
        Platform.NINTENDO: (
            "Código de amigo", "SW-1234-5678-9012 (está en tu perfil de la consola).",
            r"^SW-\d{4}-\d{4}-\d{4}$", "games/shop_icons/nintendo.svg",
        ),
        Platform.EPIC: (
            "Nombre de Epic", "Tu nombre público de Epic Games (de 3 a 32 caracteres).",
            r"^[^\s].{1,30}[^\s]$", "games/shop_icons/epic.svg",
        ),
        Platform.GOG: (
            "Usuario de GOG", "Tu nombre de usuario de GOG.com (letras, números, - _ .).",
            r"^[A-Za-z0-9_.-]{2,32}$", "games/shop_icons/gog.svg",
        ),
        Platform.BATTLENET: (
            "BattleTag", "Nombre#1234, tal como aparece en Battle.net.",
            r"^[^\s#]{3,12}#\d{4,6}$", "games/shop_icons/battlenet.svg",
        ),
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="platform_accounts")
    platform = models.CharField("plataforma", max_length=20, choices=Platform.choices)
    handle = models.CharField("nombre o código", max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["platform"]
        constraints = [
            models.UniqueConstraint(fields=["user", "platform"], name="unique_platform_account_per_user"),
        ]
        verbose_name = "cuenta de otra plataforma"
        verbose_name_plural = "cuentas de otras plataformas"

    def __str__(self):
        """Texto de la cuenta en el admin: «Plataforma: nombre»."""
        return f"{self.get_platform_display()}: {self.handle}"

    @property
    def logo_template(self):
        """Ruta de la plantilla SVG con el logo de la plataforma."""
        return self.RULES[self.platform][3]

    @property
    def url(self):
        """Perfil público en la web de la plataforma cuando existe (solo GOG); si no, vacío."""
        if self.platform == self.Platform.GOG:
            return f"https://www.gog.com/u/{self.handle}"
        return ""

    @classmethod
    def normalize(cls, platform, handle):
        """Limpia lo que escribe el usuario: quita espacios de los extremos y pone el código de Switch
        en formato SW-1234-5678-9012 aunque lo escriba solo con números o con espacios."""
        handle = " ".join((handle or "").split())
        if platform == cls.Platform.NINTENDO:
            digits = re.sub(r"\D", "", handle)
            if len(digits) == 12:
                handle = f"SW-{digits[:4]}-{digits[4:8]}-{digits[8:]}"
        return handle

    @classmethod
    def is_valid_handle(cls, platform, handle):
        """Indica si el nombre o código cumple el formato de esa plataforma."""
        return bool(re.match(cls.RULES[platform][2], handle or ""))


class XboxAccount(models.Model):
    """Cuenta de Xbox (cuenta Microsoft) vinculada a un usuario (una por usuario).

    No se guarda ningún token de Microsoft: para sincronizar el usuario vuelve a iniciar sesión.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="xbox_account")
    # XUID: ID de la cuenta en Xbox Live; único para que una cuenta no esté en dos usuarios de NextPlay.
    xuid = models.CharField("XUID", max_length=24, unique=True)
    gamertag = models.CharField("gamertag", max_length=32, blank=True)
    avatar = models.URLField("avatar de Xbox", max_length=500, blank=True)
    synced_at = models.DateTimeField("última importación", null=True, blank=True)
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "cuenta de Xbox"
        verbose_name_plural = "cuentas de Xbox"

    def __str__(self):
        """Texto de la cuenta en el admin: su gamertag."""
        return self.name

    @property
    def name(self):
        """Nombre que se muestra: el gamertag, o «Xbox» si Microsoft no lo ha devuelto."""
        return self.gamertag or "Xbox"

    @property
    def url(self):
        """Perfil público del gamertag en xbox.com, o vacío si no se conoce."""
        return f"https://www.xbox.com/es-ES/play/user/{self.gamertag}" if self.gamertag else ""


class ExternalTitle(models.Model):
    """Juego tal como lo identifica una plataforma (app de Steam, titleId de PlayStation o de Xbox) y el
    juego del catálogo que le corresponde (vacío si no se encontró).

    Es compartido entre usuarios: la importación lo mira antes de buscar por nombre, así que una
    corrección hecha a mano sirve para todos. Un administrador puede bloquearlo para que nadie más lo cambie.
    """

    class Platform(models.TextChoices):
        STEAM = "steam", "Steam"
        PSN = "psn", "PlayStation"
        XBOX = "xbox", "Xbox"

    class Source(models.TextChoices):
        AUTO = "auto", "Automático"
        USER = "user", "Usuario"
        ADMIN = "admin", "Administrador"

    platform = models.CharField("plataforma", max_length=10, choices=Platform.choices)
    external_id = models.CharField("ID en la plataforma", max_length=64)
    name = models.CharField("nombre en la plataforma", max_length=255)
    game = models.ForeignKey(
        "games.Game", on_delete=models.SET_NULL, null=True, blank=True, related_name="external_titles",
        verbose_name="juego",
    )
    # Las importaciones solo vuelven a buscar los automáticos sin juego; lo puesto a mano se respeta.
    source = models.CharField("emparejado por", max_length=10, choices=Source.choices, default=Source.AUTO)
    locked = models.BooleanField(
        "bloqueado", default=False, help_text="Los usuarios ya no pueden cambiar el juego de este título."
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="asignado por",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["platform", "external_id"], name="unique_external_title"),
        ]
        verbose_name = "título importado"
        verbose_name_plural = "títulos importados"

    def __str__(self):
        """Texto del título en el admin: «Plataforma: nombre»."""
        return f"{self.get_platform_display()}: {self.name}"


class ImportedTitle(models.Model):
    """Título que tiene un usuario en una plataforma, con los minutos y la última partida de su última importación.

    De aquí salen las horas de cada plataforma en la biblioteca, así que al corregir el juego de un
    título se pueden recolocar sin volver a importar.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="imported_titles")
    title = models.ForeignKey(ExternalTitle, on_delete=models.CASCADE, related_name="imports")
    # Juego al que se pasaron sus horas en la biblioteca la última vez: si el título cambia de juego, se le
    # quitan a este. Vacío hasta la primera vez (y en los títulos sin juego).
    game = models.ForeignKey("games.Game", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    minutes = models.PositiveIntegerField("minutos jugados", default=0)
    last_played = models.DateTimeField("última partida", null=True, blank=True)
    # Primera partida (solo la da PlayStation) y plataformas en las que se jugó, como nombres de plataforma de
    # IGDB («PlayStation 5», «Xbox Series X|S»...). Rellenan solas la plataforma y las fechas de las reseñas.
    first_played = models.DateTimeField("primera partida", null=True, blank=True)
    platforms = models.JSONField("plataformas en las que se jugó", default=list, blank=True)
    # De los minutos, los jugados en una Steam Deck (solo lo da Steam): si son la mayoría, la reseña dice Steam Deck.
    deck_minutes = models.PositiveIntegerField("minutos en Steam Deck", default=0)
    # El usuario lo quita de «Sin emparejar» (herramientas, demos, apps...).
    hidden = models.BooleanField("oculto", default=False)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title__name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "title"], name="unique_imported_title_per_user"),
        ]
        verbose_name = "título de un usuario"
        verbose_name_plural = "títulos de los usuarios"

    def __str__(self):
        """Texto en el admin: «usuario · título»."""
        return f"{self.user} · {self.title}"

    @property
    def hours(self):
        """Horas jugadas redondeadas a una décima."""
        return round(self.minutes / 60, 1)


class Notification(models.Model):
    """Aviso para un usuario: se ve en la campana de la cabecera hasta que abre la página de avisos."""

    class Kind(models.TextChoices):
        PRICE = "price", "Bajada de precio"
        LIKE = "like", "Me gusta en tu reseña"
        COMMENT = "comment", "Comentario en tu reseña"
        REVIEW_UPDATE = "review_update", "Actualizar una reseña"
        FOLLOW = "follow", "Nuevo seguidor"
        POST_LIKE = "post_like", "Me gusta en tu publicación"
        POST_COMMENT = "post_comment", "Comentario en tu publicación"
        # Solo para los administradores.
        REPORT = "report", "Denuncia"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField("tipo", max_length=20, choices=Kind.choices)
    text = models.CharField("texto", max_length=300)
    url = models.CharField("enlace", max_length=500, blank=True)
    game = models.ForeignKey(
        "games.Game", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="juego"
    )
    # Quién lo ha provocado (el nuevo seguidor o quien ha dado «me gusta» a una publicación o la ha comentado), para
    # enseñar su foto. Se borra con su cuenta.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+",
        verbose_name="quién",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField("leído", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"])]
        verbose_name = "aviso"
        verbose_name_plural = "avisos"

    def __str__(self):
        """Texto del aviso en el admin: «usuario · texto»."""
        return f"{self.user} · {self.text}"
