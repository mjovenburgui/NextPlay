"""Importación de juegos y horas de PlayStation (PS4/PS5) con la API no oficial de la app de PlayStation.

Sony no tiene API pública: se usa la misma que la app oficial, a la que se accede con el código NPSSO
de la sesión del usuario en playstation.com. El código no se guarda; se pide en cada sincronización.
Es experimental: Sony puede cambiarla sin avisar.
"""

import base64
import json
import re
from urllib.parse import parse_qs, urlparse

import requests
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .matching import add_title, clean_name, match_by_name, normalize, save_titles, update_library
from .models import ExternalTitle, ImportedTitle, PSNAccount

AUTH_URL = "https://ca.account.sony.com/api/authz/v3/oauth"
# Cliente público de la app oficial de PlayStation para Android (el mismo que usan las librerías no oficiales).
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
CLIENT_BASIC = "MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"
PROFILE_URL = "https://us-prof.np.community.playstation.net/userProfile/v1/users/me/profile2"
GAMES_URL = "https://m.np.playstation.com/api/gamelist/v2/users/me/titles"
# Lista de juegos con trofeos de la cuenta, con cuántos tiene y ha conseguido de cada tipo (del bronce al platino).
TROPHIES_URL = "https://m.np.playstation.com/api/trophy/v1/users/me/trophyTitles"
TROPHIES_PAGE_SIZE = 800
TROPHY_GRADES = ("bronze", "silver", "gold", "platinum")
NPSSO_URL = "https://ca.account.sony.com/api/v1/ssocookie"
NPSSO = re.compile(r"^[A-Za-z0-9]{64}$")
TIMEOUT = 20
PAGE_SIZE = 200
DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")

class PSNError(Exception):
    """Fallo al hablar con PlayStation (red, respuesta inesperada o API cambiada)."""


class PSNAuthError(PSNError):
    """Sony no acepta el código NPSSO (mal copiado o caducado)."""


def clean_npsso(value):
    """Saca el código NPSSO de lo que pegue el usuario: el código suelto o el JSON entero de la página de Sony.

    Devuelve el código o None si no tiene el formato esperado (64 letras y números).
    """
    value = (value or "").strip()
    if value.startswith("{"):
        try:
            value = str(json.loads(value).get("npsso", ""))
        except (ValueError, AttributeError):
            return None
    return value if NPSSO.match(value) else None


