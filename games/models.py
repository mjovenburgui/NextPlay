"""Modelos del catálogo: cada juego (con lo que trae de IGDB y de las tiendas: portadas, idiomas, IDs de tienda,
imágenes, tipo, duración y ficha técnica), sus géneros, plataformas, modos de juego, temas, perspectivas, estudios y
editoras, y sus precios por tienda. Salen en casi todas las pantallas, sobre todo en el Catálogo y en la Ficha del juego.
"""

from urllib.parse import quote

from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

# Nombres en español de los géneros de IGDB, por su slug: IGDB solo los da en inglés.
GENRE_NAMES_ES = {
    "adventure": "Aventura",
    "arcade": "Arcade",
    "card-and-board-game": "Cartas y tablero",
    "fighting": "Lucha",
    "hack-and-slash-beat-em-up": "Hack and slash / Beat 'em up",
    "indie": "Indie",
    "moba": "MOBA",
    "music": "Música",
    "pinball": "Pinball",
    "platform": "Plataformas",
    "point-and-click": "Aventura gráfica",
    "puzzle": "Puzles",
    "quiz-trivia": "Preguntas y trivial",
    "racing": "Carreras",
    "real-time-strategy-rts": "Estrategia en tiempo real",
    "role-playing-rpg": "Rol (RPG)",
    "shooter": "Disparos",
    "simulator": "Simulación",
    "sport": "Deportes",
    "strategy": "Estrategia",
    "tactical": "Táctico",
    "turn-based-strategy-tbs": "Estrategia por turnos",
    "visual-novel": "Novela visual",
}


def spanish_genre_name(name, slug=""):
    """Nombre en español de un género de IGDB; si no está en GENRE_NAMES_ES, el original.

    Pantallas: Catálogo (filtro de género), Ficha del juego (géneros), Inicio (géneros), Nuevo reto (géneros), Perfil
    (géneros favoritos del escaparate).
    """
    return GENRE_NAMES_ES.get(slug or slugify(name), name)


IGDB_IMAGE_URL = "https://images.igdb.com/igdb/image/upload/{size}/{image_id}.{ext}"


def igdb_image_url(image_id, size="t_cover_big", ext="jpg"):
    """URL de una imagen del CDN de IGDB (portada, arte o captura) en ese tamaño; vacía si no hay imagen.

    Con ext «png» conserva la transparencia, que hace falta en los logos.

    Pantallas: Inicio, Catálogo, Ficha del juego (cabecera y carrusel), Listas del juego, Perfil (cabecera con la
    imagen de un juego), Lista de deseos, Escribir reseña (imágenes de la ficha), Página de la reseña,
    Página de la publicación, Página de la lista, Editar lista, Nueva tier list, Página del reto, Avisos,
    Juegos importados, Editar perfil (las portadas de los juegos).
    """
    return IGDB_IMAGE_URL.format(size=size, image_id=image_id, ext=ext) if image_id else ""


# Nombre en español de cada idioma, por el código que usan IGDB y games.spanish (los de Steam se traducen a él).
LANGUAGE_NAMES_ES = {
    "es-ES": "Español (España)", "es-MX": "Español (Latinoamérica)", "en-US": "Inglés", "en-GB": "Inglés (Reino Unido)",
    "fr-FR": "Francés", "de-DE": "Alemán", "it-IT": "Italiano", "pt-PT": "Portugués (Portugal)",
    "pt-BR": "Portugués (Brasil)", "ja-JP": "Japonés", "ko-KR": "Coreano", "zh-CN": "Chino simplificado",
    "zh-TW": "Chino tradicional", "ru-RU": "Ruso", "pl-PL": "Polaco", "uk-UA": "Ucraniano", "cs-CZ": "Checo",
    "hu-HU": "Húngaro", "tr-TR": "Turco", "ar": "Árabe", "nl-NL": "Neerlandés", "sv-SE": "Sueco", "da-DK": "Danés",
    "fi-FI": "Finés", "nb-NO": "Noruego", "th-TH": "Tailandés", "vi-VN": "Vietnamita", "he-IL": "Hebreo",
    "el-GR": "Griego", "ro-RO": "Rumano", "bg-BG": "Búlgaro", "id-ID": "Indonesio",
}
# Idiomas que van primero en la tabla de la ficha; el resto, por nombre.
LANGUAGES_FIRST = ("es-ES", "es-MX", "en-US", "en-GB")


class Spanish(models.TextChoices):
    """Cuánto español tiene un juego en voces o en textos. Vacío = no se sabe (ninguna fuente da sus idiomas)."""

    SPAIN = "es", "Castellano"
    LATAM = "latam", "Español de Latinoamérica"
    NONE = "no", "Sin español"


# Filtro de idioma del catálogo, la biblioteca y la lista de deseos: valor de ?idioma= → etiqueta.
LANGUAGE_FILTERS = {
    "voces": "Voces en castellano",
    "textos": "Textos en castellano",
    "espanol": "Algo en español (también latino)",
}


