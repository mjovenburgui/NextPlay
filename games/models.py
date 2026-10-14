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
        return self.name


class Platform(models.Model):
    igdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=100)
    abbreviation = models.CharField(max_length=30, blank=True)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def short_name(self):
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
    genres = models.ManyToManyField(Genre, related_name="games", blank=True)
    platforms = models.ManyToManyField(Platform, related_name="games", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-popularity", "name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("games:detail", args=[self.slug])

    @property
    def cover_url(self):
        if not self.cover_image_id:
            return ""
        return f"https://images.igdb.com/igdb/image/upload/t_cover_big/{self.cover_image_id}.jpg"

    @property
    def release_year(self):
        return self.first_release_date.year if self.first_release_date else None

    @property
    def price_lookups(self):
        """([(store, store ID), ...], edition name) groups to look prices up with, in order of preference.

        The IDs of one group belong to the same product, so their deals are merged.
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
        return [self.xbox_product_id_override] if self.xbox_product_id_override else self.xbox_product_ids

    @property
    def on_current_xbox(self):
        return any(platform.name in CURRENT_XBOX_PLATFORMS for platform in self.platforms.all())

    @property
    def is_free(self):
        return any(price.is_free for price in self.prices.all())

    def price_groups(self):
        """(featured, listed, folded, best) for the game page.

        featured: the best-known stores, in a fixed order. listed: the cheapest others, shown only
        when the game is in none of them. folded: the rest by price. best: the cheapest deal when
        it is folded away, so it can still be pointed out.
        """
        prices = list(self.prices.all())
        featured_ids = list(FEATURED_SHOPS)
        featured = sorted((p for p in prices if p.is_featured), key=lambda p: featured_ids.index(p.shop_id))
        others = [p for p in prices if not p.is_featured]
        listed = [] if featured else others[:FALLBACK_SHOWN]
        folded = others[len(listed):]
        best = prices[0] if prices and prices[0] in folded else None
        return featured, listed, folded, best

    def store_searches(self):
        """(store name, logo template, search URL) for consoles the game is on but that have no price for it."""
        platforms = {platform.name for platform in self.platforms.all()}
        has_xbox_price = any(price.shop_id == XBOX_SHOP_ID for price in self.prices.all())
        return [
            (store, f"games/shop_icons/{logo}.svg", url.format(quote(self.name)))
            for store, logo, consoles, url in STORE_SEARCHES
            if platforms.intersection(consoles) and not (store == "Xbox Store" and has_xbox_price)
        ]

    @property
    def critic_score(self):
        return round(self.critic_rating) if self.critic_rating is not None else None

    @property
    def critic_tier(self):
        score = self.critic_score
        if score is None:
            return ""
        return "high" if score >= 75 else "mid" if score >= 50 else "low"

    @property
    def historical_low_symbol(self):
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
    return CURRENCY_SYMBOLS.get(code, code)


class GamePrice(models.Model):
    """Current price of a game in one store."""

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
        return f"{self.game} · {self.shop_name}: {self.price} {self.currency}"

    @property
    def symbol(self):
        return currency_symbol(self.currency)

    @property
    def is_free(self):
        return self.price == 0 and self.regular_price == 0

    @property
    def is_featured(self):
        return self.shop_id in FEATURED_SHOPS

    @property
    def logo_template(self):
        return f"games/shop_icons/{FEATURED_SHOPS[self.shop_id]}.svg"
