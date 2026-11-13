"""Importar la biblioteca de GOG desde el perfil público del usuario, sin iniciar sesión ni instalar nada.

GOG no tiene API para webs, pero su perfil público (gog.com/u/<usuario>) da todos sus juegos con los minutos y la
última partida que cuenta GOG Galaxy: es la misma dirección que lee la extensión de GOG del propio Playnite. Solo hace
falta el nombre de usuario y que el perfil sea público; no se guarda ninguna contraseña ni token. Cada juego se importa
como una fila de un archivo (accounts.csv_import): emparejado por su nombre y, si han subido sus horas, con una sesión
de juego. Se lee al vincularlo en Editar perfil y cada noche (comando sync_gog). Está sin documentar, como las de Steam
y PlayStation: GOG puede cambiarla sin avisar.

Pantallas: Editar perfil (Cuentas vinculadas › GOG), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y
horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto
(progreso).
"""

import re
import time

import requests
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.spanish import USER_AGENT

from . import csv_import
from .matching import clean_name
from .models import ExternalTitle, GOGAccount, PlatformAccount

# Los juegos del perfil público, de 50 en 50 y de lo más jugado últimamente a lo menos.
STATS_URL = "https://www.gog.com/u/{}/games/stats"
# Un usuario de GOG (lo mismo que se pide en «Otras plataformas») y la dirección de un perfil, de la que se saca.
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")
PROFILE_URL = re.compile(r"gog\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?u/([^/?#\s]+)", re.IGNORECASE)
TIMEOUT = 20
# Como mucho tantas páginas (5000 juegos, lo mismo que un archivo), con esta pausa entre una y otra para no cargar a GOG.
MAX_PAGES = 100
PAUSE = 0.5


class GOGError(Exception):
    """Fallo al leer el perfil de GOG (red o respuesta inesperada)."""


class GOGPrivateError(GOGError):
    """El perfil existe, pero no es público: GOG responde 403."""


class GOGMissingError(GOGError):
    """No hay ningún usuario de GOG con ese nombre: GOG responde 404."""


def clean_username(value):
    """El usuario de GOG de lo que escriba el usuario: el nombre suelto o la dirección de su perfil
    (https://www.gog.com/u/nombre), o None si no lo parece.

    Pantallas: Editar perfil (Cuentas vinculadas › GOG: el usuario escrito), Perfil (modal «Vincular cuentas» de su
    dueño).
    """
    value = (value or "").strip()
    match = PROFILE_URL.search(value)
    if match:
        value = match[1]
    return value if USERNAME.match(value) else None