def language_filter(key, prefix=""):
    """Condición (Q) del filtro de idioma ?idioma=key, o None si key no es uno de LANGUAGE_FILTERS.

    prefix va delante de los campos para filtrar desde otro modelo (p. ej. «game__» en la lista de
    deseos). Con voces en castellano también se cuentan los textos: si está doblado, está traducido.

    Pantallas: Catálogo (filtro de idioma), Perfil (filtro de idioma de la biblioteca), Lista de deseos (filtro de
    idioma).
    """
    audio, text = f"{prefix}spanish_audio", f"{prefix}spanish_text"
    spanish = [Spanish.SPAIN, Spanish.LATAM]
    return {
        "voces": models.Q(**{audio: Spanish.SPAIN}),
        "textos": models.Q(**{text: Spanish.SPAIN}) | models.Q(**{audio: Spanish.SPAIN}),
        "espanol": models.Q(**{f"{text}__in": spanish}) | models.Q(**{f"{audio}__in": spanish}),
    }.get(key)


def search_filter(query):
    """Condición (Q) de la búsqueda de juegos por nombre: el de IGDB, el de España y sus otros nombres (siglas como «tw3»).

    Pantallas: Catálogo (búsqueda por nombre, también desde la barra de arriba), Editar lista (buscar juegos para
    añadir).
    """
    return models.Q(name__icontains=query) | models.Q(name_es__icontains=query) | models.Q(search_names__icontains=query)


class Genre(models.Model):
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        """Nombre del género.

        Pantallas: Panel de administración, Catálogo (filtro de género), Ficha del juego (géneros).
        """
        return self.name


# Plataforma propia, que no está en IGDB, para las reseñas de lo jugado en una Steam Deck (la crea una migración).
# Se ofrece en los juegos de PC, que son los que se pueden jugar en ella.
STEAM_DECK_SLUG = "steam-deck"
STEAM_DECK_NAME = "Steam Deck"
STEAM_DECK_HOSTS = ("PC (Microsoft Windows)", "Linux")


class Platform(models.Model):
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=100)
    abbreviation = models.CharField(max_length=30, blank=True)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        """Nombre completo de la plataforma.

        Pantallas: Panel de administración, Catálogo (filtro de plataforma), Nuevo reto (plataformas).
        """
        return self.name

    @property
    def short_name(self):
        """Nombre corto para las etiquetas (p. ej. «PS5»); el completo si no tiene abreviatura.

        Pantallas: Ficha del juego (plataformas de la cabecera), Perfil (plataforma de cada juego),
        Página de la reseña (plataforma de la partida), Juegos importados.
        """
        return self.abbreviation or self.name


# Modos de juego, temas y perspectivas de IGDB con su nombre en español, por su slug de IGDB: IGDB solo los da en inglés.
# Son listas cortas y fijas (endpoints game_modes, themes y player_perspectives).
MODE_NAMES_ES = {
    "single-player": "Un jugador",
    "multiplayer": "Multijugador",
    "co-operative": "Cooperativo",
    "split-screen": "Pantalla partida",
    "massively-multiplayer-online-mmo": "Multijugador masivo (MMO)",
    "battle-royale": "Battle royale",
}
THEME_NAMES_ES = {
    "action": "Acción",
    "fantasy": "Fantasía",
    "science-fiction": "Ciencia ficción",
    "horror": "Terror",
    "thriller": "Suspense",
    "survival": "Supervivencia",
    "historical": "Histórico",
    "stealth": "Sigilo",
    "comedy": "Comedia",
    "business": "Negocios",
    "drama": "Drama",
    "non-fiction": "No ficción",
    "sandbox": "Sandbox",
    "educational": "Educativo",
    "kids": "Infantil",
    "open-world": "Mundo abierto",
    "warfare": "Bélico",
    "party": "Juegos de fiesta",
    "4x-explore-expand-exploit-and-exterminate": "4X",
    "erotic": "Erótico",
    "mystery": "Misterio",
    "romance": "Romance",
}
PERSPECTIVE_NAMES_ES = {
    "first-person": "Primera persona",
    "third-person": "Tercera persona",
    "bird-view-slash-isometric": "Vista cenital o isométrica",
    "side-view": "Vista lateral",
    "text": "Texto",
    "auditory": "Solo sonido",
    "virtual-reality": "Realidad virtual",
}
# IDs de IGDB de los modos de los juegos que no se acaban: multijugador masivo y battle royale.
ENDLESS_MODE_IDS = (5, 6)
# ID de IGDB del modo multijugador.
MULTIPLAYER_MODE_ID = 2


class IGDBTag(models.Model):
    """Base de las etiquetas que vienen de IGDB (modos, temas, perspectivas y compañías): su id de IGDB, su nombre en
    español y el slug con el que se filtra el Catálogo."""

    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)

    class Meta:
        abstract = True

    def __str__(self):
        """Nombre de la etiqueta.

        Pantallas: Panel de administración, Catálogo (filtros de modo, tema, perspectiva y estudio), Ficha del juego
        (ficha técnica).
        """
        return self.name


class GameMode(IGDBTag):
    """Modo de juego de IGDB: un jugador, multijugador, cooperativo, pantalla partida, MMO o battle royale."""

    class Meta:
        ordering = ["igdb_id"]
        verbose_name = "modo de juego"
        verbose_name_plural = "modos de juego"


class Theme(IGDBTag):
    """Tema de IGDB (terror, ciencia ficción, mundo abierto...): dice más del juego que los géneros."""

    class Meta:
        ordering = ["name"]
        verbose_name = "tema"
        verbose_name_plural = "temas"


class Perspective(IGDBTag):
    """Perspectiva de IGDB: primera persona, tercera persona, vista cenital, lateral..."""

    class Meta:
        ordering = ["igdb_id"]
        verbose_name = "perspectiva"
        verbose_name_plural = "perspectivas"


