"""Vinculación con Xbox (inicio de sesión con cuenta Microsoft) e importación de sus juegos y minutos jugados.

Flujo: el usuario inicia sesión en Microsoft (OAuth 2.0 de una aplicación de Azure con el permiso
XboxLive.signin) → con el token de Microsoft se pide uno de usuario de Xbox Live → con ese, uno XSTS
que trae el XUID y el gamertag → con el XSTS se leen el historial de juegos y la estadística
MinutesPlayed. Ningún token se guarda: se usan durante la petición y se tiran.
"""

from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .matching import add_title, clean_name, match_by_name, normalize, save_titles, update_library
from .models import ExternalTitle, PlatformAccount, XboxAccount

AUTHORIZE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
SCOPE = "XboxLive.signin offline_access"
USER_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
PROFILE_URL = "https://profile.xboxlive.com/users/xuid({xuid})/profile/settings"
TITLES_URL = "https://titlehub.xboxlive.com/users/xuid({xuid})/titles/titlehistory/decoration/detail"
STATS_URL = "https://userstats.xboxlive.com/batch"
TIMEOUT = 20
STATS_BATCH = 50  # títulos por petición de estadísticas
# Errores de XSTS que tienen explicación para el usuario.
XSTS_ERRORS = {
    2148916233: "Esa cuenta Microsoft no tiene perfil de Xbox. Entra una vez en xbox.com para crearlo.",
    2148916235: "Xbox Live no está disponible en el país de tu cuenta.",
    2148916238: "Es una cuenta infantil: un adulto tiene que añadirla a una familia de Microsoft.",
}
# Plataforma de IGDB de cada dispositivo del historial, para desempatar juegos que se llaman igual (remakes).
DEVICE_PLATFORMS = {
    "Xbox360": "Xbox 360",
    "XboxOne": "Xbox One",
    "XboxSeries": "Xbox Series X|S",
    "PC": "PC (Microsoft Windows)",
    "Win32": "PC (Microsoft Windows)",
}


class XboxError(Exception):
    """Fallo al hablar con Microsoft o Xbox Live (red, credenciales o respuesta inesperada)."""


def is_configured():
    """Indica si están puestas las credenciales de la aplicación de Azure para iniciar sesión con Microsoft."""
    return bool(settings.XBOX_CLIENT_ID and settings.XBOX_CLIENT_SECRET)


