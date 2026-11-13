"""Importar la biblioteca y las horas de Epic Games con la API de su lanzador y el código de inicio de sesión que el
usuario pega cada vez, como el NPSSO de PlayStation.

Epic no tiene API para webs: se usa la de su lanzador, con su cliente público (el mismo que usan Legendary, Heroic y
Playnite). Con la sesión iniciada en epicgames.com, la página CODE_URL enseña un código («authorizationCode») que se
cambia por una sesión del lanzador (start_session) para leer la biblioteca (library_records), las horas que ha contado
el lanzador (playtimes) y el nombre de cada juego en el catálogo (catalog_item). Ese código da acceso completo a la
cuenta, y Epic lo avisa en esa misma página: no se guarda ni él ni la sesión, que se cierra al acabar (_close). Cada
juego se importa como una fila de un archivo (accounts.csv_import), emparejado por su nombre. Es experimental: Epic
puede cambiarla sin avisar.

Pantallas: Editar perfil (Cuentas vinculadas › Epic Games), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y
horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto
(progreso).
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from django.db import transaction
from django.utils import timezone

from . import csv_import
from .matching import clean_name
from .models import EpicAccount, ExternalTitle, PlatformAccount

# Cliente público del lanzador de Epic, el mismo que usan Legendary, Heroic y Playnite.
CLIENT_ID = "34a02cf8f4414e29b15921876da36f9a"
CLIENT_SECRET = "daafbccc737745039dffe53d94fc76cf"
# Con la sesión iniciada en epicgames.com (LOGIN_URL), CODE_URL enseña el código de ese cliente.
LOGIN_URL = "https://www.epicgames.com/id/login"
CODE_URL = f"https://www.epicgames.com/id/api/redirect?clientId={CLIENT_ID}&responseType=code"
TOKEN_URL = "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token"
CLOSE_URL = "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/sessions/kill/{}"
LIBRARY_URL = "https://library-service.live.use1a.on.epicgames.com/library/api/public/items"
PLAYTIME_URL = "https://library-service.live.use1a.on.epicgames.com/library/api/public/playtime/account/{}/all"
CATALOG_URL = "https://catalog-public-service-prod06.ol.epicgames.com/catalog/api/shared/namespace/{}/bulk/items"
CODE = re.compile(r"^[A-Za-z0-9]{20,64}$")
CODE_IN_URL = re.compile(r"[?&]code=([A-Za-z0-9]+)")
TIMEOUT = 20
# Como mucho tantas páginas de la biblioteca, y tantas fichas del catálogo pedidas a la vez.
MAX_PAGES = 50
CATALOG_WORKERS = 6
# Lo que no es un juego: la tienda de Unreal Engine (su espacio de nombres) y, en el catálogo, las expansiones y los DLC,
# los mods, los extras (bandas sonoras, libros de arte…) y lo de Unreal (plugins y motores).
SKIP_NAMESPACES = {"ue"}
NOT_GAMES = ("addons", "mods", "digitalextras", "plugins", "engines")


class EpicError(Exception):
    """Fallo al hablar con Epic (red, respuesta inesperada o API cambiada)."""


class EpicAuthError(EpicError):
    """Epic no acepta el código: mal copiado, caducado o ya usado."""


def clean_code(value):
    """Saca el código de lo que pegue el usuario: el código suelto, el texto entero de la página de Epic
    ({"authorizationCode": "…"}) o una dirección que lo lleve en «code=». None si no lo parece.

    Pantallas: Editar perfil (Cuentas vinculadas › Epic Games: el código pegado), Perfil (modal «Vincular cuentas» de
    su dueño).
    """
    value = (value or "").strip()
    if value.startswith("{"):
        try:
            value = str(json.loads(value).get("authorizationCode") or "")
        except (ValueError, AttributeError):
            return None
    match = CODE_IN_URL.search(value)
    if match:
        value = match[1]
    value = value.strip().strip('"')
    return value if CODE.match(value) else None


def _json(response):
    """El JSON de una respuesta de Epic, o {} si no lo es.

    Pantallas: Editar perfil (Cuentas vinculadas › Epic Games).
    """
    try:
        return response.json()
    except ValueError:
        return {}


def start_session(code):
    """Cambia el código por una sesión del lanzador: {"access_token", "account_id", "displayName"…}. Lanza
    EpicAuthError si Epic no acepta el código y EpicError si falla otra cosa.

    Pantallas: Editar perfil (Cuentas vinculadas › Epic Games).
    """
    try:
        response = requests.post(
            TOKEN_URL, data={"grant_type": "authorization_code", "code": code, "token_type": "eg1"},
            auth=(CLIENT_ID, CLIENT_SECRET), timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise EpicError("No se ha podido conectar con Epic.") from exc
    data = _json(response)
    data = data if isinstance(data, dict) else {}
    if response.status_code == 200 and data.get("access_token") and data.get("account_id"):
        return data
    error = str(data.get("errorCode") or "")
    if response.status_code in (400, 401) or "oauth" in error:
        raise EpicAuthError(error)
    raise EpicError(f"Epic ha respondido con el código {response.status_code}.")


def _get(url, token, params=None):
    """GET a la API del lanzador con la sesión token: su JSON. Lanza EpicError si falla.

    Pantallas: Editar perfil (Cuentas vinculadas › Epic Games), Perfil (biblioteca y horas).
    """
    try:
        response = requests.get(url, params=params, headers={"Authorization": f"bearer {token}"}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise EpicError("No se ha podido conectar con Epic.") from exc
    if response.status_code != 200:
        raise EpicError(f"Epic ha respondido con el código {response.status_code}.")
    return _json(response)


def library_records(token):
    """Los artículos de la biblioteca de la cuenta (juegos, DLC y lo demás), página a página con el cursor que da
    Epic: [{"namespace", "catalogItemId", "appName", "sandboxType"…}]. Lanza EpicError si falla.

    Pantallas: Editar perfil (Cuentas vinculadas › Epic Games), Perfil (biblioteca y horas).
    """
    records, params = [], {"includeMetadata": "true"}
    for _ in range(MAX_PAGES):
        data = _get(LIBRARY_URL, token, params)
        if not isinstance(data, dict):
            raise EpicError("La biblioteca de Epic ha llegado con otra forma.")
        records.extend(record for record in data.get("records") or [] if isinstance(record, dict))
        cursor = (data.get("responseMetadata") or {}).get("nextCursor")
        if not cursor:
            break
        params = {"includeMetadata": "true", "cursor": cursor}
    return records


def playtimes(token, account_id):
    """Los segundos jugados que ha contado el lanzador, por el nombre de aplicación de cada juego (appName): {appName:
    segundos}. Lanza EpicError si falla.

    Pantallas: Editar perfil (Cuentas vinculadas › Epic Games), Perfil (biblioteca y horas).
    """
    data = _get(PLAYTIME_URL.format(account_id), token)
    if not isinstance(data, list):
        raise EpicError("Las horas de Epic han llegado con otra forma.")
    seconds = {}
    for item in data:
        if isinstance(item, dict) and item.get("artifactId") and isinstance(item.get("totalTime"), int):
            seconds[item["artifactId"]] = seconds.get(item["artifactId"], 0) + max(item["totalTime"], 0)
    return seconds


def catalog_item(token, namespace, item_id):
    """La ficha del catálogo de un artículo (title, categories y, si es un DLC, mainGameItem), en inglés, que es como
    mejor se encuentra en IGDB; None si Epic no la da.

    Pantallas: Perfil (biblioteca y horas), Juegos importados (los que no se encuentran).
    """
    params = {
        "id": item_id, "includeDLCDetails": "true", "includeMainGameDetails": "true", "country": "US", "locale": "en-US",
    }
    try:
        data = _get(CATALOG_URL.format(namespace), token, params)
    except EpicError:
        return None
    item = data.get(item_id) if isinstance(data, dict) else None
    return item if isinstance(item, dict) else None


def is_game(item):
    """Si una ficha del catálogo es un juego: no un DLC o una expansión (que llevan mainGameItem) ni nada de NOT_GAMES.

    Pantallas: Perfil (biblioteca y horas).
    """
    if item.get("mainGameItem"):
        return False
    categories = [category for category in item.get("categories") or [] if isinstance(category, dict)]
    return not any(str(category.get("path") or "").split("/")[0] in NOT_GAMES for category in categories)


def owned_games(token, records, seconds, include_unplayed=False):
    """Los juegos de la biblioteca con su nombre del catálogo y sus minutos: [{"name", "minutes"}]. Se salta la tienda
    de Unreal, lo que es de pruebas (sandboxType «PRIVATE») y lo que no es un juego (is_game). Sin include_unplayed,
    solo pide la ficha de los que tienen horas: Epic regala muchos juegos que nunca se llegan a abrir.

    Pantallas: Perfil (biblioteca y horas), Juegos importados (los que no se encuentran).
    """
    wanted = {}  # (espacio de nombres, ID del catálogo) → appName
    for record in records:
        namespace, item_id, app = record.get("namespace"), record.get("catalogItemId"), record.get("appName")
        if not namespace or not item_id or not app or namespace in SKIP_NAMESPACES:
            continue
        if record.get("sandboxType") == "PRIVATE" or not (include_unplayed or seconds.get(app)):
            continue
        wanted.setdefault((namespace, item_id), app)

    def fetch(key):
        """La ficha del catálogo de un artículo de wanted."""
        return catalog_item(token, *key)

    with ThreadPoolExecutor(CATALOG_WORKERS) as pool:
        details = list(pool.map(fetch, wanted))
    games = []
    for app, item in zip(wanted.values(), details):
        if item is None or not item.get("title") or not is_game(item):
            continue
        games.append({"name": str(item["title"]), "minutes": round(seconds.get(app, 0) / 60)})
    return games


def _close(token):
    """Cierra la sesión del lanzador que se ha abierto con el código, para que ya no sirva aunque se filtrara. Si
    falla no pasa nada: la importación ya está hecha y la sesión caduca sola.

    Pantallas: Editar perfil (Cuentas vinculadas › Epic Games).
    """
    try:
        requests.delete(CLOSE_URL.format(token), headers={"Authorization": f"bearer {token}"}, timeout=TIMEOUT)
    except requests.RequestException:
        pass


def import_library(user, code, include_unplayed=False):
    """Vincula (o actualiza) la cuenta de Epic del código e importa sus juegos con sus horas (csv_import.import_items).

    Con la sesión que da el código lee la biblioteca, las horas y el nombre de cada juego, y la cierra al acabar, vaya
    bien o mal. Los juegos sin horas solo entran con include_unplayed, como «Pendiente». La primera vez, si no tenía
    ningún nombre de Epic en «Otras plataformas», pone el suyo, para que se vea en su perfil. Lanza EpicAuthError si el
    código no vale, EpicError si falla Epic y ValueError si esa cuenta de Epic ya es de otro usuario. Devuelve lo que
    devuelve import_items, más la cuenta («account»).

    Pantallas: Editar perfil (Cuentas vinculadas › Epic Games), Perfil (modal «Vincular cuentas» de su dueño;
    biblioteca y horas; cuentas de la cabecera), Juegos importados (los que no se encuentran), Inicio (feed: la
    importación), Retos (progreso), Página del reto (progreso).
    """
    session = start_session(code)
    token, account_id = session["access_token"], str(session["account_id"])
    try:
        if EpicAccount.objects.filter(account_id=account_id).exclude(user=user).exists():
            raise ValueError("Esa cuenta de Epic Games ya está vinculada a otro usuario.")
        games = owned_games(token, library_records(token), playtimes(token, account_id), include_unplayed)
    finally:
        _close(token)
    rows = [
        csv_import.make_item(clean_name(game["name"]), ExternalTitle.Platform.EPIC, game["minutes"], None, None, None)
        for game in games
    ]
    result = csv_import.import_items(user, rows)
    name = str(session.get("displayName") or "")[:64]
    with transaction.atomic():
        account, created = EpicAccount.objects.update_or_create(
            user=user,
            defaults={"account_id": account_id, "display_name": name, "synced_at": timezone.now(), "games": len(rows)},
        )
        if created and PlatformAccount.is_valid_handle(PlatformAccount.Platform.EPIC, name):
            PlatformAccount.objects.get_or_create(
                user=user, platform=PlatformAccount.Platform.EPIC, defaults={"handle": name},
            )
    return {**result, "account": account}