def access_token(npsso):
    """Cambia el código NPSSO por un token de acceso de la API de PlayStation.

    Lanza PSNAuthError si Sony no da el código de autorización (NPSSO incorrecto o caducado).
    """
    try:
        response = requests.get(
            f"{AUTH_URL}/authorize",
            params={
                "access_type": "offline",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": SCOPE,
            },
            cookies={"npsso": npsso},
            allow_redirects=False,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise PSNError(f"No se pudo conectar con PlayStation: {exc}") from exc
    code = parse_qs(urlparse(response.headers.get("Location", "")).query).get("code", [None])[0]
    if not code:
        raise PSNAuthError("PlayStation no aceptó el código NPSSO.")
    try:
        response = requests.post(
            f"{AUTH_URL}/token",
            data={
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
                "token_format": "jwt",
            },
            headers={"Authorization": f"Basic {CLIENT_BASIC}"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise PSNError(f"No se pudo obtener el token de PlayStation: {exc}") from exc


def _get(url, token, **params):
    """GET autenticado contra la API de PlayStation; devuelve el JSON."""
    try:
        response = requests.get(
            url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PSNError(f"Error consultando PlayStation ({url}): {exc}") from exc


def _account_id_from_token(token):
    """ID de cuenta guardado dentro del token JWT, por si el perfil no lo devuelve; None si no está."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return None
    value = data.get("account_id") or data.get("sub")
    return str(value) if value else None


def profile(token):
    """Datos de la cuenta del token: {"account_id", "online_id", "avatar"}.

    Si el perfil no responde, intenta sacar al menos el ID de cuenta del propio token.
    """
    try:
        data = _get(PROFILE_URL, token, fields="accountId,onlineId,avatarUrls").get("profile") or {}
    except PSNError:
        data = {}
    avatars = data.get("avatarUrls") or []
    account_id = str(data.get("accountId") or "") or _account_id_from_token(token)
    if not account_id:
        raise PSNError("PlayStation no devolvió el ID de la cuenta.")
    return {
        "account_id": account_id,
        "online_id": data.get("onlineId") or "",
        "avatar": (avatars[-1].get("avatarUrl") if avatars else "") or "",
    }


def played_games(token):
    """Juegos de PS4 y PS5 que ha jugado la cuenta, con «name», «playDuration», «lastPlayedDateTime»..."""
    titles, offset = [], 0
    while True:
        data = _get(
            GAMES_URL, token, categories="ps4_game,ps5_native_game", limit=PAGE_SIZE, offset=offset
        )
        titles += data.get("titles") or []
        offset = data.get("nextOffset")
        if not offset or not data.get("titles"):
            return titles


def trophy_titles(token):
    """Juegos con trofeos de la cuenta (también de PS3 y PS Vita): cada uno con su nombre («trophyTitleName») y, por
    tipo (bronze, silver, gold y platinum), los trofeos que tiene («definedTrophies») y los conseguidos
    («earnedTrophies»)."""
    titles, offset = [], 0
    while True:
        data = _get(TROPHIES_URL, token, limit=TROPHIES_PAGE_SIZE, offset=offset)
        titles += data.get("trophyTitles") or []
        offset = data.get("nextOffset")
        if not offset or not data.get("trophyTitles"):
            return titles


def save_trophies(user, trophies):
    """Apunta en los títulos de PlayStation importados del usuario sus trofeos (los de trophy_titles) y devuelve en
    cuántos.

    PlayStation da los trofeos con otro identificador que los juegos, así que se emparejan por nombre: el del título o
    el del juego del catálogo que tiene. Si un juego tiene trofeos aparte en PS4 y en PS5, cuenta la versión que más
    lleva (primero el platino). Los títulos sin trofeos leídos se quedan como estaban.
    """
    found = {}
    for trophy in trophies:
        name = normalize(clean_name(trophy.get("trophyTitleName")))
        defined, earned = trophy.get("definedTrophies") or {}, trophy.get("earnedTrophies") or {}
        total = sum(defined.get(grade) or 0 for grade in TROPHY_GRADES)
        if not name or not total:
            continue
        result = (min(sum(earned.get(grade) or 0 for grade in TROPHY_GRADES), total), total, bool(earned.get("platinum")))
        old = found.get(name)
        if old is None or (result[2], result[0] / result[1]) > (old[2], old[0] / old[1]):
            found[name] = result
    now = timezone.now()
    rows = []
    for row in ImportedTitle.objects.filter(user=user, title__platform=ExternalTitle.Platform.PSN).select_related("title__game"):
        names = [row.title.name] + ([row.title.game.name] if row.title.game else [])
        result = next((found[key] for key in map(normalize, names) if key in found), None)
        if result is not None:
            row.set_achievements(*result, at=now)
            rows.append(row)
    ImportedTitle.objects.bulk_update(rows, ["achievements_unlocked", "achievements_total", "platinum", "achievements_at"])
    return len(rows)


def duration_minutes(value):
    """Minutos de una duración ISO 8601 de PSN («PT228H56M33S» → 13736); 0 si no se entiende."""
    match = DURATION.match(value or "")
    if not match:
        return 0
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 1440 + int(hours or 0) * 60 + int(minutes or 0) + round(float(seconds or 0) / 60)


# Títulos de PSN que no son un juego concreto sino un lanzador de varios (p. ej. «Call of Duty®» en PS5,
# desde el que se abren MWII, MWIII y Black Ops 6). Se cuentan como no encontrados.
LAUNCHERS = {"call of duty"}
# Plataforma de IGDB de cada categoría de PSN, para desempatar juegos que se llaman igual (remakes).
CATEGORY_PLATFORMS = {"ps4_game": "PlayStation 4", "ps5_native_game": "PlayStation 5"}


def import_library(user, npsso):
    """Vincula (o actualiza) la cuenta de PSN del NPSSO e importa sus juegos de PS4/PS5 con sus horas.

    Los nuevos entran como «Jugando» si se jugaron en los últimos 14 días y como «Jugado» si no; los
    que ya tenía conservan su estado y solo se les actualizan las horas de PlayStation. La versión de
    PS4 y la de PS5 de un juego se suman. Lanza PSNAuthError si el código no vale y ValueError si esa
    cuenta de PSN ya es de otro usuario.

    Con el mismo token lee después los trofeos de cada juego (trophy_titles y save_trophies); si fallan, la
    importación de los juegos sigue valiendo.

    Devuelve un dict con la cuenta («account»), cuántos juegos se han añadido («added»), actualizado
    («updated») y no se han encontrado en el catálogo («unmatched»), y de cuántos se han leído los trofeos
    («trophies»).
    """
    token = access_token(npsso)
    info = profile(token)
    owner = PSNAccount.objects.filter(account_id=info["account_id"]).exclude(user=user).first()
    if owner is not None:
        raise ValueError("Esa cuenta de PlayStation ya está vinculada a otro usuario.")

    titles = {}
    for title in played_games(token):
        name = clean_name(title.get("localizedName") or title.get("name"))
        if not name:
            continue
        platform = CATEGORY_PLATFORMS.get(title.get("category"))
        add_title(
            titles, title.get("titleId") or f"name:{normalize(name)}", name,
            duration_minutes(title.get("playDuration")), parse_datetime(title.get("lastPlayedDateTime") or ""),
            [platform] if platform else [],
            # PlayStation también da la primera partida: rellena «Lo empecé el» en las reseñas.
            first_played=parse_datetime(title.get("firstPlayedDateTime") or ""),
        )

    def auto_match(ids):
        """Busca por nombre (y plataforma) los títulos que aún no tienen juego, salvo los lanzadores."""
        return match_by_name({
            i: (titles[i]["name"], titles[i]["platforms"])
            for i in ids if normalize(titles[i]["name"]) not in LAUNCHERS
        })

    games = save_titles(user, ExternalTitle.Platform.PSN, titles, auto_match)
    with transaction.atomic():
        account, _ = PSNAccount.objects.update_or_create(
            user=user,
            defaults={
                "account_id": info["account_id"],
                "online_id": info["online_id"],
                "avatar": info["avatar"],
                "synced_at": timezone.now(),
            },
        )
        # PSN solo lista juegos que se han abierto: nunca entran como «Pendiente».
        added, updated = update_library(user, ExternalTitle.Platform.PSN, opened_only=True, record_activity=True)
    unmatched = sum(1 for game in games.values() if game is None)
    try:
        trophies = save_trophies(user, trophy_titles(token))
    except PSNError:
        # Sin los trofeos, lo importado de los juegos sigue valiendo: se intentan en la próxima sincronización.
        trophies = 0
    return {"account": account, "added": added, "updated": updated, "unmatched": unmatched, "trophies": trophies}
