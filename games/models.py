from urllib.parse import quote

from django.db import models
from django.urls import reverse


class Genre(models.Model):
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        """Nombre del género."""
        return self.name


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

    @property
    def cover_url(self):
        """URL de la portada en el CDN de IGDB, o cadena vacía si el juego no tiene portada."""
        if not self.cover_image_id:
            return ""
        return f"https://images.igdb.com/igdb/image/upload/t_cover_big/{self.cover_image_id}.jpg"

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
        """Media de las valoraciones de los usuarios de NextPlay: {"average": 1-5 o None, "count": n}."""
        return self.library_entries.filter(rating__isnull=False).aggregate(
            average=models.Avg("rating"), count=models.Count("id")
        )

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

# Shop IDs of the best-known stores, in display order, with their logo template.
FEATURED_SHOPS = {61: "steam", 16: "epic", 48: "microsoft", 35: "gog", XBOX_SHOP_ID: "xbox"}

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
