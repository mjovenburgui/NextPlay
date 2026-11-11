"""Vinculación con Steam (inicio de sesión OpenID) e importación de los juegos de su biblioteca y de su lista de deseos."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from games import igdb
from games.models import Game
from library.models import LibraryEntry, WishlistItem
from social import activity

from . import achievements
from .matching import add_title, save_titles, update_library
from .models import Achievement, ExternalTitle, ImportedTitle

OPENID_URL = "https://steamcommunity.com/openid/login"
OPENID_NS = "http://specs.openid.net/auth/2.0"
OPENID_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
CLAIMED_ID = re.compile(r"^https://steamcommunity\.com/openid/id/(\d{17})$")
API_URL = "https://api.steampowered.com"
TIMEOUT = 15
# Plataforma de IGDB de lo que se juega en Steam, para rellenar la de las reseñas.
STEAM_PLATFORM = "PC (Microsoft Windows)"
# Los logros se piden juego a juego: al importar, los de los ACHIEVEMENTS_PER_IMPORT jugados hace menos (el resto, en
# la sincronización de cada noche), con ACHIEVEMENTS_WORKERS peticiones a la vez.
ACHIEVEMENTS_URL = f"{API_URL}/ISteamUserStats/GetPlayerAchievements/v1/"
ACHIEVEMENTS_PER_IMPORT = 40
ACHIEVEMENTS_WORKERS = 8
# Los logros de cada juego (nombres y descripciones en castellano, iconos y cuáles son ocultos) y el porcentaje de los
# jugadores de Steam que tiene cada uno, que no necesita la clave. Se vuelven a leer cuando alguien juega a ese juego
# si tienen más de ACHIEVEMENTS_INFO_DAYS días: la rareza cambia poco a poco.
SCHEMA_URL = f"{API_URL}/ISteamUserStats/GetSchemaForGame/v2/"
PERCENTAGES_URL = f"{API_URL}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/"
ACHIEVEMENTS_LANGUAGE = "spanish"
ACHIEVEMENTS_INFO_DAYS = 30
# Steam da los iconos de los logros en su dirección antigua, donde ya no están los de los logros nuevos (dan 404): se
# piden a la actual, que los tiene todos.
OLD_ICON_PATH = re.compile(r"^https?://[^/]+/steamcommunity/public/images/apps/")
ICON_URL = "https://shared.akamai.steamstatic.com/community_assets/images/apps/"


class SteamError(Exception):
    """Fallo al hablar con Steam (red, clave de la API o respuesta no válida)."""


class SteamPrivateError(SteamError):
    """Steam no devuelve los juegos porque los detalles del perfil del usuario no son públicos."""


def is_configured():
    """Indica si está puesta la clave de la Web API de Steam, necesaria para leer la biblioteca.

    Pantallas: Editar perfil (Cuentas vinculadas: sin clave de Steam no se puede importar), Perfil (modal «Vincular
    cuentas» de su dueño).
    """
    return bool(settings.STEAM_API_KEY)


def login_url(return_to, realm):
    """URL de Steam donde el usuario inicia sesión; Steam lo devuelve después a return_to.

    Pantallas: Editar perfil (Cuentas vinculadas: botón para vincular Steam), Perfil (modal «Vincular cuentas» de su
    dueño).
    """
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

    Pantallas: Editar perfil (Cuentas vinculadas: vuelta desde Steam), Perfil (modal «Vincular cuentas» de su dueño).
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
    """Llama a un método GET de la Web API de Steam con la clave de settings y devuelve su «response».

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas; botón «Lo quiero»), Juegos importados (los que no se encuentran), Inicio (feed: la
    importación), Retos (progreso), Página del reto (progreso), Lista de deseos.
    """
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
    """Nombre, avatar y URL del perfil de Steam: {"persona", "avatar", "profile_url"}, o {} si no lo encuentra.

    Pantallas: Editar perfil (Cuentas vinculadas › Steam: nombre y avatar), Perfil (cuentas de la cabecera), todas (la
    foto del perfil cuando es la de Steam).
    """
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

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
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

    Pantallas: Editar perfil (Cuentas vinculadas › Steam), Lista de deseos, Ficha del juego (botón «Lo quiero»).
    """
    return _api("IWishlistService/GetWishlist/v1/", steamid=steam_id).get("items") or None


