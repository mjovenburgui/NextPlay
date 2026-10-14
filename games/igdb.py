"""IGDB client and the upsert that maps its payloads onto the local catalog."""

from datetime import datetime, timezone

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from .models import Game, Genre, Platform

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API_URL = "https://api.igdb.com/v4"
TOKEN_CACHE_KEY = "igdb:access_token"
TIMEOUT = 15
MAX_PAGE_SIZE = 500

GAME_FIELDS = (
    "fields name, slug, summary, first_release_date, total_rating_count, cover.image_id, "
    "genres.name, genres.slug, platforms.name, platforms.abbreviation, platforms.slug;"
)


class IGDBError(Exception):
    pass


def is_configured():
    return bool(settings.IGDB_CLIENT_ID and settings.IGDB_CLIENT_SECRET)


class IGDBClient:
    def __init__(self, client_id=None, client_secret=None):
        self.client_id = client_id or settings.IGDB_CLIENT_ID
        self.client_secret = client_secret or settings.IGDB_CLIENT_SECRET
        if not (self.client_id and self.client_secret):
            raise IGDBError("Faltan IGDB_CLIENT_ID e IGDB_CLIENT_SECRET.")

    def _token(self):
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
        limit = min(limit, MAX_PAGE_SIZE)
        body = (
            f"{GAME_FIELDS} where version_parent = null & total_rating_count > 0; "
            f"sort total_rating_count desc; limit {limit}; offset {offset};"
        )
        return self.query("games", body)

    def search(self, term, limit=20):
        # Apicalypse string literal: drop the characters that would end or escape it.
        term = term.replace("\\", " ").replace('"', " ").strip()
        if not term:
            return []
        body = f'search "{term}"; {GAME_FIELDS} where version_parent = null; limit {limit};'
        return self.query("games", body)


def _upsert_ref(model, data, **extra):
    """Get or create a Genre/Platform, adopting a non-IGDB row that shares the slug."""
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


@transaction.atomic
def upsert_games(payload):
    """Create or update local games from IGDB game objects. Returns the Game list."""
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
