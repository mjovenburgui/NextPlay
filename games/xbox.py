"""Xbox console prices from Microsoft's public store catalog (undocumented, no key needed)."""

import re
import time
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction

from .models import Game, GamePrice

CATALOG_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"
SEARCH_URL = "https://displaycatalog.mp.microsoft.com/v7.0/productFamilies/autosuggest"
TIMEOUT = 15
BATCH_SIZE = 20  # product IDs per catalog request
XBOX_PLATFORM = "Windows.Xbox"


class XboxError(Exception):
    pass


def _market():
    return settings.ITAD_COUNTRY.upper()


def _locale():
    return f"{settings.LANGUAGE_CODE}-{_market()}"


def fetch_products(product_ids):
    """Return {product_id: (price, regular_price, currency)} for products buyable on Xbox consoles."""
    found = {}
    for start in range(0, len(product_ids), BATCH_SIZE):
        batch = product_ids[start : start + BATCH_SIZE]
        try:
            response = requests.get(
                CATALOG_URL,
                params={"bigIds": ",".join(batch), "market": _market(), "languages": _locale()},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            products = response.json().get("Products") or []
        except (requests.RequestException, ValueError) as exc:
            raise XboxError(f"Error consultando la tienda de Xbox: {exc}") from exc
        for product in products:
            offer = _console_offer(product)
            if offer:
                found[product["ProductId"]] = offer
    return found


def _console_offer(product):
    """Cheapest paid purchase offer that runs on Xbox; 0.00 SKUs are Game Pass, trials or bundled copies."""
    best = None
    for sku in product.get("DisplaySkuAvailabilities") or []:
        for availability in sku.get("Availabilities") or []:
            platforms = (availability.get("Conditions") or {}).get("ClientConditions", {}).get("AllowedPlatforms") or []
            price = (availability.get("OrderManagementData") or {}).get("Price") or {}
            if (
                "Purchase" not in (availability.get("Actions") or [])
                or not any(p.get("PlatformName") == XBOX_PLATFORM for p in platforms)
                or not price.get("ListPrice")
            ):
                continue
            offer = (
                Decimal(str(price["ListPrice"])).quantize(Decimal("0.01")),
                Decimal(str(price.get("MSRP") or price["ListPrice"])).quantize(Decimal("0.01")),
                price.get("CurrencyCode") or "",
            )
            if best is None or offer[0] < best[0]:
                best = offer
    return best


def _normalize(title):
    return " ".join(re.sub(r"[™®©]", "", title or "").casefold().split())


def search_games(name):
    """Product IDs of Xbox games titled exactly like name (English titles, as in IGDB)."""
    try:
        response = requests.get(
            SEARCH_URL,
            params={
                "market": _market(),
                "languages": "en-US",
                "query": name,
                "productFamilyNames": "Games",
                "platformdependencyname": XBOX_PLATFORM,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("Results") or []
    except (requests.RequestException, ValueError) as exc:
        raise XboxError(f"Error buscando en la tienda de Xbox: {exc}") from exc
    wanted = _normalize(name)
    return [
        product["ProductId"]
        for result in results
        for product in result.get("Products") or []
        if product.get("Type") == "Game" and _normalize(product.get("Title")) == wanted
    ]


def store_url(product_id):
    return f"https://www.xbox.com/{_locale()}/games/store/_/{product_id}"


@transaction.atomic
def _store(game, product_id, offer):
    game.prices.filter(source=GamePrice.Source.XBOX).delete()
    if offer:
        price, regular, currency = offer
        GamePrice.objects.create(
            game=game,
            source=GamePrice.Source.XBOX,
            shop_id=GamePrice.XBOX_SHOP_ID,
            shop_name="Xbox",
            price=price,
            regular_price=max(regular, price),
            currency=currency,
            cut=round((1 - price / regular) * 100) if regular > price else 0,
            url=store_url(product_id),
        )


def update_xbox_prices(games, sleep=time.sleep):
    """Store the Xbox price of each game: its first product ID (own, then editions) sold on consoles.

    IGDB often lists only products no longer sold (e.g. the original release of a re-released game),
    so games on current Xbox consoles without a price are also searched by their exact title.
    Returns how many games ended up with a price.
    """
    games = [game for game in games if game.xbox_lookup_ids or game.on_current_xbox]
    offers = fetch_products(sorted({pid for game in games for pid in game.xbox_lookup_ids}))
    chosen = {game.pk: next((pid for pid in game.xbox_lookup_ids if pid in offers), None) for game in games}

    searchable = [
        g for g in games
        if not chosen[g.pk] and not g.xbox_product_id_override and g.on_current_xbox
        # A title shared in the catalog (Resident Evil 4 of 2005, 2011 and 2023) can't pick its own product.
        and not Game.objects.filter(name__iexact=g.name).exclude(pk=g.pk).exists()
    ]
    found = {}
    for game in searchable:
        sleep(0.1)
        found[game.pk] = search_games(game.name)
    offers.update(fetch_products(sorted({pid for ids in found.values() for pid in ids} - offers.keys())))
    for game in searchable:
        # Two games sold under the same title (e.g. Resident Evil 4 and its remake) can't be told apart.
        sold = [pid for pid in found[game.pk] if pid in offers]
        chosen[game.pk] = sold[0] if len(sold) == 1 else None

    for game in games:
        _store(game, chosen[game.pk], offers.get(chosen[game.pk]))
    return sum(product_id is not None for product_id in chosen.values())
