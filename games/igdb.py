"""Cliente de IGDB y la función que vuelca sus juegos en el catálogo local."""

import re
import time
from datetime import datetime, timezone

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from .models import Game, GamePrice, Genre, Platform

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API_URL = "https://api.igdb.com/v4"
TOKEN_CACHE_KEY = "igdb:access_token"
TIMEOUT = 15
MAX_PAGE_SIZE = 500
STEAM_SOURCE_ID = 1  # external_game_source for Steam; uid is the Steam app ID
GOG_SOURCE_ID = 5  # uid is the GOG product ID
STORE_SOURCES = {"steam": STEAM_SOURCE_ID, "gog": GOG_SOURCE_ID}
MICROSOFT_SOURCE_ID = 11  # uid is a Microsoft Store product ID (or an "xbox360..." legacy one)
MICROSOFT_PRODUCT_ID = re.compile(r"^[0-9A-Z]{12}$")

STEAM_FIELDS = "external_games.uid, external_games.name, external_games.external_game_source"
GAME_FIELDS = (
    "fields name, slug, summary, first_release_date, total_rating_count, cover.image_id, "
    f"aggregated_rating, aggregated_rating_count, {STEAM_FIELDS}, "
    f"remasters.name, {STEAM_FIELDS.replace('external_games', 'remasters.external_games')}, "
    "genres.name, genres.slug, platforms.name, platforms.abbreviation, platforms.slug;"
)


class IGDBError(Exception):
    """Fallo al hablar con IGDB o con Twitch (credenciales, red o respuesta no válida)."""


def is_configured():
    """Indica si están puestas las credenciales de Twitch para usar IGDB."""
    return bool(settings.IGDB_CLIENT_ID and settings.IGDB_CLIENT_SECRET)


class IGDBClient:
    def __init__(self, client_id=None, client_secret=None):
        """Prepara el cliente con las credenciales dadas o las de settings; falla si no hay ninguna."""
        self.client_id = client_id or settings.IGDB_CLIENT_ID
        self.client_secret = client_secret or settings.IGDB_CLIENT_SECRET
        if not (self.client_id and self.client_secret):
            raise IGDBError("Faltan IGDB_CLIENT_ID e IGDB_CLIENT_SECRET.")

    def _token(self):
        """Token de acceso de Twitch: lo saca de la caché o pide uno nuevo y lo guarda hasta que caduque."""
        token = cache.get(TOKEN_CACHE_KEY)
        if token:
            return token
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise IGDBError(f"No se pudo obtener el token de Twitch: {exc}") from exc
        token = data["access_token"]
        cache.set(TOKEN_CACHE_KEY, token, max(int(data.get("expires_in", 0)) - 60, 60))
        return token

    def query(self, endpoint, body):
        """Lanza una consulta en Apicalypse (el lenguaje de IGDB) contra endpoint y devuelve el JSON.

        Si IGDB responde 401 borra el token de la caché para que la próxima llamada pida uno nuevo.
        """
        try:
            response = requests.post(
                f"{API_URL}/{endpoint}",
                data=body.encode("utf-8"),
                headers={
                    "Client-ID": self.client_id,
                    "Authorization": f"Bearer {self._token()}",
                    "Accept": "application/json",
                },
                timeout=TIMEOUT,
            )
            if response.status_code == 401:
                cache.delete(TOKEN_CACHE_KEY)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise IGDBError(f"Error consultando IGDB: {exc}") from exc

    def top_games(self, limit=MAX_PAGE_SIZE, offset=0):
        """Una página de los juegos más valorados por número de votos, sin ediciones sueltas, con sus ediciones."""
        limit = min(limit, MAX_PAGE_SIZE)
        body = (
            f"{GAME_FIELDS} where version_parent = null & total_rating_count > 0; "
            f"sort total_rating_count desc; limit {limit}; offset {offset};"
        )
        return self._with_editions(self.query("games", body))

    def search(self, term, limit=20):
        """Busca juegos por texto en IGDB (hasta limit resultados, sin ediciones sueltas), con sus ediciones."""
        # Apicalypse string literal: drop the characters that would end or escape it.
        term = term.replace("\\", " ").replace('"', " ").strip()
        if not term:
            return []
        body = f'search "{term}"; {GAME_FIELDS} where version_parent = null; limit {limit};'
        return self._with_editions(self.query("games", body))

    def _with_editions(self, games):
        """Añade a cada juego sus ediciones (GOTY, Definitive...) en la clave «editions», para tener sus IDs de tienda."""
        if not games:
            return games
        ids = ",".join(str(game["id"]) for game in games)
        editions, offset = [], 0
        while True:
            time.sleep(0.25)  # IGDB allows 4 requests per second
            page = self.query(
                "games",
                f"fields name, version_parent, {STEAM_FIELDS}; where version_parent = ({ids}); "
                f"sort id asc; limit {MAX_PAGE_SIZE}; offset {offset};",
            )
            editions += page
            if len(page) < MAX_PAGE_SIZE:
                break
            offset += MAX_PAGE_SIZE
        for game in games:
            game["editions"] = [edition for edition in editions if edition.get("version_parent") == game["id"]]
        return games


