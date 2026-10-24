from urllib.parse import quote

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
    """Nombre en español de un género de IGDB; si no está en GENRE_NAMES_ES, el original."""
    return GENRE_NAMES_ES.get(slug or slugify(name), name)


IGDB_IMAGE_URL = "https://images.igdb.com/igdb/image/upload/{size}/{image_id}.{ext}"


def igdb_image_url(image_id, size="t_cover_big", ext="jpg"):
    """URL de una imagen del CDN de IGDB (portada, arte o captura) en ese tamaño; vacía si no hay imagen.

    Con ext «png» conserva la transparencia, que hace falta en los logos.
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
    """
    audio, text = f"{prefix}spanish_audio", f"{prefix}spanish_text"
    spanish = [Spanish.SPAIN, Spanish.LATAM]
    return {
        "voces": models.Q(**{audio: Spanish.SPAIN}),
        "textos": models.Q(**{text: Spanish.SPAIN}) | models.Q(**{audio: Spanish.SPAIN}),
        "espanol": models.Q(**{f"{text}__in": spanish}) | models.Q(**{f"{audio}__in": spanish}),
    }.get(key)


class Genre(models.Model):
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        """Nombre del género."""
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
        """Nombre completo de la plataforma."""
        return self.name

    @property
    def short_name(self):
        """Nombre corto para las etiquetas (p. ej. «PS5»); el completo si no tiene abreviatura."""
        return self.abbreviation or self.name


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
    genres = models.ManyToManyField(Genre, related_name="games", blank=True)
    platforms = models.ManyToManyField(Platform, related_name="games", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-popularity", "name"]

    def __str__(self):
        """Nombre del juego."""
        return self.name

    def get_absolute_url(self):
        """URL de la ficha del juego (/juegos/<slug>/)."""
        return reverse("games:detail", args=[self.slug])

    def review_platforms(self):
        """Plataformas que se pueden elegir en una reseña del juego: las suyas y, si sale en PC, la Steam Deck."""
        query = models.Q(games=self)
        if any(platform.name in STEAM_DECK_HOSTS for platform in self.platforms.all()):
            query |= models.Q(slug=STEAM_DECK_SLUG)
        return Platform.objects.filter(query).distinct()

    @property
    def cover_url(self):
        """URL de la portada en el CDN de IGDB (la europea si IGDB la tiene), o cadena vacía si no hay portada."""
        return igdb_image_url(self.cover_eu_image_id or self.cover_image_id)

    def merged_languages(self):
        """Idiomas de todas las fuentes juntos: {código: {"audio", "text"}}. Si una fuente dice que tiene voces, las tiene."""
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
        """Nombre con el que se vende en España si es distinto del original («Los Sims 4»); vacío si no."""
        return self.name_es if self.name_es and self.name_es.casefold() != self.name.casefold() else ""

    @property
    def language_sources(self):
        """Fuentes que han dado idiomas del juego, para citarlas en la ficha: p. ej. ["IGDB", "Steam"]."""
        names = {"igdb": "IGDB", "steam": "Steam"}
        return [names.get(source, source) for source, values in (self.languages or {}).items() if values]

    @property
    def language_rows(self):
        """Idiomas para la tabla de la ficha: [(nombre en español, voces, textos)], con los españoles y el inglés primero."""
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
        """Año de lanzamiento, o None si no se conoce la fecha."""
        return self.first_release_date.year if self.first_release_date else None

    @property
    def price_lookups(self):
        """Grupos ([(tienda, ID en la tienda), ...], nombre de edición) con los que buscar precios, por preferencia.

        Primero los IDs del propio juego y después los de sus ediciones y remasters. Los IDs de un
        mismo grupo son el mismo producto, así que sus ofertas se juntan. Si hay un ID de Steam
        manual, solo se usa ese.
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
        """IDs de producto de Xbox con los que buscar el precio: el manual si lo hay, si no los de IGDB."""
        return [self.xbox_product_id_override] if self.xbox_product_id_override else self.xbox_product_ids

    @property
    def on_current_xbox(self):
        """Indica si el juego está en Xbox One o Xbox Series X|S."""
        return any(platform.name in CURRENT_XBOX_PLATFORMS for platform in self.platforms.all())

    @property
    def best_price(self):
        """Oferta más barata de todas las tiendas (PC y Xbox), o None si no hay precios."""
        return self.cheapest()

    def cheapest(self, shops=None):
        """Oferta más barata de esas tiendas (ids; None = todas), o None si ninguna lo vende."""
        prices = sorted(
            (price for price in self.prices.all() if shops is None or price.shop_id in shops),
            key=lambda price: price.price,
        )
        return prices[0] if prices else None

    @property
    def is_free(self):
        """Indica si alguna tienda lo da gratis (precio y precio normal a 0), es decir, si es free-to-play."""
        return any(price.is_free for price in self.prices.all())

    def price_groups(self, shops=None):
        """Reparte los precios de esas tiendas (ids; None = todas) para la ficha en (destacados, listados, plegados, mejor).

        destacados: las tiendas más conocidas (Steam, Epic, Microsoft, GOG, Xbox) en un orden fijo.
        listados: las demás más baratas, solo si el juego no está en ninguna destacada.
        plegados: el resto por precio, dentro de «Ver N tiendas más».
        mejor: la oferta más barata cuando ha quedado plegada, para poder señalarla igualmente.
        """
        prices = [price for price in self.prices.all() if shops is None or price.shop_id in shops]
        featured_ids = list(FEATURED_SHOPS)
        featured = sorted((p for p in prices if p.is_featured), key=lambda p: featured_ids.index(p.shop_id))
        others = [p for p in prices if not p.is_featured]
        listed = [] if featured else others[:FALLBACK_SHOWN]
        folded = others[len(listed):]
        best = prices[0] if prices and prices[0] in folded else None
        return featured, listed, folded, best

    def store_searches(self):
        """(tienda, plantilla del logo, URL de búsqueda) de las consolas en las que está el juego pero sin precio.

        Son PlayStation Store, Nintendo eShop y, si no se encontró precio de Xbox, Xbox Store.
        """
        platforms = {platform.name for platform in self.platforms.all()}
        has_xbox_price = any(price.shop_id == XBOX_SHOP_ID for price in self.prices.all())
        return [
            (store, f"games/shop_icons/{logo}.svg", url.format(quote(self.name)))
            for store, logo, consoles, url in STORE_SEARCHES
            if platforms.intersection(consoles) and not (store == "Xbox Store" and has_xbox_price)
        ]

    def community_rating(self):
        """Media de las valoraciones de los usuarios de NextPlay: {"average": de 0,5 a 5 estrellas o None, "count": n},
        y la misma solo con quienes tienen horas importadas de Steam, PlayStation o Xbox («verified_average» y
        «verified_count»), que no pueden valorarlo sin haberlo jugado.

        Las notas se guardan en medias estrellas (de 1 a 10), así que las medias se dividen entre dos.
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
        """Nota de la crítica redondeada (0-100), o None si no tiene."""
        return round(self.critic_rating) if self.critic_rating is not None else None

    @property
    def critic_tier(self):
        """Franja de la nota para colorearla: «high» (75 o más), «mid» (50-74), «low» o vacío sin nota."""
        score = self.critic_score
        if score is None:
            return ""
        return "high" if score >= 75 else "mid" if score >= 50 else "low"

    @property
    def historical_low_symbol(self):
        """Símbolo de la moneda del mínimo histórico (€, $, £...)."""
        return currency_symbol(self.historical_low_currency)


XBOX_SHOP_ID = 0  # not an IsThereAnyDeal shop: Xbox prices come from the Microsoft catalog
# ID de Steam en IsThereAnyDeal.
STEAM_SHOP_ID = 61

# Shop IDs of the best-known stores, in display order, with their logo template.
FEATURED_SHOPS = {STEAM_SHOP_ID: "steam", 16: "epic", 48: "microsoft", 35: "gog", XBOX_SHOP_ID: "xbox"}

CURRENT_XBOX_PLATFORMS = ("Xbox One", "Xbox Series X|S")

# Consoles whose stores have no price source: the game page links to a search there instead.
# (store, logo, consoles, search URL)
STORE_SEARCHES = [
    (
        "PlayStation Store",
        "playstation",
        ("PlayStation 4", "PlayStation 5"),
        "https://store.playstation.com/es-es/search/{}",
    ),
    (
        "Nintendo eShop",
        "nintendo",
        ("Nintendo Switch", "Nintendo Switch 2"),
        "https://www.nintendo.com/es-es/Buscar/Buscar-299117.html?q={}",
    ),
    ("Xbox Store", "xbox", CURRENT_XBOX_PLATFORMS, "https://www.xbox.com/es-ES/search/results/games?q={}"),
]
FALLBACK_SHOWN = 5  # stores shown when a game is in none of the featured ones

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def currency_symbol(code):
    """Símbolo de una moneda a partir de su código ISO («EUR» → «€»); el propio código si no lo conoce."""
    return CURRENCY_SYMBOLS.get(code, code)


def known_shops():
    """Tiendas con algún precio en el catálogo, [(id, nombre)]: las destacadas primero y después por nombre."""
    names = {}
    for shop_id, name in GamePrice.objects.values_list("shop_id", "shop_name").distinct():
        names.setdefault(shop_id, name)
    featured = list(FEATURED_SHOPS)
    return sorted(
        names.items(),
        key=lambda item: (featured.index(item[0]) if item[0] in featured else len(featured), item[1].casefold()),
    )


def format_price(amount, symbol="€"):
    """Precio escrito como en España para los textos que se generan fuera de las plantillas: «2,99 €»."""
    return f"{amount:.2f}".replace(".", ",") + f" {symbol}"


class GamePrice(models.Model):
    """Precio actual de un juego en una tienda."""

    class Source(models.TextChoices):
        ITAD = "itad", "IsThereAnyDeal"
        XBOX = "xbox", "Xbox"

    XBOX_SHOP_ID = XBOX_SHOP_ID

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
        """Texto del precio en el admin: «juego · tienda: precio moneda»."""
        return f"{self.game} · {self.shop_name}: {self.price} {self.currency}"

    @property
    def symbol(self):
        """Símbolo de la moneda del precio."""
        return currency_symbol(self.currency)

    @property
    def is_free(self):
        """Indica si el juego es gratis en esta tienda (no solo rebajado a 0 por una oferta)."""
        return self.price == 0 and self.regular_price == 0

    @property
    def is_featured(self):
        """Indica si la tienda es una de las destacadas (Steam, Epic, Microsoft, GOG, Xbox)."""
        return self.shop_id in FEATURED_SHOPS

    @property
    def logo_template(self):
        """Ruta de la plantilla SVG con el logo de la tienda; solo para tiendas destacadas."""
        return f"games/shop_icons/{FEATURED_SHOPS[self.shop_id]}.svg"
