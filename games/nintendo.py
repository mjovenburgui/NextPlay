"""Precios de la eShop de Nintendo (Switch y Switch 2) en la tienda europea (sin documentar, sin clave).

IGDB no tiene el NSUID de los juegos (su ID en la eShop), así que se busca por el nombre en el buscador
de Nintendo Europa, el de su web, y se guarda en el juego. El precio sale de la API de precios de la
eShop, la misma que usa la web de Nintendo. Los NSUID de la tienda europea valen en todos sus países.
"""

import re
import time
import unicodedata
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction

from .models import Game, GamePrice

SEARCH_URL = "https://searching.nintendo-europe.com/{language}/select"
PRICE_URL = "https://api.ec.nintendo.com/v1/price"
TIMEOUT = 15
BATCH_SIZE = 50  # NSUIDs per price request: the API rejects more
SEARCH_ROWS = 30  # resultados de cada búsqueda entre los que se busca el título exacto
# Código de cada consola en el buscador de Nintendo.
SYSTEMS = {"Nintendo Switch": "HAC", "Nintendo Switch 2": "BEE"}
# NSUID de un juego completo; los de contenido descargable empiezan por 7005 y los de packs por 7007.
GAME_NSUID = re.compile(r"^7001\d{10}$")
# Caracteres con significado en las búsquedas de Solr, el motor del buscador.
SOLR_SYNTAX = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


class NintendoError(Exception):
    """Fallo al consultar la eShop de Nintendo (red o respuesta no válida)."""


def _country():
    """País de la tienda, el mismo que ITAD_COUNTRY (p. ej. «ES»); tiene que ser de la tienda europea."""
    return settings.ITAD_COUNTRY.upper()


