"""Vinculación con Steam (inicio de sesión OpenID) e importación de los juegos de su biblioteca."""

import re
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from games import igdb
from games.models import Game

from .matching import add_title, save_titles, update_library
from .models import ExternalTitle

OPENID_URL = "https://steamcommunity.com/openid/login"
OPENID_NS = "http://specs.openid.net/auth/2.0"
OPENID_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
CLAIMED_ID = re.compile(r"^https://steamcommunity\.com/openid/id/(\d{17})$")
API_URL = "https://api.steampowered.com"
TIMEOUT = 15
# Plataforma de IGDB de lo que se juega en Steam, para rellenar la de las reseñas.
STEAM_PLATFORM = "PC (Microsoft Windows)"


class SteamError(Exception):
    """Fallo al hablar con Steam (red, clave de la API o respuesta no válida)."""


class SteamPrivateError(SteamError):
    """Steam no devuelve los juegos porque los detalles del perfil del usuario no son públicos."""


def is_configured():
    """Indica si está puesta la clave de la Web API de Steam, necesaria para leer la biblioteca."""
    return bool(settings.STEAM_API_KEY)


def login_url(return_to, realm):
    """URL de Steam donde el usuario inicia sesión; Steam lo devuelve después a return_to."""
    params = {
        "openid.ns": OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": OPENID_IDENTIFIER_SELECT,
        "openid.claimed_id": OPENID_IDENTIFIER_SELECT,
    }
    return f"{OPENID_URL}?{urlencode(params)}"