def _page(username, number):
    """La página number del perfil público de username: (sus juegos tal cual los da GOG, cuántas páginas hay). Lanza
    GOGPrivateError, GOGMissingError o GOGError.

    Pantallas: Editar perfil (Cuentas vinculadas › GOG), Perfil (biblioteca y horas).
    """
    try:
        response = requests.get(
            STATS_URL.format(username),
            params={"sort": "recent_playtime", "order": "desc", "page": number},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GOGError("No se ha podido conectar con GOG.") from exc
    if response.status_code == 403:
        raise GOGPrivateError(username)
    if response.status_code == 404:
        raise GOGMissingError(username)
    if response.status_code != 200:
        raise GOGError(f"GOG ha respondido con el código {response.status_code}.")
    try:
        data = response.json()
        return list(data["_embedded"]["items"]), int(data.get("pages") or 1)
    except (ValueError, KeyError, TypeError) as exc:
        raise GOGError("GOG ha respondido algo que no se entiende.") from exc


def _when(value):
    """La fecha ISO value de GOG («2026-09-02T00:14:57+00:00»), o None si no viene o no se entiende.

    Pantallas: Perfil (biblioteca y horas).
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def fetch_games(username):
    """Todos los juegos del perfil público de username, página a página: [{"id", "title", "minutes", "last_played"}],
    con los minutos y la última partida que cuenta GOG Galaxy (0 y None si nunca lo ha abierto con él). Lanza
    GOGPrivateError si el perfil no es público, GOGMissingError si el usuario no existe y GOGError si falla otra cosa.

    Pantallas: Editar perfil (Cuentas vinculadas › GOG), Perfil (biblioteca y horas).
    """
    games, number, pages = [], 1, 1
    while number <= min(pages, MAX_PAGES):
        if number > 1:
            time.sleep(PAUSE)
        items, pages = _page(username, number)
        for item in items:
            game = item.get("game") if isinstance(item, dict) else None
            if not isinstance(game, dict) or not game.get("title"):
                continue
            # stats es {ID del usuario en GOG: {"playtime": minutos, "lastSession": fecha}}, o una lista vacía si nunca
            # lo ha jugado con GOG Galaxy.
            stats = item.get("stats")
            own = next(iter(stats.values()), None) if isinstance(stats, dict) and stats else None
            own = own if isinstance(own, dict) else {}
            minutes = own.get("playtime")
            games.append({
                "id": str(game.get("id") or ""),
                "title": str(game["title"]),
                "minutes": minutes if isinstance(minutes, int) and minutes > 0 else 0,
                "last_played": _when(own.get("lastSession")),
            })
        number += 1
    return games


def items(games, include_unplayed=False):
    """Los juegos de fetch_games como las filas de un archivo (csv_import.make_item), en la plataforma «gog». Sin
    include_unplayed, solo los que tienen minutos o última partida, como Steam sin «los que no he abierto nunca».

    Pantallas: Perfil (biblioteca y horas), Juegos importados (los que no se encuentran).
    """
    return [
        csv_import.make_item(
            clean_name(game["title"]), ExternalTitle.Platform.GOG, game["minutes"], game["last_played"], None, None,
        )
        for game in games
        if include_unplayed or game["minutes"] or game["last_played"]
    ]


def import_library(user, username, include_unplayed=False):
    """Lee el perfil público de GOG de username, importa sus juegos a la biblioteca de user (csv_import.import_items) y
    guarda la cuenta (GOGAccount), que la sincronización de cada noche vuelve a leer igual. La primera vez, si no tenía
    ningún usuario de GOG en «Otras plataformas», pone ese, para que se vea en su perfil. Lanza GOGPrivateError,
    GOGMissingError o GOGError, y entonces no cambia nada. Devuelve lo que devuelve import_items, más la cuenta
    («account») y cuántos juegos tiene el perfil («total»).

    Pantallas: Editar perfil (Cuentas vinculadas › GOG), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y
    horas; cuentas de la cabecera), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    games = fetch_games(username)
    rows = items(games, include_unplayed)
    result = csv_import.import_items(user, rows)
    with transaction.atomic():
        account, created = GOGAccount.objects.update_or_create(
            user=user,
            defaults={
                "username": username, "include_unplayed": include_unplayed, "synced_at": timezone.now(),
                "games": len(rows), "problem": "",
            },
        )
        if created:
            PlatformAccount.objects.get_or_create(
                user=user, platform=PlatformAccount.Platform.GOG, defaults={"handle": username},
            )
    return {**result, "account": account, "total": len(games)}


def sync(account):
    """La sincronización de cada noche de account (comando sync_gog): vuelve a importar su perfil con lo que eligió al
    vincularlo. Si el perfil ya no es público o el usuario ya no existe, lo apunta en account.problem, que Editar perfil
    avisa, y vuelve a lanzar el error.

    Pantallas: Editar perfil (Cuentas vinculadas › GOG: la última importación o el aviso), Perfil (biblioteca y horas),
    Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto
    (progreso).
    """
    try:
        return import_library(account.user, account.username, account.include_unplayed)
    except (GOGPrivateError, GOGMissingError) as exc:
        problem = GOGAccount.Problem.PRIVATE if isinstance(exc, GOGPrivateError) else GOGAccount.Problem.MISSING
        GOGAccount.objects.filter(pk=account.pk).update(problem=problem)
        account.problem = problem
        raise
