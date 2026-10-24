"""Vinculación con Steam (inicio de sesión OpenID) e importación de los juegos de su biblioteca y de su lista de deseos."""

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
from library.models import LibraryEntry, WishlistItem

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


def wishlist(steam_id):
    """Lista de deseos de la cuenta de Steam: [{"appid", "priority", "date_added"}], o None si no se puede leer.

    No necesita la clave de la API (se manda igual). Steam contesta lo mismo, sin «items», a una lista
    privada que a una vacía, así que las dos dan None.
    """
    return _api("IWishlistService/GetWishlist/v1/", steamid=steam_id).get("items") or None


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
            # playtime_deck_forever: de los minutos, los jugados en una Steam Deck (la reseña dirá Steam Deck si son más).
            add_title(
                titles, data["appid"], data.get("name") or f"App {data['appid']}",
                data.get("playtime_forever"), _played_at(data), [STEAM_PLATFORM],
                deck_minutes=data.get("playtime_deck_forever"),
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


def _first(values):
    """El menor de los valores que no son None (el mejor puesto o la fecha más antigua), o None si no hay ninguno."""
    values = [value for value in values if value is not None]
    return min(values) if values else None


def _wishlist_games(app_ids):
    """Juegos del catálogo de unos IDs de Steam de la lista de deseos: {ID: Game o None}.

    Si alguien corrigió a mano el juego de esa app (ExternalTitle, también para decir que no es un juego),
    manda la corrección; los demás se buscan como los de la biblioteca (_match_games).
    """
    corrected = {
        int(title.external_id): title.game
        for title in ExternalTitle.objects.filter(
            platform=ExternalTitle.Platform.STEAM, external_id__in=[str(app_id) for app_id in app_ids]
        ).exclude(source=ExternalTitle.Source.AUTO).select_related("game")
    }
    found = _match_games([app_id for app_id in app_ids if app_id not in corrected])
    return {app_id: corrected[app_id] if app_id in corrected else found.get(app_id) for app_id in app_ids}


def import_wishlist(user):
    """Trae a «Lo quiero» los juegos de la lista de deseos de todas las cuentas de Steam del usuario.

    Los juegos se buscan por su ID de Steam como los de la biblioteca. No se añaden los que ya están en
    su biblioteca ni los que quitó a mano de «Lo quiero» (Profile.steam_wishlist_skip); de los que ya
    estaban en «Lo quiero» solo se apuntan su puesto y su fecha en Steam. Si se han podido leer todas sus
    listas, se quitan los que trajo una importación anterior y ya no están en Steam (los ha comprado o
    quitado allí); los que añadió él en NextPlay nunca se quitan. Un juego en varias cuentas (o en varias
    apps, como un relanzamiento) se queda con el mejor puesto y la fecha más antigua.

    Devuelve un dict con los deseados de Steam («total»), los WishlistItem nuevos («added», una lista),
    cuántos se han quitado («removed»), cuántos no se traen porque ya los tiene («owned») o porque los
    quitó él («skipped»), cuántos no están en el catálogo («unmatched») y los nombres de las cuentas cuya
    lista no se pudo leer, porque es privada o está vacía («unreadable»).
    """
    wanted, unreadable = {}, []  # ID de Steam → (puesto o None, fecha en que se añadió o None)
    for account in user.steam_accounts.all():
        items = wishlist(account.steam_id)
        if items is None:
            unreadable.append(account.name)
            continue
        for item in items:
            if not item.get("appid"):
                continue
            # Puesto 0: aún no lo ha ordenado en su lista.
            priority = item.get("priority") or None
            added = datetime.fromtimestamp(item["date_added"], tz=dt_timezone.utc) if item.get("date_added") else None
            old_priority, old_added = wanted.get(item["appid"], (None, None))
            wanted[item["appid"]] = (_first([priority, old_priority]), _first([added, old_added]))

    found = {}  # id del juego → [Game, puesto, fecha]
    unmatched = 0
    for app_id, game in (_wishlist_games(list(wanted)) if wanted else {}).items():
        if game is None:
            unmatched += 1
            continue
        priority, added = wanted[app_id]
        _, old_priority, old_added = found.get(game.pk, (game, None, None))
        found[game.pk] = [game, _first([priority, old_priority]), _first([added, old_added])]

    profile = user.profile
    owned = set(LibraryEntry.objects.filter(user=user, game_id__in=list(found)).values_list("game_id", flat=True))
    skip = set(profile.steam_wishlist_skip)
    current = {item.game_id: item for item in WishlistItem.objects.filter(user=user)}
    new, changed = [], []
    for game_id, (game, priority, added) in found.items():
        item = current.get(game_id)
        if item is not None:
            if (item.steam_priority, item.steam_added_at) != (priority, added):
                item.steam_priority, item.steam_added_at = priority, added
                changed.append(item)
        elif game_id not in owned and game_id not in skip:
            new.append(WishlistItem(user=user, game=game, from_steam=True, steam_priority=priority, steam_added_at=added))

    removed = 0
    with transaction.atomic():
        WishlistItem.objects.bulk_create(new)
        WishlistItem.objects.bulk_update(changed, ["steam_priority", "steam_added_at"])
        # Solo con todas las listas leídas: una privada o vacía no dice qué ha dejado de estar en Steam.
        if not unreadable:
            gone = WishlistItem.objects.filter(user=user, steam_added_at__isnull=False).exclude(game_id__in=list(found))
            removed, _ = gone.filter(from_steam=True).delete()
            gone.update(steam_added_at=None, steam_priority=None)
            profile.steam_wishlist_skip = [game_id for game_id in profile.steam_wishlist_skip if game_id in found]
            profile.save(update_fields=["steam_wishlist_skip"])
    return {
        "total": len(wanted),
        "added": new,
        "removed": removed,
        "owned": sum(1 for game_id in found if game_id in owned and game_id not in current),
        "skipped": sum(1 for game_id in found if game_id in skip and game_id not in current and game_id not in owned),
        "unmatched": unmatched,
        "unreadable": unreadable,
    }