def refresh_account(account):
    """Actualiza el nombre, el avatar y la URL guardados de una cuenta de Steam (sin fallar si Steam no responde).

    Pantallas: Editar perfil (Cuentas vinculadas › Steam: nombre y avatar), Perfil (cuentas de la cabecera), todas (la
    foto del perfil cuando es la de Steam).
    """
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

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas; botón «Lo quiero»), Juegos importados (los que no se encuentran), Inicio (feed: la
    importación), Retos (progreso), Página del reto (progreso), Lista de deseos.
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


def _timestamp(value):
    """Fecha de una marca de tiempo de Steam (segundos desde 1970), o None si no viene o es 0.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas;
    insignias de las portadas, cifras y sección «Logros»), Ficha del juego (tus horas; tus logros), Juegos importados
    (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto (progreso).
    """
    return datetime.fromtimestamp(value, tz=dt_timezone.utc) if value else None


def _played_at(data):
    """Fecha de la última partida de un juego de Steam, o None si nunca se ha jugado.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    return _timestamp(data.get("rtime_last_played"))


def import_library(user, include_unplayed=False):
    """Importa a la biblioteca del usuario los juegos que ha jugado en todas sus cuentas de Steam vinculadas.

    Si un juego está en varias cuentas (o en varias apps de Steam, como un relanzamiento) se suman sus
    horas y se toma la última partida más reciente. Los nuevos entran como «Jugando» si se jugaron en
    los últimos 14 días y como «Jugado» si no; los que ya tenía conservan su estado y solo se les
    actualizan las horas. Con include_unplayed también entran los que nunca ha abierto, como
    «Pendiente». Cada app queda guardada como título importado, así que las que no se encuentren se
    pueden emparejar a mano. Las cuentas que no dejan ver sus juegos se saltan; si no deja ninguna,
    lanza SteamPrivateError.

    Lo que ha subido el tiempo de los que ya tenía se apunta como sesiones de juego (update_library), salvo lo que
    trae una cuenta que se importa por primera vez (sin synced_at): es de antes de vincularla, no algo jugado ahora.

    Después lee los logros de los juegos jugados hace menos (import_achievements).

    Devuelve un dict con cuántos juegos se han añadido («added»), actualizado («updated») y no se han
    encontrado en el catálogo («unmatched»), los nombres de las cuentas privadas («private») y de cuántos
    juegos se han leído los logros («achievements»).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas;
    insignias de las portadas, cifras y sección «Logros»), Ficha del juego (tus horas; tus logros), Juegos importados
    (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto (progreso).
    """
    accounts = list(user.steam_accounts.all())
    owned, private = [], []
    # Minutos de cada app en las cuentas que se importan por primera vez: son de antes de vincularlas, no sesiones.
    prior = {}
    for account in accounts:
        try:
            games = owned_games(account.steam_id)
        except SteamPrivateError:
            private.append(account)
            continue
        owned += games
        if account.synced_at is None:
            for data in games:
                if data.get("appid"):
                    key = str(data["appid"])
                    prior[key] = prior.get(key, 0) + (data.get("playtime_forever") or 0)
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
        added, updated = update_library(user, ExternalTitle.Platform.STEAM, record_activity=True, prior_minutes=prior)
        user.steam_accounts.exclude(pk__in=[account.pk for account in private]).update(synced_at=timezone.now())
    return {
        "added": added,
        "updated": updated,
        "unmatched": sum(1 for game in games.values() if game is None),
        "private": [account.name for account in private],
        "achievements": import_achievements(user),
    }


def player_achievements(steam_id, app_id):
    """Logros de un juego en una cuenta de Steam, todos y en el orden de Steam: [(clave, conseguido, fecha)], con la
    fecha en que lo consiguió o None si no lo tiene (o si es de los muy antiguos, de los que Steam no guarda la fecha);
    [] si el juego no tiene logros o None si no se pueden leer (perfil o detalles de juego privados, o Steam no responde).

    A un juego sin logros Steam le contesta con un 400 y «Requested app has no stats», así que no se mira el código
    de la respuesta sino su «playerstats».

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras y sección «Logros»), Ficha del juego (tus logros).
    """
    try:
        response = requests.get(
            ACHIEVEMENTS_URL, params={"key": settings.STEAM_API_KEY, "steamid": steam_id, "appid": app_id},
            timeout=TIMEOUT,
        )
        stats = response.json().get("playerstats") or {}
    except (requests.RequestException, ValueError, AttributeError):
        return None
    if not stats.get("success"):
        return [] if "no stats" in (stats.get("error") or "").lower() else None
    return [
        (item["apiname"], bool(item.get("achieved")), _timestamp(item.get("unlocktime")) if item.get("achieved") else None)
        for item in stats.get("achievements") or []
        if item.get("apiname")
    ]


def _icon(url):
    """Dirección actual de un icono de logro de Steam (ICON_URL en vez de OLD_ICON_PATH), o vacía si no viene.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras y sección «Logros»), Ficha del juego (tus logros).
    """
    return OLD_ICON_PATH.sub(ICON_URL, url or "")


def achievement_schema(app_id):
    """Logros de un juego de Steam con sus textos en castellano, en el orden de Steam: [{"api_name", "name",
    "description", "icon", "icon_locked", "hidden"}], [] si no tiene o None si Steam no responde.

    De los ocultos Steam no da la descripción (tampoco en los logros de cada jugador), así que se queda vacía. Los
    iconos van con su dirección actual (_icon).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras y sección «Logros»), Ficha del juego (tus logros).
    """
    try:
        response = requests.get(
            SCHEMA_URL, params={"key": settings.STEAM_API_KEY, "appid": app_id, "l": ACHIEVEMENTS_LANGUAGE},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        game = response.json().get("game") or {}
    except (requests.RequestException, ValueError, AttributeError):
        return None
    return [
        {
            "api_name": item["name"],
            "name": (item.get("displayName") or item["name"])[:255],
            "description": (item.get("description") or "")[:500],
            "icon": _icon(item.get("icon")),
            "icon_locked": _icon(item.get("icongray")),
            "hidden": bool(item.get("hidden")),
        }
        for item in (game.get("availableGameStats") or {}).get("achievements") or []
        if item.get("name")
    ]


def global_percentages(app_id):
    """Porcentaje de los jugadores de Steam que tiene cada logro de un juego, redondeado a una décima: {clave:
    porcentaje}, o None si Steam no lo da. No necesita la clave de la API, y el porcentaje llega como texto («63.1»).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras y sección «Logros»), Ficha del juego (tus logros).
    """
    try:
        response = requests.get(PERCENTAGES_URL, params={"gameid": app_id}, timeout=TIMEOUT)
        response.raise_for_status()
        items = (response.json().get("achievementpercentages") or {}).get("achievements") or []
        return {item["name"]: round(float(item["percent"]), 1) for item in items if item.get("name")}
    except (requests.RequestException, ValueError, AttributeError, KeyError, TypeError):
        return None


def achievement_info(app_id):
    """Logros de un juego de Steam con su rareza, para achievements.save_info: (esquema, porcentajes) como los dan
    achievement_schema y global_percentages (los porcentajes, None si no se han podido leer), o None si Steam no da el
    esquema.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras y sección «Logros»), Ficha del juego (tus logros).
    """
    schema = achievement_schema(app_id)
    if schema is None:
        return None
    return schema, global_percentages(app_id) if schema else {}


def _needs_info(title, result, known, now):
    """Si hay que (volver a) leer los logros del juego de title: si nunca se han leído, si se leyeron hace más de
    ACHIEVEMENTS_INFO_DAYS días o si el jugador trae alguno que no está. result es lo que da player_achievements y known,
    las (título, clave) que ya hay.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras y sección «Logros»), Ficha del juego (tus logros).
    """
    if title.achievements_read_at is None or title.achievements_read_at < now - timedelta(days=ACHIEVEMENTS_INFO_DAYS):
        return True
    return any((title.pk, api_name) not in known for api_name, _, _ in result)


def import_achievements(user, limit=ACHIEVEMENTS_PER_IMPORT):
    """Lee de Steam los logros de los juegos del usuario que ha jugado desde la última vez que se leyeron (o que nunca
    se han leído), de la partida más reciente a la más antigua y como mucho limit (None: todos), y los apunta en sus
    títulos importados (ImportedTitle): cuántos tiene y, uno a uno, cuáles y cuándo los consiguió
    (achievements.save_unlocked). Con varias cuentas de Steam se queda con la que más lleva de cada juego.

    De los juegos cuyos logros aún no se conocen (o con más de ACHIEVEMENTS_INFO_DAYS días, o si el jugador trae uno
    nuevo) lee también cuáles tienen, con sus textos en castellano y su rareza (achievement_info). Lo que no se puede
    leer se intenta la próxima vez: también los logros de un jugador si no se sabe aún cuáles son los de su juego. Van
    ACHIEVEMENTS_WORKERS peticiones a la vez. De los recién conseguidos, lo que merece salir en el feed (todos los de
    un juego o alguno rarísimo, si son de estos días) pasa a su actividad (social.activity.achievements_read).

    Devuelve de cuántos juegos se han leído.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras, sección «Logros» y pestaña Actividad), Ficha del juego (tus logros), Inicio (feed: logros).
    """
    steam_ids = list(user.steam_accounts.values_list("steam_id", flat=True))
    if not steam_ids or not is_configured():
        return 0
    rows = (
        ImportedTitle.objects.filter(user=user, title__platform=ExternalTitle.Platform.STEAM, minutes__gt=0)
        .filter(Q(achievements_at__isnull=True) | Q(last_played__gt=F("achievements_at")))
        .select_related("title")
        .order_by(F("last_played").desc(nulls_last=True), "pk")
    )
    rows = list(rows if limit is None else rows[:limit])
    if not rows:
        return 0

    def read(row):
        """Lo que más lleva de ese juego entre las cuentas de Steam del usuario, o None si ninguna deja leerlo."""
        found = [player_achievements(steam_id, row.title.external_id) for steam_id in steam_ids]
        return max(
            (result for result in found if result is not None),
            key=lambda result: sum(1 for _, achieved, _ in result if achieved), default=None,
        )

    now = timezone.now()
    # Solo las peticiones van en otros hilos: lo que se guarda, en este.
    with ThreadPoolExecutor(max_workers=ACHIEVEMENTS_WORKERS) as pool:
        found = [(row, result) for row, result in zip(rows, pool.map(read, rows)) if result is not None]
        known = set(
            Achievement.objects.filter(title_id__in={row.title_id for row, _ in found}).values_list("title_id", "api_name")
        )
        titles = {row.title_id: row.title for row, result in found if result and _needs_info(row.title, result, known, now)}
        infos = dict(zip(titles, pool.map(achievement_info, [title.external_id for title in titles.values()])))

    def ready(row, result):
        """Si ya se pueden guardar los logros de row: se acaban de leer los de su juego o ya se conocían todos."""
        if infos.get(row.title_id) is not None:
            return True
        return all((row.title_id, api_name) in known for api_name, _, _ in result)

    with transaction.atomic():
        for title_id, info in infos.items():
            if info is not None:
                achievements.save_info(titles[title_id], *info, at=now)
        saved = [(row, result) for row, result in found if ready(row, result)]
        new = achievements.save_unlocked(saved)
        # Cómo iba cada uno antes de esta lectura, para saber si acaba de conseguirlos todos.
        before = {row.pk: (row.achievements_unlocked, row.achievements_total, row.platinum) for row, _ in saved}
        for row, result in saved:
            row.set_achievements(sum(1 for _, achieved, _ in result if achieved), len(result), at=now)
        ImportedTitle.objects.bulk_update(
            [row for row, _ in saved], ["achievements_unlocked", "achievements_total", "platinum", "achievements_at"],
        )
    activity.achievements_read(user, [row for row, _ in saved], new, before)
    return len(saved)


def _first(values):
    """El menor de los valores que no son None (el mejor puesto o la fecha más antigua), o None si no hay ninguno.

    Pantallas: Editar perfil (Cuentas vinculadas › Steam), Lista de deseos, Ficha del juego (botón «Lo quiero»).
    """
    values = [value for value in values if value is not None]
    return min(values) if values else None


def _wishlist_games(app_ids):
    """Juegos del catálogo de unos IDs de Steam de la lista de deseos: {ID: Game o None}.

    Si alguien corrigió a mano el juego de esa app (ExternalTitle, también para decir que no es un juego),
    manda la corrección; los demás se buscan como los de la biblioteca (_match_games). Una edición cuenta
    como su juego (Game.main).

    Pantallas: Editar perfil (Cuentas vinculadas › Steam), Lista de deseos, Ficha del juego (botón «Lo quiero»).
    """
    corrected = {
        int(title.external_id): title.game
        for title in ExternalTitle.objects.filter(
            platform=ExternalTitle.Platform.STEAM, external_id__in=[str(app_id) for app_id in app_ids]
        ).exclude(source=ExternalTitle.Source.AUTO).select_related("game")
    }
    found = _match_games([app_id for app_id in app_ids if app_id not in corrected])
    games = {app_id: corrected[app_id] if app_id in corrected else found.get(app_id) for app_id in app_ids}
    return {app_id: game.main if game is not None else None for app_id, game in games.items()}


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

    Pantallas: Editar perfil (Cuentas vinculadas › Steam), Lista de deseos, Ficha del juego (botón «Lo quiero»).
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