def verify_login(params, return_to):
    """Comprueba con Steam la respuesta de OpenID y devuelve el SteamID64 del usuario, o None si no vale.

    Rechaza respuestas de otro proveedor, para otra URL de vuelta o que Steam no confirme como suyas
    (así nadie puede inventarse una URL de vuelta con el SteamID de otro).
    """
    if params.get("openid.mode") != "id_res" or params.get("openid.op_endpoint") != OPENID_URL:
        return None
    if params.get("openid.return_to") != return_to:
        return None
    match = CLAIMED_ID.match(params.get("openid.claimed_id", ""))
    if not match:
        return None
    check = {key: value for key, value in params.items() if key.startswith("openid.")}
    check["openid.mode"] = "check_authentication"
    try:
        response = requests.post(OPENID_URL, data=check, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SteamError(f"No se pudo verificar el inicio de sesión con Steam: {exc}") from exc
    if "is_valid:true" not in response.text.split("\n"):
        return None
    return match.group(1)


def _api(path, **params):
    """Llama a un método GET de la Web API de Steam con la clave de settings y devuelve su «response»."""
    if not is_configured():
        raise SteamError("Falta STEAM_API_KEY.")
    try:
        response = requests.get(
            f"{API_URL}/{path}", params={"key": settings.STEAM_API_KEY, **params}, timeout=TIMEOUT
        )
        response.raise_for_status()
        return response.json().get("response", {})
    except (requests.RequestException, ValueError) as exc:
        raise SteamError(f"Error consultando la API de Steam: {exc}") from exc


def player_summary(steam_id):
    """Nombre, avatar y URL del perfil de Steam: {"persona", "avatar", "profile_url"}, o {} si no lo encuentra."""
    players = _api("ISteamUser/GetPlayerSummaries/v2/", steamids=steam_id).get("players") or []
    if not players:
        return {}
    player = players[0]
    return {
        "persona": (player.get("personaname") or "")[:100],
        "avatar": player.get("avatarfull") or "",
        "profile_url": player.get("profileurl") or f"https://steamcommunity.com/profiles/{steam_id}/",
    }


def owned_games(steam_id):
    """Juegos de la cuenta de Steam: [{"appid", "name", "playtime_forever", "rtime_last_played"...}].

    Lanza SteamPrivateError si el perfil no deja ver sus juegos (Steam responde sin «games»).
    """
    data = _api(
        "IPlayerService/GetOwnedGames/v1/",
        steamid=steam_id,
        include_appinfo=1,
        include_played_free_games=1,
    )
    if "games" not in data:
        if data.get("game_count") == 0:
            return []
        raise SteamPrivateError("Los detalles de juego del perfil de Steam no son públicos.")
    return data["games"]


def refresh_account(account):
    """Actualiza el nombre, el avatar y la URL guardados de una cuenta de Steam (sin fallar si Steam no responde)."""
    try:
        summary = player_summary(account.steam_id)
    except SteamError:
        summary = {}
    account.persona = summary.get("persona", account.persona)
    account.avatar = summary.get("avatar", account.avatar)
    account.profile_url = summary.get("profile_url", account.profile_url)
    account.save()


def _match_games(app_ids):
    """Busca los juegos del catálogo de una lista de IDs de Steam y devuelve {ID de Steam: Game}.

    Primero mira el catálogo local; los que falten los busca en IGDB y los importa (si IGDB está
    configurado).
    """
    matches = {}
    for game in Game.objects.filter(Q(steam_app_id__in=app_ids) | Q(steam_app_id_override__in=app_ids)):
        for app_id in (game.steam_app_id_override, game.steam_app_id):
            if app_id in app_ids:
                matches.setdefault(app_id, game)

    missing = [app_id for app_id in app_ids if app_id not in matches]
    if missing and igdb.is_configured():
        payload, steam_to_igdb = igdb.IGDBClient().games_by_steam_ids(missing)
        by_igdb_id = {game.igdb_id: game for game in igdb.upsert_games(payload)}
        for app_id in missing:
            game = by_igdb_id.get(steam_to_igdb.get(app_id))
            if game is not None:
                matches[app_id] = game
    return matches


def _played_at(data):
    """Fecha de la última partida de un juego de Steam, o None si nunca se ha jugado."""
    if not data.get("rtime_last_played"):
        return None
    return datetime.fromtimestamp(data["rtime_last_played"], tz=dt_timezone.utc)


def import_library(user, include_unplayed=False):
    """Importa a la biblioteca del usuario los juegos que ha jugado en todas sus cuentas de Steam vinculadas.

    Si un juego está en varias cuentas (o en varias apps de Steam, como un relanzamiento) se suman sus
    horas y se toma la última partida más reciente. Los nuevos entran como «Jugando» si se jugaron en
    los últimos 14 días y como «Jugado» si no; los que ya tenía conservan su estado y solo se les
    actualizan las horas. Con include_unplayed también entran los que nunca ha abierto, como
    «Pendiente». Cada app queda guardada como título importado, así que las que no se encuentren se
    pueden emparejar a mano. Las cuentas que no dejan ver sus juegos se saltan; si no deja ninguna,
    lanza SteamPrivateError.

    Devuelve un dict con cuántos juegos se han añadido («added»), actualizado («updated») y no se han
    encontrado en el catálogo («unmatched»), y los nombres de las cuentas privadas («private»).
    """
    accounts = list(user.steam_accounts.all())
    owned, private = [], []
    for account in accounts:
        try:
            owned += owned_games(account.steam_id)
        except SteamPrivateError:
            private.append(account)
    if accounts and len(private) == len(accounts):
        raise SteamPrivateError("Ninguna cuenta de Steam deja ver sus juegos.")
    if not include_unplayed:
        owned = [game for game in owned if game.get("playtime_forever")]

    titles = {}
    for data in owned:
        if data.get("appid"):
            add_title(
                titles, data["appid"], data.get("name") or f"App {data['appid']}",
                data.get("playtime_forever"), _played_at(data), [STEAM_PLATFORM],
            )

    def auto_match(ids):
        """Busca por su ID de app de Steam los títulos que aún no tienen juego."""
        return {str(app_id): game for app_id, game in _match_games([int(i) for i in ids]).items()}

    games = save_titles(user, ExternalTitle.Platform.STEAM, titles, auto_match)
    with transaction.atomic():
        added, updated = update_library(user, ExternalTitle.Platform.STEAM)
        user.steam_accounts.exclude(pk__in=[account.pk for account in private]).update(synced_at=timezone.now())
    return {
        "added": added,
        "updated": updated,
        "unmatched": sum(1 for game in games.values() if game is None),
        "private": [account.name for account in private],
    }