def _upsert_ref(model, data, **extra):
    """Busca o crea un Genre o Platform de IGDB, aprovechando uno que no venga de IGDB con el mismo slug."""
    slug = (data.get("slug") or slugify(data["name"]))[:120]
    obj = model.objects.filter(igdb_id=data["id"]).first()
    if obj is None:
        obj = model.objects.filter(slug=slug).first()
    if obj is None:
        obj = model(slug=slug)
    obj.igdb_id = data["id"]
    obj.name = data["name"][:100]
    for field, value in extra.items():
        setattr(obj, field, value)
    obj.save()
    return obj


def _normalize(name):
    """Nombre para comparar: sin ™ ® ©, en minúsculas y con los espacios simplificados."""
    return " ".join(re.sub(r"[™®©]", "", name or "").casefold().split())


def _store_ids(data):
    """IDs de tienda de un juego de IGDB: {"steam": [...], "gog": [...]}.

    IGDB puede tener varias entradas por tienda: relanzamientos con el mismo nombre que el juego (se
    quedan todos y sus ofertas se juntan) y otras raras como «Mac Portal» (solo se usa la primera,
    y únicamente si ninguna se llama como el juego).
    """
    name = _normalize(data["name"])
    ids = {}
    for store, source in STORE_SOURCES.items():
        entries = [
            item for item in data.get("external_games") or []
            if item.get("external_game_source") == source and str(item.get("uid") or "").isdigit()
        ]
        chosen = [item for item in entries if _normalize(item.get("name")) == name] or entries[:1]
        uids = list(dict.fromkeys(int(item["uid"]) if store == "steam" else str(item["uid"]) for item in chosen))
        if uids:
            ids[store] = uids
    return ids


def _store_alternatives(data, own_ids):
    """IDs de tienda de las ediciones del juego y después de sus remasters, sin repetir los suyos.

    Sirven para encontrar precios cuando el juego original ya no se vende.
    """
    seen = {(store, uid) for store, uids in own_ids.items() for uid in uids}
    alternatives = []
    for related in (data.get("editions") or []) + (data.get("remasters") or []):
        ids = {}
        for store, uids in _store_ids(related).items():
            new = [uid for uid in uids if (store, uid) not in seen]
            seen.update((store, uid) for uid in new)
            if new:
                ids[store] = new
        if ids:
            alternatives.append({"name": related["name"][:255], **ids})
    return alternatives


def _xbox_product_ids(data):
    """IDs de producto de Microsoft Store del juego y después de sus ediciones y remasters, sin repetidos."""
    ids = []
    for related in [data] + (data.get("editions") or []) + (data.get("remasters") or []):
        for item in related.get("external_games") or []:
            uid = str(item.get("uid") or "")
            if item.get("external_game_source") == MICROSOFT_SOURCE_ID and MICROSOFT_PRODUCT_ID.match(uid):
                if uid not in ids:
                    ids.append(uid)
    return ids


@transaction.atomic
def upsert_games(payload):
    """Crea o actualiza los juegos locales a partir de los de IGDB y devuelve la lista de Game.

    Guarda datos, nota, portada, IDs de tienda, géneros y plataformas. Aprovecha juegos de demo o
    creados a mano con el mismo nombre o slug en vez de duplicarlos. Si cambian los IDs de tienda,
    borra los precios de PC, que se buscaron con los IDs viejos. Todo va en una transacción.
    """
    genres, platforms, games = {}, {}, []
    for data in payload:
        if not data.get("name") or not data.get("slug"):
            continue

        game = Game.objects.filter(igdb_id=data["id"]).first()
        if game is None:
            # Adopt a demo/manual row for the same game instead of duplicating it.
            game = Game.objects.filter(
                Q(slug=data["slug"]) | Q(name__iexact=data["name"]), igdb_id__isnull=True
            ).first()
        if game is None:
            game = Game()

        game.igdb_id = data["id"]
        game.name = data["name"][:255]
        game.slug = data["slug"][:255]
        game.summary = data.get("summary", "")
        game.cover_image_id = (data.get("cover") or {}).get("image_id", "")
        released = data.get("first_release_date")
        game.first_release_date = (
            datetime.fromtimestamp(released, tz=timezone.utc).date() if released is not None else None
        )
        game.popularity = data.get("total_rating_count") or 0
        game.critic_rating = data.get("aggregated_rating")
        game.critic_rating_count = data.get("aggregated_rating_count") or 0
        store_ids = _store_ids(data)
        steam_app_id = (store_ids.get("steam") or [None])[0]
        if store_ids != game.store_ids and not game.steam_app_id_override:
            # Prices were found through the old store IDs; drop them with them.
            game.prices_edition = ""
            game.historical_low = None
            game.historical_low_currency = ""
            game.prices_updated_at = None
            if game.pk:
                game.prices.filter(source=GamePrice.Source.ITAD).delete()
        game.steam_app_id = steam_app_id
        game.store_ids = store_ids
        game.store_alternatives = _store_alternatives(data, store_ids)
        game.xbox_product_ids = _xbox_product_ids(data)
        game.save()

        game_genres = []
        for item in data.get("genres") or []:
            if item["id"] not in genres:
                genres[item["id"]] = _upsert_ref(Genre, item)
            game_genres.append(genres[item["id"]])
        game.genres.set(game_genres)

        game_platforms = []
        for item in data.get("platforms") or []:
            if item["id"] not in platforms:
                platforms[item["id"]] = _upsert_ref(
                    Platform, item, abbreviation=(item.get("abbreviation") or "")[:30]
                )
            game_platforms.append(platforms[item["id"]])
        game.platforms.set(game_platforms)

        games.append(game)
    return games