class Company(IGDBTag):
    """Estudio o editora de IGDB (involved_companies)."""

    class Meta:
        ordering = ["name"]
        verbose_name = "compañía"
        verbose_name_plural = "compañías"


class GameType(models.IntegerChoices):
    """Tipo de juego en IGDB (endpoint game_types): juego, DLC, remake, edición ampliada..."""

    MAIN = 0, "Juego"
    DLC = 1, "DLC"
    EXPANSION = 2, "Expansión"
    BUNDLE = 3, "Recopilatorio"
    STANDALONE_EXPANSION = 4, "Expansión independiente"
    MOD = 5, "Mod"
    EPISODE = 6, "Episodio"
    SEASON = 7, "Temporada"
    REMAKE = 8, "Remake"
    REMASTER = 9, "Remaster"
    EXPANDED = 10, "Edición ampliada"
    PORT = 11, "Port"
    FORK = 12, "Versión derivada"
    PACK = 13, "Pack"
    UPDATE = 14, "Actualización"


class GameStatus(models.IntegerChoices):
    """Estado de un juego en IGDB (endpoint game_statuses). Casi todos los publicados lo tienen vacío."""

    RELEASED = 0, "Publicado"
    ALPHA = 2, "En alfa"
    BETA = 3, "En beta"
    EARLY_ACCESS = 4, "En acceso anticipado"
    OFFLINE = 5, "Servidores cerrados"
    CANCELLED = 6, "Cancelado"
    RUMORED = 7, "Rumor"
    DELISTED = 8, "Ya no se vende"


# Tipos que nunca entran al catálogo desde las búsquedas de IGDB: DLC sueltos (trajes...), packs y actualizaciones.
ADDON_TYPES = (GameType.DLC, GameType.PACK, GameType.UPDATE)
# Tipos que el Catálogo esconde si no se piden con su filtro: lo que necesita otro juego o es un trozo de uno.
HIDDEN_TYPES = (
    GameType.DLC, GameType.EXPANSION, GameType.MOD, GameType.EPISODE, GameType.SEASON, GameType.PACK, GameType.UPDATE,
)
# Lo que las tiendas de consola venden como complemento de un juego, no como juego: sus buscadores solo miran juegos,
# así que buscarlos por el título nunca da con ellos (xbox.update_xbox_prices y nintendo.update_nintendo_prices no lo
# hacen).
STORE_ADDON_TYPES = (GameType.DLC, GameType.EXPANSION, GameType.PACK, GameType.UPDATE)
# Duración: con menos respuestas en IGDB es un dato orientativo; para contar con ella (lo que te falta, los filtros
# y la pista del repaso) hacen falta al menos DURATION_USABLE_ANSWERS. Por encima de ENDLESS_HOURS, el juego no se
# acaba o los jugadores apuntan todo lo que llevan, así que tampoco cuenta.
DURATION_MIN_ANSWERS = 5
DURATION_USABLE_ANSWERS = 2
ENDLESS_HOURS = 200


def score_tier(score):
    """Franja de una nota de 0 a 100 para colorearla: «high» (75 o más), «mid» (50-74), «low» o vacío sin nota.

    Pantallas: Ficha del juego (color de la nota de la crítica y de la de los usuarios de IGDB).
    """
    if score is None:
        return ""
    return "high" if score >= 75 else "mid" if score >= 50 else "low"


def half_hours(minutes):
    """Minutos en horas redondeadas a la media hora (870 → 14.5), o None si no hay minutos.

    Pantallas: Ficha del juego (Cuánto dura).
    """
    return round(minutes / 30) / 2 if minutes else None


