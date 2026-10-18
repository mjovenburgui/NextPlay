import re

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse


class User(AbstractUser):
    # Custom from day one: swapping the user model after the first migration is painful.
    email = models.EmailField("correo electrónico", unique=True)


class Profile(models.Model):
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
    price_alert_emails = models.BooleanField(
        "avisarme también por correo",
        default=True,
        help_text="Cuando un juego de tu lista de deseos baje de tu precio o llegue a su mínimo histórico.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

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
        """Indica si viewer puede ver el perfil: siempre si es público, y si es privado solo su dueño."""
        return self.is_public or viewer == self.user

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
        """Avatar del perfil: el de Steam, si no el de PlayStation, si no el de Xbox; vacío para usar la inicial."""
        return (
            self.steam_avatar
            or (self.psn.avatar if self.psn else "")
            or (self.xbox.avatar if self.xbox else "")
        )


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

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField("tipo", max_length=20, choices=Kind.choices)
    text = models.CharField("texto", max_length=300)
    url = models.CharField("enlace", max_length=500, blank=True)
    game = models.ForeignKey(
        "games.Game", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="juego"
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
