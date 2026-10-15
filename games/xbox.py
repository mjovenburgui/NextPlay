"""Precios de consola Xbox sacados del catálogo público de la tienda de Microsoft (sin documentar, sin clave)."""

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
    """Fallo al consultar la tienda de Xbox (red o respuesta no válida)."""


def _market():
    """País de la tienda, el mismo que ITAD_COUNTRY (p. ej. «ES»)."""
    return settings.ITAD_COUNTRY.upper()


def _locale():
    """Idioma y país de la tienda, p. ej. «es-ES»."""
    return f"{settings.LANGUAGE_CODE}-{_market()}"


def fetch_products(product_ids):
    """Devuelve {ID de producto: (precio, precio normal, moneda)} de los productos que se pueden comprar en consola Xbox.

    Pide los productos de 20 en 20; los que no se venden en consola no aparecen en el resultado.
    """
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
    """Oferta de compra más barata de un producto que funciona en Xbox, o None si no hay.

    Ignora las de 0,00, que son Game Pass, pruebas o copias incluidas en packs.
    """
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
    """Título para comparar: sin ™ ® ©, en minúsculas y con los espacios simplificados."""
    return " ".join(re.sub(r"[™®©]", "", title or "").casefold().split())


def search_games(name):
    """IDs de producto de los juegos de Xbox que se llaman exactamente como name (títulos en inglés, como en IGDB)."""
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
    """Enlace a la página del producto en xbox.com."""
    return f"https://www.xbox.com/{_locale()}/games/store/_/{product_id}"


@transaction.atomic
def _store(game, product_id, offer):
    """Sustituye el precio de Xbox del juego por offer y calcula el % de descuento; sin offer lo deja sin precio."""
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
    """Guarda el precio de Xbox de cada juego y devuelve cuántos acabaron con precio.

    Usa el primer ID de producto (el suyo y después sus ediciones) que se venda en consola. Como IGDB
    a menudo solo tiene productos que ya no se venden (p. ej. la versión original de un relanzamiento),
    los juegos de Xbox One/Series sin precio se buscan también por su título exacto, salvo que otro
    juego del catálogo se llame igual o que la búsqueda dé más de un producto.
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