class Game(models.Model):
    # Null for games that did not come from IGDB (demo seed, manual entries).
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=255, unique=True)
    summary = models.TextField(blank=True)
    cover_image_id = models.CharField(max_length=64, blank=True)
    first_release_date = models.DateField(null=True, blank=True)
    # IGDB total_rating_count, used only as a popularity signal for ordering.
    popularity = models.PositiveIntegerField(default=0, db_index=True)
    # IGDB aggregated_rating: average of external critic scores, 0-100.
    critic_rating = models.FloatField(null=True, blank=True)
    critic_rating_count = models.PositiveIntegerField(default=0)
    steam_app_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    # Set by hand when IGDB's Steam ID is wrong or delisted; the import never touches it.
    steam_app_id_override = models.PositiveIntegerField(
        "ID de Steam manual",
        null=True,
        blank=True,
        help_text="Sustituye al ID de Steam de IGDB para buscar precios (p. ej. 3240220 para GTA V Enhanced).",
    )
    # Store IDs of the game itself from IGDB: {"steam": [...], "gog": [...]}. A game can have several
    # (e.g. an original release and a re-release); their deals are merged.
    store_ids = models.JSONField(default=dict, blank=True)
    # Editions and remasters from IGDB ([{"name", "steam", "gog"}]), tried in order when the game has no deals.
    store_alternatives = models.JSONField(default=list, blank=True)
    # Microsoft Store product IDs from IGDB (own first, then editions/remasters), for Xbox prices.
    xbox_product_ids = models.JSONField(default=list, blank=True)
    # Set by hand when the product sold on Xbox is not among IGDB's; the import never touches it.
    xbox_product_id_override = models.CharField(
        "ID de Xbox manual",
        max_length=12,
        blank=True,
        help_text=(
            "ID de producto de la tienda de Xbox (lo último de la URL de xbox.com, p. ej. 9NTS053TCCZ8). "
            "Sustituye a los de IGDB y a la búsqueda por nombre."
        ),
    )
    # NSUID (ID de la eShop) del juego de Switch o Switch 2 en la tienda europea: update_prices lo busca por
    # el nombre y lo guarda para no volver a buscarlo. IGDB no lo tiene.
    nintendo_nsuid = models.CharField("NSUID de Nintendo", max_length=14, blank=True)
    # Se pone a mano cuando la búsqueda no lo encuentra o encuentra otro juego; update_prices no lo toca.
    nintendo_nsuid_override = models.CharField(
        "NSUID de Nintendo manual",
        max_length=14,
        blank=True,
        validators=[RegexValidator(r"^\d{14}$", "El NSUID son 14 cifras.")],
        help_text=(
            "NSUID del juego en la eShop europea: 14 cifras que empiezan por 7001 (p. ej. 70010000003207 para "
            "Hollow Knight). Sale como «nsuid» en el código de su página de nintendo.com/es-es. Sustituye al "
            "que se encuentra buscando por el nombre."
        ),
    )
    # Name of the edition whose prices are shown, empty when they are the game's own.
    prices_edition = models.CharField(max_length=255, blank=True)
    historical_low = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    historical_low_currency = models.CharField(max_length=3, blank=True)
    prices_updated_at = models.DateTimeField(null=True, blank=True)
    # UUID de IsThereAnyDeal de los juegos con los que se encontraron los precios de PC (para pedir su historial).
    itad_ids = models.JSONField(default=list, blank=True)
    # Historial de precios de PC por tienda (formato en games.history), de IsThereAnyDeal.
    price_history = models.JSONField(default=dict, blank=True)
    price_history_updated_at = models.DateTimeField(null=True, blank=True)
    # Castellano (ver games.spanish). Idiomas de cada fuente por separado, para no perder los de una al
    # actualizar la otra: {"igdb": {código: ["audio", "text"]}, "steam": {...}}; «text» = interfaz o subtítulos.
    languages = models.JSONField(default=dict, blank=True)
    # Resumen del español de languages para filtrar con una consulta normal (SQLite no busca dentro de un JSONField).
    spanish_audio = models.CharField(
        "voces en español", max_length=5, choices=Spanish.choices, blank=True, db_index=True
    )
    spanish_text = models.CharField(
        "textos en español", max_length=5, choices=Spanish.choices, blank=True, db_index=True
    )
    name_es = models.CharField("nombre en España", max_length=255, blank=True)
    # Otros nombres del juego en minúsculas (siglas, títulos de otros países...), para que el buscador los encuentre.
    search_names = models.TextField("otros nombres", blank=True)
    cover_eu_image_id = models.CharField("portada europea", max_length=64, blank=True)
    summary_es = models.TextField("sinopsis en español", blank=True)
    summary_es_source = models.CharField("de dónde sale la sinopsis", max_length=10, blank=True)
    # Última vez que se pidieron a Steam y a Microsoft la sinopsis y los idiomas (comando localize_games).
    localized_at = models.DateTimeField("textos de las tiendas pedidos", null=True, blank=True)
    # Artes, capturas, logos y vídeos de IGDB para la cabecera y el carrusel de la ficha (formato en games.media).
    media = models.JSONField("imágenes y vídeos", default=dict, blank=True)
    media_updated_at = models.DateTimeField("imágenes y vídeos pedidos", null=True, blank=True)
    # Tipo y estado en IGDB (GameType y GameStatus); vacíos si el juego no viene de IGDB o aún no se han pedido.
    game_type = models.PositiveSmallIntegerField("tipo", choices=GameType.choices, null=True, blank=True, db_index=True)
    game_status = models.PositiveSmallIntegerField("estado", choices=GameStatus.choices, null=True, blank=True)
    # Si es una edición de otro juego («Game of the Year Edition», «Deluxe Edition»...: version_parent en IGDB), ese
    # juego y el nombre de la edición. Las ediciones no salen en el Catálogo, su ficha lleva a la del juego y lo que la
    # gente tiene de ellas se junta con el juego (comando merge_editions). También se puede poner a mano en el admin.
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="editions",
        verbose_name="es una edición de",
    )
    edition_name = models.CharField("nombre de la edición", max_length=255, blank=True)
    # Nota de los usuarios de IGDB (rating, de 0 a 100) y cuántos han votado.
    igdb_rating = models.FloatField("nota de los usuarios de IGDB", null=True, blank=True)
    igdb_rating_count = models.PositiveIntegerField("votos de los usuarios de IGDB", default=0)
    # Cuánto dura según los jugadores de IGDB (game_time_to_beats), en minutos: con prisa, normal y al 100 %, y
    # cuántos han respondido.
    ttb_quick = models.PositiveIntegerField("duración con prisa", null=True, blank=True)
    ttb_normal = models.PositiveIntegerField("duración normal", null=True, blank=True, db_index=True)
    ttb_full = models.PositiveIntegerField("duración al 100 %", null=True, blank=True)
    ttb_count = models.PositiveIntegerField("respuestas de la duración", default=0)
    # Lo que la ficha solo enseña, sin filtrar por ello (formato en games.details): PEGI, sagas, motor, cooperativo,
    # ediciones, DLC, remakes, ports, recopilatorios y juegos parecidos.
    details = models.JSONField("ficha técnica", default=dict, blank=True)
    details_updated_at = models.DateTimeField("ficha técnica pedida", null=True, blank=True)
    genres = models.ManyToManyField(Genre, related_name="games", blank=True)
    platforms = models.ManyToManyField(Platform, related_name="games", blank=True)
    modes = models.ManyToManyField(GameMode, related_name="games", blank=True, verbose_name="modos de juego")
    themes = models.ManyToManyField(Theme, related_name="games", blank=True, verbose_name="temas")
    perspectives = models.ManyToManyField(Perspective, related_name="games", blank=True, verbose_name="perspectivas")
    developers = models.ManyToManyField(Company, related_name="developed", blank=True, verbose_name="estudios")
    publishers = models.ManyToManyField(Company, related_name="published", blank=True, verbose_name="editoras")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-popularity", "name"]

    def __str__(self):
        """Nombre del juego.

        Pantallas: Panel de administración, Escribir reseña (el juego elegido en los formularios).
        """
        return self.name

    def get_absolute_url(self):
        """URL de la ficha del juego (/juegos/<slug>/).

        Pantallas: Inicio, Catálogo, Ficha del juego, Listas del juego, Perfil, Lista de deseos, Escribir reseña,
        Página de la reseña, Página de la publicación, Página de la lista, Editar lista, Página del reto,
        Juegos importados (los enlaces a la ficha de cada juego).
        """
        return reverse("games:detail", args=[self.slug])

    def review_platforms(self):
        """Plataformas que se pueden elegir en una reseña del juego: las suyas y, si sale en PC, la Steam Deck.

        Pantallas: Escribir reseña (plataformas que se pueden elegir).
        """
        query = models.Q(games=self)
        if any(platform.name in STEAM_DECK_HOSTS for platform in self.platforms.all()):
            query |= models.Q(slug=STEAM_DECK_SLUG)
        return Platform.objects.filter(query).distinct()

    @property
    def cover_url(self):
        """URL de la portada en el CDN de IGDB (la europea si IGDB la tiene), o cadena vacía si no hay portada.

        Pantallas: Inicio, Catálogo, Ficha del juego, Listas del juego, Perfil, Lista de deseos, Escribir reseña,
        Página de la reseña, Página de la publicación, Página de la lista, Editar lista, Nueva tier list,
        Página del reto, Avisos, Juegos importados, Editar perfil (las portadas de los juegos).
        """
        return igdb_image_url(self.cover_eu_image_id or self.cover_image_id)

    def merged_languages(self):
        """Idiomas de todas las fuentes juntos: {código: {"audio", "text"}}. Si una fuente dice que tiene voces, las tiene.

        Pantallas: Ficha del juego (nombre en España, sinopsis en español e idiomas), Catálogo (búsqueda por nombre en
        España y filtro de idioma), Perfil (filtro de idioma de la biblioteca), Lista de deseos (filtro de idioma).
        """
        merged = {}
        for source in (self.languages or {}).values():
            for code, kinds in (source or {}).items():
                merged.setdefault(code, set()).update(kinds)
        return merged

    def refresh_spanish(self):
        """Recalcula spanish_audio y spanish_text con los idiomas de languages (sin guardar el juego).

        Castellano si está el español de España, latino si solo está el de México y «sin español» si
        hay idiomas pero ninguno es español; vacío si ninguna fuente da idiomas. Las voces cuentan
        también como textos.

        Pantallas: Ficha del juego (nombre en España, sinopsis en español e idiomas), Catálogo (búsqueda por nombre en
        España y filtro de idioma), Perfil (filtro de idioma de la biblioteca), Lista de deseos (filtro de idioma).
        """
        merged = self.merged_languages()

        def level(kinds):
            """Nivel de español con esos tipos de soporte (voces, o voces y textos)."""
            if kinds & merged.get("es-ES", set()):
                return Spanish.SPAIN
            if kinds & merged.get("es-MX", set()):
                return Spanish.LATAM
            return Spanish.NONE if merged else ""

        self.spanish_audio = level({"audio"})
        self.spanish_text = level({"audio", "text"})

    @property
    def spain_name(self):
        """Nombre con el que se vende en España si es distinto del original («Los Sims 4»); vacío si no.

        Pantallas: Ficha del juego (nombre en España de la cabecera).
        """
        return self.name_es if self.name_es and self.name_es.casefold() != self.name.casefold() else ""

    @property
    def language_sources(self):
        """Fuentes que han dado idiomas del juego, para citarlas en la ficha: p. ej. ["IGDB", "Steam"].

        Pantallas: Ficha del juego (de dónde salen los idiomas).
        """
        names = {"igdb": "IGDB", "steam": "Steam"}
        return [names.get(source, source) for source, values in (self.languages or {}).items() if values]

    @property
    def language_rows(self):
        """Idiomas para la tabla de la ficha: [(nombre en español, voces, textos)], con los españoles y el inglés primero.

        Pantallas: Ficha del juego (tabla de idiomas).
        """
        merged = self.merged_languages()

        def order(code):
            """Primero LANGUAGES_FIRST en su orden; después por nombre."""
            first = LANGUAGES_FIRST.index(code) if code in LANGUAGES_FIRST else len(LANGUAGES_FIRST)
            return first, LANGUAGE_NAMES_ES.get(code, code).casefold()

        return [
            (LANGUAGE_NAMES_ES.get(code, code), "audio" in merged[code], "text" in merged[code])
            for code in sorted(merged, key=order)
        ]

    @property
    def release_year(self):
        """Año de lanzamiento, o None si no se conoce la fecha.

        Pantallas: Catálogo, Inicio, Ficha del juego, Perfil, Página de la reseña, Página de la lista, Editar lista,
        Página del reto, Juegos importados (el año de cada juego).
        """
        return self.first_release_date.year if self.first_release_date else None

    @property
    def price_lookups(self):
        """Grupos ([(tienda, ID en la tienda), ...], nombre de edición) con los que buscar precios, por preferencia.

        Primero los IDs del propio juego y después los de sus ediciones y remasters. Los IDs de un
        mismo grupo son el mismo producto, así que sus ofertas se juntan. Si hay un ID de Steam
        manual, solo se usa ese.

        Pantallas: Ficha del juego (Dónde comprarlo: los IDs de tienda con los que se buscan), Lista de deseos (mejor
        precio de hoy y mínimo histórico), Página de la lista (lo que cuesta lo que te falta).
        """
        if self.steam_app_id_override:
            return [([("steam", self.steam_app_id_override)], "")]
        groups = [(self.store_ids, "")] + [(alt, alt["name"]) for alt in self.store_alternatives]
        lookups = []
        for ids, edition in groups:
            keys = [(store, store_id) for store in ("steam", "gog") for store_id in ids.get(store) or []]
            if keys:
                lookups.append((keys, edition))
        return lookups

    @property
    def xbox_lookup_ids(self):
        """IDs de producto de Xbox con los que buscar el precio: el manual si lo hay, si no los de IGDB.

        Pantallas: Ficha del juego (Dónde comprarlo: el precio de Xbox), Lista de deseos (mejor precio de hoy y mínimo
        histórico), Página de la lista (lo que cuesta lo que te falta).
        """
        return [self.xbox_product_id_override] if self.xbox_product_id_override else self.xbox_product_ids

    @property
    def on_current_xbox(self):
        """Indica si el juego está en Xbox One o Xbox Series X|S.

        Pantallas: Ficha del juego (Dónde comprarlo: Xbox).
        """
        return any(platform.name in CURRENT_XBOX_PLATFORMS for platform in self.platforms.all())

    @property
    def nintendo_lookup_nsuid(self):
        """NSUID con el que pedir el precio de la eShop: el manual si lo hay, si no el encontrado por el nombre.

        Pantallas: Ficha del juego (Dónde comprarlo: el precio de Nintendo), Lista de deseos (mejor precio de hoy y
        mínimo histórico), Página de la lista (lo que cuesta lo que te falta).
        """
        return self.nintendo_nsuid_override or self.nintendo_nsuid

    @property
    def best_price(self):
        """Oferta más barata de todas las tiendas (PC, Xbox y Nintendo), o None si no hay precios.

        Pantallas: Lista de deseos (mejor precio de hoy), Página de la lista (lo que cuesta lo que te falta).
        """
        return self.cheapest()

    def cheapest(self, shops=None):
        """Oferta más barata de esas tiendas (ids; None = todas), o None si ninguna lo vende.

        Pantallas: Ficha del juego (Dónde comprarlo: «Mejor precio ahora»), Lista de deseos (mejor precio de hoy en tus
        tiendas), Página de la lista (lo que cuesta lo que te falta), Avisos (bajadas de precio), Perfil (lo que cuesta
        el pendiente).
        """
        prices = sorted(
            (price for price in self.prices.all() if shops is None or price.shop_id in shops),
            key=lambda price: price.price,
        )
        return prices[0] if prices else None

    @property
    def is_free(self):
        """Indica si alguna tienda lo da gratis (precio y precio normal a 0), es decir, si es free-to-play.

        Pantallas: Ficha del juego (Dónde comprarlo: «Gratis»).
        """
        return any(price.is_free for price in self.prices.all())

    def price_groups(self, shops=None):
        """Reparte los precios de esas tiendas (ids; None = todas) para la ficha en (destacados, listados, plegados).

        destacados: las tiendas más conocidas (Steam, Epic, Microsoft, GOG, Xbox, Nintendo) en un orden fijo.
        listados: las demás más baratas, solo si el juego no está en ninguna destacada.
        plegados: el resto por precio, dentro de «Ver N tiendas más».
        La más barata, se vea o esté plegada, la da cheapest con las mismas tiendas.

        Pantallas: Ficha del juego (Dónde comprarlo: destacadas, el resto y las plegadas).
        """
        prices = [price for price in self.prices.all() if shops is None or price.shop_id in shops]
        featured_ids = list(FEATURED_SHOPS)
        featured = sorted((p for p in prices if p.is_featured), key=lambda p: featured_ids.index(p.shop_id))
        others = [p for p in prices if not p.is_featured]
        listed = [] if featured else others[:FALLBACK_SHOWN]
        folded = others[len(listed):]
        return featured, listed, folded

    def store_searches(self):
        """(tienda, plantilla del logo, URL de búsqueda) de las consolas en las que está el juego pero sin precio.

        Son PlayStation Store y, si no se encontró su precio, Nintendo eShop y Xbox Store.

        Pantallas: Ficha del juego (Dónde comprarlo: enlaces a buscarlo en las tiendas de consola sin precio).
        """
        platforms = {platform.name for platform in self.platforms.all()}
        priced = {price.shop_id for price in self.prices.all()}
        return [
            (store, f"games/shop_icons/{logo}.svg", url.format(quote(self.name)))
            for store, logo, consoles, url, shop_id in STORE_SEARCHES
            if platforms.intersection(consoles) and shop_id not in priced
        ]

    def community_rating(self):
        """Media de las valoraciones de los usuarios de NextPlay: {"average": de 0,5 a 5 estrellas o None, "count": n},
        y la misma solo con quienes tienen horas importadas de Steam, PlayStation o Xbox («verified_average» y
        «verified_count»), que no pueden valorarlo sin haberlo jugado.

        Las notas se guardan en medias estrellas (de 1 a 10), así que las medias se dividen entre dos.

        Pantallas: Ficha del juego (valoración de la comunidad).
        """
        played = (
            models.Q(steam_playtime_minutes__gt=0) | models.Q(psn_playtime_minutes__gt=0)
            | models.Q(xbox_playtime_minutes__gt=0)
        )
        result = self.library_entries.filter(rating__isnull=False).aggregate(
            average=models.Avg("rating"),
            count=models.Count("id"),
            verified_average=models.Avg("rating", filter=played),
            verified_count=models.Count("id", filter=played),
        )
        for key in ("average", "verified_average"):
            if result[key] is not None:
                result[key] /= 2
        return result

    @property
    def critic_score(self):
        """Nota de la crítica redondeada (0-100), o None si no tiene.

        Pantallas: Ficha del juego (nota de la crítica).
        """
        return round(self.critic_rating) if self.critic_rating is not None else None

    @property
    def critic_tier(self):
        """Franja de la nota para colorearla: «high» (75 o más), «mid» (50-74), «low» o vacío sin nota.

        Pantallas: Ficha del juego (color de la nota de la crítica).
        """
        return score_tier(self.critic_score)

    @property
    def igdb_score(self):
        """Nota de los usuarios de IGDB redondeada (0-100), o None si no tiene.

        Pantallas: Ficha del juego (nota de los jugadores de IGDB).
        """
        return round(self.igdb_rating) if self.igdb_rating is not None else None

    @property
    def igdb_tier(self):
        """Franja de la nota de los usuarios de IGDB para colorearla, como critic_tier.

        Pantallas: Ficha del juego (color de la nota de los jugadores de IGDB).
        """
        return score_tier(self.igdb_score)

    @property
    def type_label(self):
        """Tipo del juego para su etiqueta («Remake», «Expansión», «Edición ampliada»...), o vacío si es un juego normal
        o no se sabe.

        Pantallas: Catálogo (etiqueta de las tarjetas), Ficha del juego (de qué es remake, DLC... en la cabecera).
        """
        if self.game_type in (None, GameType.MAIN) or self.game_type not in GameType.values:
            return ""
        return GameType(self.game_type).label

    @property
    def status_label(self):
        """Estado que conviene saber antes de comprarlo («Ya no se vende», «Servidores cerrados», «En acceso
        anticipado»...), o vacío si está publicado o no se sabe.

        Pantallas: Ficha del juego (cabecera y ficha técnica).
        """
        if self.game_status in (None, GameStatus.RELEASED) or self.game_status not in GameStatus.values:
            return ""
        return GameStatus(self.game_status).label

    @property
    def main(self):
        """El juego que cuenta: si es una edición de otro, ese otro; si no, él mismo.

        Pantallas: Ficha del juego (la de una edición lleva a la de su juego), Juegos importados, Perfil (biblioteca y
        horas: lo importado de una edición va al juego), Lista de deseos (lo que se trae de Steam).
        """
        return self.parent if self.parent_id else self

    @property
    def duration(self):
        """Cuánto dura según los jugadores de IGDB, para la ficha: {"normal", "quick", "full" (horas, redondeadas a la
        media hora; None las que no hay), "count" (cuántos han respondido), "rough" (menos de DURATION_MIN_ANSWERS
        respuestas: dato orientativo) y "endless" (más de ENDLESS_HOURS: no se acaba)}, o None si no hay datos.

        Pantallas: Ficha del juego (Cuánto dura).
        """
        if not (self.ttb_normal or self.ttb_quick or self.ttb_full):
            return None
        return {
            "normal": half_hours(self.ttb_normal),
            "quick": half_hours(self.ttb_quick),
            "full": half_hours(self.ttb_full),
            "count": self.ttb_count,
            "rough": self.ttb_count < DURATION_MIN_ANSWERS,
            "endless": bool(self.ttb_normal and self.ttb_normal > ENDLESS_HOURS * 60),
        }

    @property
    def normal_hours(self):
        """Horas que suele durar (la duración normal de IGDB) si es un dato con el que contar: al menos
        DURATION_USABLE_ANSWERS respuestas y no más de ENDLESS_HOURS; None si no.

        Pantallas: Ficha del juego (Cuánto dura: lo que te falta), Repasar la biblioteca (pista «suele durar…»).
        """
        if not self.ttb_normal or self.ttb_count < DURATION_USABLE_ANSWERS or self.ttb_normal > ENDLESS_HOURS * 60:
            return None
        return self.ttb_normal / 60

    @property
    def is_endless(self):
        """Indica si es un juego que no se acaba: multijugador masivo o battle royale, o los jugadores de IGDB apuntan
        más de ENDLESS_HOURS (con al menos DURATION_USABLE_ANSWERS respuestas).

        Pantallas: Repasar la biblioteca (los juegos sin final van al fondo y sin «¿lo terminaste?»).
        """
        if self.ttb_normal and self.ttb_count >= DURATION_USABLE_ANSWERS and self.ttb_normal > ENDLESS_HOURS * 60:
            return True
        return any(mode.igdb_id in ENDLESS_MODE_IDS for mode in self.modes.all())

    @property
    def historical_low_symbol(self):
        """Símbolo de la moneda del mínimo histórico (€, $, £...).

        Pantallas: Ficha del juego (mínimo histórico), Lista de deseos (mínimo histórico).
        """
        return currency_symbol(self.historical_low_currency)