def _amount(value):
    """Importe de la API de precios («14.99») como Decimal con dos decimales."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def fetch_prices(nsuids):
    """Devuelve {NSUID: (precio, precio normal, moneda)} de los juegos que se pueden comprar.

    Pide los precios de 50 en 50; los que no están a la venta (sin salir o retirados) no aparecen.
    """
    found = {}
    for start in range(0, len(nsuids), BATCH_SIZE):
        batch = nsuids[start : start + BATCH_SIZE]
        try:
            response = requests.get(
                PRICE_URL,
                params={"country": _country(), "lang": settings.LANGUAGE_CODE, "ids": ",".join(batch)},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            items = response.json().get("prices") or []
        except (requests.RequestException, ValueError) as exc:
            raise NintendoError(f"Error consultando los precios de la eShop de Nintendo: {exc}") from exc
        for item in items:
            offer = _offer(item)
            if offer:
                found[str(item["title_id"])] = offer
    return found


def _offer(item):
    """(precio, precio normal, moneda) de un juego de la respuesta de precios, o None si no se vende.

    El precio es el rebajado si tiene una oferta en marcha.
    """
    regular = item.get("regular_price") or {}
    if item.get("sales_status") != "onsale" or regular.get("raw_value") is None:
        return None
    discount = (item.get("discount_price") or {}).get("raw_value")
    regular_price = _amount(regular["raw_value"])
    price = _amount(discount) if discount is not None else regular_price
    return price, regular_price, regular.get("currency") or ""


def _normalize(title):
    """Título para comparar: en minúsculas, sin tildes ni signos (™, dos puntos, guiones...) y con los espacios
    simplificados, porque la tienda los pone a su manera: «South Park™ : La vara de la verdad™», «ABZÛ»."""
    # Fuera ™ ® © antes de separar las tildes, que NFKD convierte ™ en «TM».
    title = unicodedata.normalize("NFKD", re.sub(r"[™®©]", "", title or ""))
    title = "".join(char for char in title if not unicodedata.combining(char))
    return " ".join(re.sub(r"[\W_]+", " ", title.casefold()).split())


def search_game(names, systems):
    """NSUID del juego de esas consolas («HAC», «BEE») que se llama exactamente como uno de names, o None.

    Busca con cada nombre hasta encontrarlo y compara con el título de la tienda española, el original en
    inglés y sus traducciones. Si dos juegos de una consola se llaman igual no elige ninguno; si es el
    mismo título en Switch y en Switch 2 se queda con el de Switch, que también funciona en Switch 2.
    """
    wanted = {_normalize(name) for name in names} - {""}
    for name in names:
        query = " ".join(SOLR_SYNTAX.sub(" ", name).split())
        if not query:
            continue
        try:
            response = requests.get(
                SEARCH_URL.format(language=settings.LANGUAGE_CODE),
                params={
                    "q": query,
                    "fq": f"type:GAME AND playable_on_txt:({' OR '.join(sorted(systems))})",
                    "fl": "title,title_master_s,title_extras_txt,nsuid_txt,playable_on_txt",
                    "rows": SEARCH_ROWS,
                    "wt": "json",
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            docs = (response.json().get("response") or {}).get("docs") or []
        except (requests.RequestException, ValueError) as exc:
            raise NintendoError(f"Error buscando en la eShop de Nintendo: {exc}") from exc
        found = {}  # consola -> NSUIDs de los juegos que se llaman así
        for doc in docs:
            titles = [doc.get("title"), doc.get("title_master_s"), *(doc.get("title_extras_txt") or [])]
            nsuid = next((n for n in doc.get("nsuid_txt") or [] if GAME_NSUID.match(n)), None)
            if nsuid and wanted.intersection(_normalize(title) for title in titles if title):
                for system in systems.intersection(doc.get("playable_on_txt") or []):
                    found.setdefault(system, set()).add(nsuid)
        candidates = found.get("HAC") or found.get("BEE")
        if candidates:
            return next(iter(candidates)) if len(candidates) == 1 else None
    return None


def store_url(nsuid):
    """Enlace al juego en la eShop, que lleva a su página en nintendo.com."""
    return f"https://ec.nintendo.com/{_country()}/{settings.LANGUAGE_CODE}/titles/{nsuid}"


def _systems(game):
    """Códigos del buscador de Nintendo de las consolas del juego: «HAC» (Switch) y «BEE» (Switch 2)."""
    return {SYSTEMS[platform.name] for platform in game.platforms.all() if platform.name in SYSTEMS}


@transaction.atomic
def _store(game, nsuid, offer):
    """Sustituye el precio de Nintendo del juego por offer y calcula el % de descuento; sin offer lo deja sin precio."""
    game.prices.filter(source=GamePrice.Source.NINTENDO).delete()
    if offer:
        price, regular, currency = offer
        GamePrice.objects.create(
            game=game,
            source=GamePrice.Source.NINTENDO,
            shop_id=GamePrice.NINTENDO_SHOP_ID,
            shop_name="Nintendo eShop",
            price=price,
            regular_price=max(regular, price),
            currency=currency,
            cut=round((1 - price / regular) * 100) if regular > price else 0,
            url=store_url(nsuid),
        )


def update_nintendo_prices(games, sleep=time.sleep):
    """Guarda el precio de la eShop de cada juego y devuelve cuántos acabaron con precio.

    Los juegos de Switch o Switch 2 sin NSUID (ni manual ni encontrado antes) lo buscan primero por su
    nombre y su nombre en España, salvo que otro juego del catálogo se llame igual; el que se encuentra se
    guarda para no volver a buscarlo.
    """
    games = [game for game in games if game.nintendo_lookup_nsuid or _systems(game)]
    for game in games:
        # A title shared in the catalog (Resident Evil 4 of 2005 and 2023) can't pick its own product.
        if game.nintendo_lookup_nsuid or Game.objects.filter(name__iexact=game.name).exclude(pk=game.pk).exists():
            continue
        sleep(0.1)
        nsuid = search_game(list(dict.fromkeys(name for name in (game.name, game.name_es) if name)), _systems(game))
        if nsuid:
            game.nintendo_nsuid = nsuid
            game.save(update_fields=["nintendo_nsuid"])

    offers = fetch_prices(sorted({game.nintendo_lookup_nsuid for game in games if game.nintendo_lookup_nsuid}))
    for game in games:
        _store(game, game.nintendo_lookup_nsuid, offers.get(game.nintendo_lookup_nsuid))
    return sum(game.nintendo_lookup_nsuid in offers for game in games)
