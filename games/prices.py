"""Cliente de IsThereAnyDeal y la sincronización que guarda los precios actuales de PC de cada juego."""

import time
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import GamePrice

API_URL = "https://api.isthereanydeal.com"
# IsThereAnyDeal shop IDs and the format of their product IDs in /lookup/id/shop.
LOOKUP_SHOPS = {"steam": (61, "app/{}"), "gog": (35, "{}")}
TIMEOUT = 15
BATCH_SIZE = 200  # max game IDs per /games/prices/v3 request
MAX_RETRIES = 3


class ITADError(Exception):
    """Fallo al hablar con IsThereAnyDeal (clave, red o respuesta no válida)."""


def is_configured():
    """Indica si está puesta la clave de IsThereAnyDeal."""
    return bool(settings.ITAD_API_KEY)


class ITADClient:
    def __init__(self, api_key=None, country=None):
        """Prepara el cliente con la clave y el país dados o los de settings; falla si no hay clave."""
        self.api_key = api_key or settings.ITAD_API_KEY
        self.country = country or settings.ITAD_COUNTRY
        if not self.api_key:
            raise ITADError("Falta ITAD_API_KEY.")

    def _post(self, path, body, params=None):
        """POST a la API con la clave y devuelve el JSON.

        Si la API responde 429 (demasiadas peticiones) espera lo que diga Retry-After (máximo 5
        minutos) y reintenta hasta 3 veces.
        """
        try:
            for attempt in range(MAX_RETRIES + 1):
                response = requests.post(
                    f"{API_URL}{path}",
                    json=body,
                    params=params,
                    headers={"ITAD-API-Key": self.api_key},
                    timeout=TIMEOUT,
                )
                if response.status_code != 429 or attempt == MAX_RETRIES:
                    break
                # Rate limited: the window resets after Retry-After seconds.
                time.sleep(min(int(response.headers.get("Retry-After") or 30), 300))
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ITADError(f"Error consultando IsThereAnyDeal: {exc}") from exc

    def lookup(self, store, store_ids):
        """Traduce IDs de una tienda (de Steam o de GOG) a los UUID de juego de ITAD (None si no lo conoce)."""
        shop_id, key = LOOKUP_SHOPS[store]
        found = self._post(f"/lookup/id/shop/{shop_id}/v1", [key.format(store_id) for store_id in store_ids])
        return {store_id: found.get(key.format(store_id)) for store_id in store_ids}

    def prices(self, itad_ids):
        """Ofertas actuales y mínimo histórico de cada juego de ITAD en el país configurado."""
        return self._post("/games/prices/v3", list(itad_ids), params={"country": self.country})


def _amount(value):
    """Importe de un precio de ITAD como Decimal con dos decimales."""
    return Decimal(str(value["amount"])).quantize(Decimal("0.01"))


def _batches(items):
    """Parte la lista en trozos de BATCH_SIZE, lo máximo que acepta la API por petición."""
    for start in range(0, len(items), BATCH_SIZE):
        yield items[start : start + BATCH_SIZE]


def _cheapest_per_shop(deals):
    """Se queda con la oferta más barata de cada tienda."""
    # A shop can list several deals for one game (e.g. keys for different launchers).
    cheapest = {}
    for deal in deals:
        shop_id = deal["shop"]["id"]
        if shop_id not in cheapest or _amount(deal["price"]) < _amount(cheapest[shop_id]["price"]):
            cheapest[shop_id] = deal
    return cheapest.values()


@transaction.atomic
def _store_prices(game, data, edition=""):
    """Sustituye los precios de PC del juego por las ofertas de data y guarda su mínimo histórico.

    Con data vacío deja el juego sin precios de PC. edition es el nombre de la edición de la que
    salen los precios (vacío si son del propio juego).
    """
    game.prices.filter(source=GamePrice.Source.ITAD).delete()
    GamePrice.objects.bulk_create(
        GamePrice(
            game=game,
            shop_id=deal["shop"]["id"],
            shop_name=deal["shop"]["name"][:100],
            price=_amount(deal["price"]),
            regular_price=_amount(deal["regular"]),
            currency=deal["price"]["currency"],
            cut=deal.get("cut") or 0,
            url=deal["url"][:500],
        )
        for deal in _cheapest_per_shop((data or {}).get("deals") or [])
    )
    low = ((data or {}).get("historyLow") or {}).get("all")
    game.historical_low = _amount(low) if low else None
    game.historical_low_currency = low["currency"] if low else ""
    game.prices_edition = edition if data else ""
    game.prices_updated_at = timezone.now()
    game.save(update_fields=["prices_edition", "historical_low", "historical_low_currency", "prices_updated_at"])


def _lookup(client, keys):
    """Traduce un conjunto de (tienda, ID) a UUID de ITAD, tienda por tienda y por lotes."""
    found = {}
    for store in LOOKUP_SHOPS:
        ids = sorted({store_id for key_store, store_id in keys if key_store == store})
        for batch in _batches(ids):
            found.update(((store, store_id), itad_id) for store_id, itad_id in client.lookup(store, batch).items())
    return found


def _fetch_prices(client, itad_ids):
    """Precios de todos los UUID de ITAD pedidos por lotes: {uuid: datos de precios}."""
    data = {}
    for batch in _batches(sorted(itad_ids)):
        data.update((item["id"], item) for item in client.prices(batch))
    return data


def _merge(items):
    """Junta los precios de varios juegos de ITAD que son el mismo producto (p. ej. un relanzamiento).

    Suma todas sus ofertas y se queda con el mínimo histórico más bajo. Devuelve None si no hay datos.
    """
    items = [item for item in items if item]
    if not items:
        return None
    lows = [low for item in items if (low := (item.get("historyLow") or {}).get("all"))]
    return {
        "deals": [deal for item in items for deal in item.get("deals") or []],
        "historyLow": {"all": min(lows, key=_amount)} if lows else None,
    }


def update_prices(games, client=None):
    """Actualiza los precios de PC de los juegos y devuelve cuántos acabaron con precios.

    Va por rondas: en la primera busca con los IDs del propio juego; los juegos que se quedan sin
    ofertas pasan a la siguiente ronda con su primera edición o remaster, y así sucesivamente.
    """
    client = client or ITADClient()
    games = [game for game in games if game.price_lookups]
    found = {}  # game.pk -> (merged prices item, edition name)
    pending, rank = games, 0
    while pending:
        round_ = [(game, game.price_lookups[rank]) for game in pending if len(game.price_lookups) > rank]
        if not round_:
            break
        itad_ids = _lookup(client, {key for _, (keys, _) in round_ for key in keys})
        data = _fetch_prices(client, {itad_id for itad_id in itad_ids.values() if itad_id})
        pending = []
        for game, (keys, edition) in round_:
            item = _merge([data.get(itad_id) for itad_id in dict.fromkeys(itad_ids.get(key) for key in keys) if itad_id])
            if item and item["deals"]:
                found[game.pk] = (item, edition)
            else:
                pending.append(game)
        rank += 1

    for game in games:
        item, edition = found.get(game.pk, (None, ""))
        _store_prices(game, item, edition)
    return len(found)