XBOX_SHOP_ID = 0  # not an IsThereAnyDeal shop: Xbox prices come from the Microsoft catalog
# Tampoco es de IsThereAnyDeal (las suyas son números pequeños): los precios salen de la eShop de Nintendo.
NINTENDO_SHOP_ID = 1000
# ID de Steam en IsThereAnyDeal.
STEAM_SHOP_ID = 61

# Shop IDs of the best-known stores, in display order, with their logo template.
FEATURED_SHOPS = {
    STEAM_SHOP_ID: "steam", 16: "epic", 48: "microsoft", 35: "gog", XBOX_SHOP_ID: "xbox", NINTENDO_SHOP_ID: "nintendo",
}

CURRENT_XBOX_PLATFORMS = ("Xbox One", "Xbox Series X|S")
NINTENDO_PLATFORMS = ("Nintendo Switch", "Nintendo Switch 2")

# Console stores: when the game has no price from one of them, its page links to a search there instead.
# (store, logo, consoles, search URL, shop ID of its prices or None if it has no price source)
STORE_SEARCHES = [
    (
        "PlayStation Store",
        "playstation",
        ("PlayStation 4", "PlayStation 5"),
        "https://store.playstation.com/es-es/search/{}",
        None,
    ),
    (
        "Nintendo eShop",
        "nintendo",
        NINTENDO_PLATFORMS,
        "https://www.nintendo.com/es-es/Buscar/Buscar-299117.html?q={}",
        NINTENDO_SHOP_ID,
    ),
    (
        "Xbox Store",
        "xbox",
        CURRENT_XBOX_PLATFORMS,
        "https://www.xbox.com/es-ES/search/results/games?q={}",
        XBOX_SHOP_ID,
    ),
]
FALLBACK_SHOWN = 5  # stores shown when a game is in none of the featured ones

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def currency_symbol(code):
    """Símbolo de una moneda a partir de su código ISO («EUR» → «€»); el propio código si no lo conoce.

    Pantallas: Ficha del juego (Dónde comprarlo), Lista de deseos, Página de la lista (los símbolos de moneda).
    """
    return CURRENCY_SYMBOLS.get(code, code)


