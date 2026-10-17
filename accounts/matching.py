"""Emparejamiento por nombre de los juegos importados (PlayStation, Xbox) con el catálogo.

Steam se empareja por su ID de app; las demás plataformas solo dan el nombre del juego, así que se
busca por nombre en el catálogo local y, si falta, en IGDB.
"""

import re

from django.db.models import Q

from games import igdb
from games.models import Game

# Búsquedas sueltas en IGDB como máximo por sincronización, para los juegos que no salen por nombre exacto.
MAX_SEARCHES = 25
# Coletillas de plataforma que las tiendas añaden al nombre: «PS4 & PS5», «(PS5)», «- PS4 Edition»,
# «Xbox One», «Xbox Series X|S», «for Windows 10», «(PC)»...
PLATFORM_SUFFIX = re.compile(
    r"\s*[-–:(]?\s*(?:"
    r"(?:PS4|PS5|PlayStation\s*[45])(?:\s*(?:&|and|y|/)\s*(?:PS4|PS5))?"
    r"|(?:Xbox\s*(?:One|Series\s*X\s*\|?\s*S|Series\s*X|360))(?:\s*(?:&|and|y|/)\s*Xbox\s*(?:One|Series\s*X\s*\|?\s*S))?"
    r"|(?:for\s+)?Windows(?:\s*10)?|PC"
    r")(?:\s*(?:Edition|Version|Versión))?\s*\)?\s*$",
    re.IGNORECASE,
)


def clean_name(name):
    """Nombre de un juego sin ™ ® © ni coletillas de plataforma, para buscarlo en el catálogo."""
    name = re.sub(r"[™®©]", "", name or "")
    name = PLATFORM_SUFFIX.sub("", name)
    return " ".join(name.split())


def normalize(name):
    """Nombre para comparar: sin ™ ® ©, en minúsculas y con los espacios simplificados."""
    return igdb._normalize(name)


def match_by_name(names):
    """Busca en el catálogo los juegos de una lista de nombres limpios y devuelve {nombre normalizado: Game}.

    Primero mira el catálogo local; después pide a IGDB los que falten por nombre exacto (tal cual y
    con mayúsculas de título, porque a veces vienen en mayúsculas) y, para los que sigan sin salir,
    hace hasta MAX_SEARCHES búsquedas sueltas quedándose solo con resultados de nombre idéntico.
    """
    wanted = {normalize(name): name for name in names if name}
    matches = {}

    def take(games):
        """Guarda en matches los juegos cuyo nombre normalizado se buscaba (el primero que llegue, el más popular)."""
        for game in games:
            key = normalize(game.name)
            if key in wanted and key not in matches:
                matches[key] = game

    keys = list(wanted)
    for start in range(0, len(keys), 100):
        query = Q()
        for key in keys[start:start + 100]:
            query |= Q(name__iexact=wanted[key])
        take(Game.objects.filter(query))

    if not igdb.is_configured():
        return matches
    client = igdb.IGDBClient()
    missing = [wanted[key] for key in wanted if key not in matches]
    if missing:
        variants = missing + [name.title() for name in missing]
        take(igdb.upsert_games(client.games_by_names(variants)))
    for name in [wanted[key] for key in wanted if key not in matches][:MAX_SEARCHES]:
        found = [data for data in client.search(name, limit=5) if normalize(data["name"]) == normalize(name)]
        if found:
            take(igdb.upsert_games(found[:1]))
    return matches
