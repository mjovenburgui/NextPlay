"""Lo que manda la extensión NextPlay Sync de Playnite (la carpeta playnite-extension/): la biblioteca entera al abrir
Playnite y una vez al día, o un solo juego al cerrar una partida. Cada juego se lee como una fila de un archivo
(accounts.csv_import) y se importa igual: por tienda, emparejado por su nombre y, si han subido sus horas, con una
sesión de juego del día en que se jugó.

Formato (JSON): {"version": 1, "kind": "biblioteca" o "partida", "games": [{"id", "name", "source", "platforms",
"playtime" (segundos), "last_activity" (ISO en UTC), "completion_status", "user_score" (de 0 a 100), "hidden"}]}.

Pantallas: Editar perfil (Cuentas vinculadas › Playnite), Perfil (biblioteca y horas), Juegos importados (los que no
se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto (progreso).
"""

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import csv_import
from .matching import clean_name
from .models import ExternalTitle

# Como mucho tantos juegos por envío, lo mismo que en un archivo.
MAX_GAMES = csv_import.MAX_ROWS
# Lo que manda la extensión cuando envía la biblioteca entera; con «partida» manda solo el juego que se acaba de cerrar.
LIBRARY = "biblioteca"


class PayloadError(ValueError):
    """Lo que ha llegado no tiene la forma de lo que manda la extensión."""


def _number(value):
    """value si es un número (no un sí o un no, que en Python también lo son), o None.

    Pantallas: Perfil (biblioteca y horas).
    """
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _when(value):
    """La fecha ISO value (en UTC, como la manda la extensión), o None si no viene o no se entiende: una fecha rara no
    estropea el resto del envío.

    Pantallas: Perfil (biblioteca y horas).
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def items(games):
    """Los juegos que manda la extensión, como las filas de csv_import.interpret (csv_import.make_item). Los que no
    tienen tienda (añadidos a mano o emulados) van a «Otras tiendas»; los de Steam, PlayStation y Xbox se saltan, porque
    se importan desde su cuenta. Lanza PayloadError si games no es una lista.

    Pantallas: Perfil (biblioteca y horas), Juegos importados (los que no se encuentran).
    """
    if not isinstance(games, list):
        raise PayloadError("Falta la lista de juegos.")
    rows = []
    for game in games[:MAX_GAMES]:
        if not isinstance(game, dict):
            continue
        seconds, score = _number(game.get("playtime")), _number(game.get("user_score"))
        rows.append(csv_import.make_item(
            clean_name(str(game.get("name") or "")),
            csv_import.store_of(str(game.get("source") or ""), ExternalTitle.Platform.OTHER),
            round(seconds / 60) if seconds is not None and seconds >= 0 else None,
            _when(game.get("last_activity")),
            csv_import.status_of(str(game.get("completion_status") or "")),
            csv_import.rating_of(str(score), "100") if score is not None else None,
        ))
    return rows


def sync(account, payload):
    """Importa lo que ha mandado la extensión de account (payload, con el formato de arriba) con
    csv_import.import_items y apunta cuándo y, si era la biblioteca entera, cuántos juegos se importan de ella. Devuelve
    lo que devuelve import_items. Lanza PayloadError si no tiene la forma esperada.

    Pantallas: Editar perfil (Cuentas vinculadas › Playnite: la última sincronización), Perfil (biblioteca y horas),
    Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto
    (progreso).
    """
    if not isinstance(payload, dict):
        raise PayloadError("No es un objeto JSON.")
    rows = items(payload.get("games"))
    result = csv_import.import_items(account.user, rows)
    account.synced_at = timezone.now()
    fields = ["synced_at"]
    if payload.get("kind", LIBRARY) == LIBRARY:
        account.games = sum(1 for row in rows if not row["skip"])
        fields.append("games")
    account.save(update_fields=fields)
    return result