def known_shops():
    """Tiendas con algún precio en el catálogo, [(id, nombre)]: las destacadas primero y después por nombre.

    Pantallas: Editar perfil (Tiendas en las que compras).
    """
    names = {}
    for shop_id, name in GamePrice.objects.values_list("shop_id", "shop_name").distinct():
        names.setdefault(shop_id, name)
    featured = list(FEATURED_SHOPS)
    return sorted(
        names.items(),
        key=lambda item: (featured.index(item[0]) if item[0] in featured else len(featured), item[1].casefold()),
    )


def format_price(amount, symbol="€"):
    """Precio escrito como en España para los textos que se generan fuera de las plantillas: «2,99 €».

    Pantallas: Ficha del juego (historial de precios), Página de la lista (lo que cuesta lo que te falta), Avisos
    (bajadas de precio), Editar perfil (mensaje al traer la lista de deseos de Steam).
    """
    return f"{amount:.2f}".replace(".", ",") + f" {symbol}"


class GamePrice(models.Model):
    """Precio actual de un juego en una tienda."""

    class Source(models.TextChoices):
        ITAD = "itad", "IsThereAnyDeal"
        XBOX = "xbox", "Xbox"
        NINTENDO = "nintendo", "Nintendo"

    XBOX_SHOP_ID = XBOX_SHOP_ID
    NINTENDO_SHOP_ID = NINTENDO_SHOP_ID

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="prices")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.ITAD)
    shop_id = models.PositiveIntegerField()
    shop_name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    regular_price = models.DecimalField(max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=3)
    cut = models.PositiveSmallIntegerField(default=0)
    url = models.URLField(max_length=500)

    class Meta:
        ordering = ["price", "shop_name"]
        constraints = [models.UniqueConstraint(fields=["game", "shop_id"], name="unique_price_per_shop")]

    def __str__(self):
        """Texto del precio en el admin: «juego · tienda: precio moneda».

        Pantallas: Panel de administración.
        """
        return f"{self.game} · {self.shop_name}: {self.price} {self.currency}"

    @property
    def symbol(self):
        """Símbolo de la moneda del precio.

        Pantallas: Ficha del juego (Dónde comprarlo), Lista de deseos (precios).
        """
        return currency_symbol(self.currency)

    @property
    def is_free(self):
        """Indica si el juego es gratis en esta tienda (no solo rebajado a 0 por una oferta).

        Pantallas: Ficha del juego (Dónde comprarlo: «Gratis»).
        """
        return self.price == 0 and self.regular_price == 0

    @property
    def is_featured(self):
        """Indica si la tienda es una de las destacadas (Steam, Epic, Microsoft, GOG, Xbox, Nintendo).

        Pantallas: Ficha del juego (Dónde comprarlo: tiendas destacadas).
        """
        return self.shop_id in FEATURED_SHOPS

    @property
    def logo_template(self):
        """Ruta de la plantilla SVG con el logo de la tienda; solo para tiendas destacadas.

        Pantallas: Ficha del juego (Dónde comprarlo: logos de las tiendas).
        """
        return f"games/shop_icons/{FEATURED_SHOPS[self.shop_id]}.svg"