def login_url(redirect_uri, state):
    """URL de Microsoft donde el usuario inicia sesión; Microsoft lo devuelve a redirect_uri con un código."""
    params = {
        "client_id": settings.XBOX_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "prompt": "select_account",  # deja elegir cuenta si tiene varias abiertas
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _post(url, **kwargs):
    """POST a Microsoft o Xbox Live que devuelve el JSON; lanza XboxError si falla."""
    try:
        response = requests.post(url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise XboxError(f"No se pudo conectar con {url}: {exc}") from exc
    if url == XSTS_URL and response.status_code == 401:
        try:
            code = response.json().get("XErr")
        except ValueError:
            code = None
        raise XboxError(XSTS_ERRORS.get(code, f"Xbox Live rechazó la cuenta (XErr {code})."))
    if response.status_code >= 400:
        raise XboxError(f"{url} respondió {response.status_code}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError as exc:
        raise XboxError(f"{url} no devolvió JSON") from exc


def xbox_tokens(code, redirect_uri):
    """Cambia el código de Microsoft por los datos de Xbox Live: {"xuid", "gamertag", "auth"}.

    «auth» es la cabecera Authorization (XBL3.0) para llamar a las APIs de Xbox Live.
    """
    microsoft = _post(TOKEN_URL, data={
        "client_id": settings.XBOX_CLIENT_ID,
        "client_secret": settings.XBOX_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
    })
    user = _post(USER_AUTH_URL, headers={"x-xbl-contract-version": "1"}, json={
        "Properties": {
            "AuthMethod": "RPS",
            "SiteName": "user.auth.xboxlive.com",
            "RpsTicket": f"d={microsoft['access_token']}",
        },
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    })
    xsts = _post(XSTS_URL, headers={"x-xbl-contract-version": "1"}, json={
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [user["Token"]]},
        "RelyingParty": "http://xboxlive.com",
        "TokenType": "JWT",
    })
    try:
        claims = xsts["DisplayClaims"]["xui"][0]
        return {
            "xuid": claims["xid"],
            "gamertag": claims.get("gtg", ""),
            "auth": f"XBL3.0 x={claims['uhs']};{xsts['Token']}",
        }
    except (KeyError, IndexError) as exc:
        raise XboxError("Xbox Live no devolvió el XUID de la cuenta.") from exc


def _get(url, auth, contract, **params):
    """GET autenticado contra una API de Xbox Live con su versión de contrato; devuelve el JSON."""
    try:
        response = requests.get(
            url,
            params=params,
            headers={"Authorization": auth, "x-xbl-contract-version": contract, "Accept-Language": "es-ES"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise XboxError(f"Error consultando Xbox Live ({url}): {exc}") from exc


def avatar(xuid, auth):
    """URL del avatar (gamerpic) de la cuenta, o vacío si Xbox Live no la da."""
    try:
        data = _get(PROFILE_URL.format(xuid=xuid), auth, "3", settings="GameDisplayPicRaw")
        settings_list = data["profileUsers"][0]["settings"]
    except (XboxError, KeyError, IndexError):
        return ""
    return next((item["value"] for item in settings_list if item.get("id") == "GameDisplayPicRaw"), "")


def played_titles(xuid, auth):
    """Juegos del historial de la cuenta (solo de tipo «Game», sin apps como Netflix), con su última partida."""
    titles = _get(TITLES_URL.format(xuid=xuid), auth, "2").get("titles") or []
    return [title for title in titles if title.get("type") == "Game"]


def minutes_played(xuid, auth, title_ids):
    """Minutos jugados por título según la estadística MinutesPlayed: {titleId: minutos}.

    Los juegos que no publican esa estadística (muchos de Xbox 360 y algunos de PC) no aparecen.
    """
    minutes = {}
    for start in range(0, len(title_ids), STATS_BATCH):
        chunk = title_ids[start:start + STATS_BATCH]
        data = _post(
            STATS_URL,
            headers={"Authorization": auth, "x-xbl-contract-version": "2"},
            json={
                "arrangebyfield": "xuid",
                "xuids": [xuid],
                "stats": [{"name": "MinutesPlayed", "titleId": title_id} for title_id in chunk],
            },
        )
        for collection in data.get("statlistscollection") or []:
            for stat in collection.get("stats") or []:
                title_id = str(stat.get("titleid") or stat.get("titleId") or "")
                if title_id and str(stat.get("value") or "").isdigit():
                    minutes[title_id] = int(stat["value"])
    return minutes


def import_library(user, code, redirect_uri):
    """Vincula (o actualiza) la cuenta de Xbox del inicio de sesión e importa sus juegos con los minutos jugados.

    Los juegos se emparejan con el catálogo por nombre (y por los dispositivos en los que se jugaron).
    Los nuevos entran como «Jugando» si se jugaron en los últimos 14 días y como «Jugado» si no; los
    que ya tenía conservan su estado (salvo «Pendiente», que pasa a jugado si hay horas) y se les
    actualizan los minutos de Xbox. Si el usuario tenía puesto a mano un gamertag en «Otras
    plataformas», se quita porque ya está la cuenta verificada. Lanza ValueError si esa cuenta de Xbox
    ya es de otro usuario.

    Devuelve un dict con la cuenta («account») y cuántos juegos se han añadido («added»), actualizado
    («updated») y no se han encontrado en el catálogo («unmatched»).
    """
    xbl = xbox_tokens(code, redirect_uri)
    if XboxAccount.objects.filter(xuid=xbl["xuid"]).exclude(user=user).exists():
        raise ValueError("Esa cuenta de Xbox ya está vinculada a otro usuario.")
    played = played_titles(xbl["xuid"], xbl["auth"])
    minutes = minutes_played(xbl["xuid"], xbl["auth"], [str(t["titleId"]) for t in played if t.get("titleId")])

    titles = {}
    for title in played:
        name = clean_name(title.get("name"))
        if not name:
            continue
        title_id = str(title.get("titleId") or "")
        add_title(
            titles, title_id or f"name:{normalize(name)}", name, minutes.get(title_id, 0),
            parse_datetime((title.get("titleHistory") or {}).get("lastTimePlayed") or ""),
            [DEVICE_PLATFORMS[d] for d in title.get("devices") or [] if d in DEVICE_PLATFORMS],
        )

    def auto_match(ids):
        """Busca por nombre (y plataforma) los títulos que aún no tienen juego."""
        return match_by_name({i: (titles[i]["name"], titles[i]["platforms"]) for i in ids})

    games = save_titles(user, ExternalTitle.Platform.XBOX, titles, auto_match)
    with transaction.atomic():
        account, _ = XboxAccount.objects.update_or_create(
            user=user,
            defaults={
                "xuid": xbl["xuid"],
                "gamertag": xbl["gamertag"],
                "avatar": avatar(xbl["xuid"], xbl["auth"]),
                "synced_at": timezone.now(),
            },
        )
        PlatformAccount.objects.filter(user=user, platform=PlatformAccount.Platform.XBOX).delete()
        # El historial solo trae juegos abiertos alguna vez: nunca entran como «Pendiente».
        added, updated = update_library(user, ExternalTitle.Platform.XBOX, opened_only=True, record_activity=True)
    unmatched = sum(1 for game in games.values() if game is None)
    return {"account": account, "added": added, "updated": updated, "unmatched": unmatched}
