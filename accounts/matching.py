"""Emparejamiento de los juegos importados (Steam, PlayStation, Xbox) con el catálogo y su paso a la biblioteca.

Cada título importado se guarda en ExternalTitle (compartido por todos los usuarios) y en ImportedTitle
(del usuario, con sus minutos). Los títulos ya emparejados —por una importación anterior o porque
alguien lo corrigió a mano— no se vuelven a buscar; los demás se buscan con la función de cada
plataforma: Steam por su ID de app y las demás por nombre (en el catálogo local y, si falta, en IGDB).
Las horas de cada plataforma en la biblioteca salen después de todos los ImportedTitle del usuario.
"""

import re

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from games import igdb
from games.models import Game
from library.models import LibraryEntry, Review
from library.reminders import remind_outdated_reviews

from .models import ExternalTitle, ImportedTitle

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


def add_title(titles, external_id, name, minutes=0, last_played=None, platforms=(), first_played=None, deck_minutes=0):
    """Añade un título importado al dict titles ({ID externo: datos}).

    Si el ID ya estaba (el mismo juego en dos cuentas de Steam, o dos versiones con el mismo nombre)
    suma los minutos (y los jugados en una Steam Deck), se queda con la última partida más reciente y con
    la primera más antigua, y junta las plataformas, que son nombres de plataforma de IGDB («PlayStation 5»,
    «Xbox One»...): sirven para desempatar al buscar por nombre y para rellenar la plataforma de las reseñas.
    """
    title = titles.setdefault(
        str(external_id),
        {"name": name, "minutes": 0, "deck_minutes": 0, "last_played": None, "first_played": None, "platforms": set()},
    )
    title["minutes"] += minutes or 0
    title["deck_minutes"] += deck_minutes or 0
    if last_played and (title["last_played"] is None or last_played > title["last_played"]):
        title["last_played"] = last_played
    if first_played and (title["first_played"] is None or first_played < title["first_played"]):
        title["first_played"] = first_played
    title["platforms"].update(platforms)


def _platform_names(game):
    """Nombres de las plataformas del juego (de IGDB), aprovechando el prefetch si lo hay."""
    return {platform.name for platform in game.platforms.all()}


def _fits(game, platforms):
    """Indica si el juego puede ser uno jugado en esas plataformas.

    Sin plataformas conocidas (del título o del juego) cualquiera vale.
    """
    names = _platform_names(game)
    return not platforms or not names or bool(names & set(platforms))


def match_by_name(titles):
    """Busca en el catálogo los juegos de {clave: (nombre limpio, plataformas)} y devuelve {clave: Game}.

    Primero mira el catálogo local; después pide a IGDB por nombre exacto (tal cual y con mayúsculas
    de título, porque a veces vienen en mayúsculas) los que no tengan ningún candidato de sus
    plataformas y, para los que sigan sin salir, hace hasta MAX_SEARCHES búsquedas sueltas quedándose
    solo con resultados de nombre idéntico. Si varios juegos se llaman igual (remakes como God of War
    o Resident Evil 4) gana el más popular de los que salieron en esas plataformas.
    """
    wanted = {normalize(name): name for name, _ in titles.values() if name}
    candidates = {key: [] for key in wanted}

    def take(games):
        """Apunta como candidatos los juegos cuyo nombre normalizado se buscaba, sin repetir."""
        for game in games:
            key = normalize(game.name)
            if key in candidates and game not in candidates[key]:
                candidates[key].append(game)

    def unfit():
        """Nombres buscados que aún no tienen ningún candidato de las plataformas de sus títulos."""
        keys = set()
        for name, platforms in titles.values():
            key = normalize(name)
            if key in wanted and not any(_fits(game, platforms) for game in candidates[key]):
                keys.add(key)
        return [wanted[key] for key in wanted if key in keys]

    keys = list(wanted)
    for start in range(0, len(keys), 100):
        query = Q()
        for key in keys[start:start + 100]:
            query |= Q(name__iexact=wanted[key])
        take(Game.objects.filter(query).prefetch_related("platforms"))

    if igdb.is_configured():
        client = igdb.IGDBClient()
        missing = unfit()
        if missing:
            take(igdb.upsert_games(client.games_by_names(missing + [name.title() for name in missing])))
        for name in [name for name in unfit() if not candidates[normalize(name)]][:MAX_SEARCHES]:
            found = [data for data in client.search(name, limit=5) if normalize(data["name"]) == normalize(name)]
            if found:
                take(igdb.upsert_games(found[:1]))

    matches = {}
    for key, (name, platforms) in titles.items():
        options = candidates.get(normalize(name)) or []
        fitting = [game for game in options if _fits(game, platforms)]
        if options:
            matches[key] = max(fitting or options, key=lambda game: game.popularity)
    return matches


def save_titles(user, platform, titles, auto_match):
    """Guarda los títulos importados de un usuario en una plataforma y devuelve {ID externo: Game o None}.

    titles es el dict que llena add_title. Los títulos que ya tienen juego, o que alguien dejó sin
    juego a mano, se respetan; los demás se buscan con auto_match(ids), que devuelve {ID: Game}. Los
    minutos, las partidas y las plataformas del usuario se sobrescriben con los de esta importación.
    """
    ids = list(titles)
    known = {t.external_id: t for t in ExternalTitle.objects.filter(platform=platform, external_id__in=ids)}
    pending = [
        i for i in ids
        if i not in known or (known[i].game_id is None and known[i].source == ExternalTitle.Source.AUTO)
    ]
    found = auto_match(pending) if pending else {}

    now = timezone.now()
    new, changed = [], []
    for i in ids:
        name = (titles[i]["name"] or i)[:255]
        title = known.get(i)
        if title is None:
            new.append(ExternalTitle(platform=platform, external_id=i, name=name, game=found.get(i)))
        elif title.name != name or (title.game_id is None and i in found):
            title.name = name
            if title.game_id is None and i in found:
                title.game = found[i]
            title.updated_at = now
            changed.append(title)
    with transaction.atomic():
        # ignore_conflicts: otra importación del mismo juego puede haberlo creado mientras tanto.
        ExternalTitle.objects.bulk_create(new, ignore_conflicts=True)
        ExternalTitle.objects.bulk_update(changed, ["name", "game", "updated_at"])
        saved = {
            t.external_id: t
            for t in ExternalTitle.objects.filter(platform=platform, external_id__in=ids).select_related("game")
        }
        ImportedTitle.objects.bulk_create(
            [
                ImportedTitle(
                    user=user, title=saved[i], minutes=titles[i]["minutes"], last_played=titles[i]["last_played"],
                    first_played=titles[i].get("first_played"), platforms=sorted(titles[i].get("platforms") or ()),
                    deck_minutes=titles[i].get("deck_minutes") or 0,
                )
                for i in ids
            ],
            update_conflicts=True,
            unique_fields=["user", "title"],
            update_fields=["minutes", "last_played", "first_played", "platforms", "deck_minutes", "imported_at"],
        )
    return {i: saved[i].game for i in ids}


def update_library(user, platform, opened_only=False):
    """Pasa a la biblioteca las horas de una plataforma a partir de todos los títulos importados del usuario.

    Por juego suma los minutos de sus títulos (puede venir de varias cuentas o de la versión de PS4 y
    la de PS5) y toma la última partida más reciente. Los juegos nuevos entran como «Jugando» si se
    jugaron en los últimos días y como «Jugado» si no; sin horas ni partida, como «Pendiente», salvo
    con opened_only (plataformas que solo listan juegos abiertos alguna vez). Los que ya estaban
    conservan su estado (salvo «Pendiente», que pasa a jugado si hay horas). Si un título ha cambiado
    de juego desde la última vez (porque se corrigió el emparejamiento), al juego anterior se le quitan
    sus horas de esta plataforma. Si han cambiado horas, avisa al usuario de las reseñas que se le han quedado
    atrás (library.reminders). Devuelve (juegos añadidos, juegos actualizados).
    """
    rows = list(ImportedTitle.objects.filter(user=user, title__platform=platform).select_related("title"))
    totals = {}
    for row in rows:
        if row.title.game_id is None:
            continue
        old_minutes, old_last = totals.get(row.title.game_id, (0, None))
        newest = max((d for d in (old_last, row.last_played) if d), default=None)
        totals[row.title.game_id] = (old_minutes + row.minutes, newest)
    moved = [row for row in rows if row.game_id != row.title.game_id]
    stale = {row.game_id for row in moved if row.game_id is not None} - set(totals)

    minutes_field, last_field = f"{platform}_playtime_minutes", f"{platform}_last_played"
    existing = {
        entry.game_id: entry
        for entry in LibraryEntry.objects.filter(user=user, game_id__in=list(totals) + list(stale))
    }
    new, updated = [], 0
    with transaction.atomic():
        for game_id, (minutes, last_played) in totals.items():
            entry = existing.get(game_id)
            if entry is None:
                status = LibraryEntry.imported_status(minutes, last_played)
                if opened_only and status == LibraryEntry.Status.BACKLOG:
                    status = LibraryEntry.Status.PLAYED
                new.append(LibraryEntry(
                    user=user, game_id=game_id, status=status, **{minutes_field: minutes, last_field: last_played}
                ))
            elif entry.update_import(platform, minutes, last_played):
                updated += 1
        for game_id in stale:
            entry = existing.get(game_id)
            if entry is not None and entry.update_import(platform, None, None):
                updated += 1
        LibraryEntry.objects.bulk_create(new)
        for row in moved:
            row.game_id = row.title.game_id
        ImportedTitle.objects.bulk_update(moved, ["game"])
    if updated:
        remind_outdated_reviews(user)
    return len(new), updated


def reassign(imported, game, by):
    """Cambia el juego de un título importado (None = ninguno) para todos y recoloca las horas de quien lo cambia.

    Lo hecho por un administrador queda como suyo («admin»); lo de los demás, como «user». Los demás
    usuarios con ese título lo verán en su próxima importación. Si el juego anterior se queda en la
    biblioteca de quien lo cambia sin horas, valoración ni reseña, se quita: solo estaba por el error.
    Lanza PermissionError si el título está bloqueado y quien lo cambia no es administrador.

    Devuelve un dict con los juegos añadidos («added») y actualizados («updated») en su biblioteca y
    si se ha quitado el juego anterior («removed»).
    """
    title = imported.title
    if title.locked and not by.is_staff:
        raise PermissionError("Un administrador ha revisado este título y ya no se puede cambiar.")
    previous = title.game
    title.game = game
    title.source = ExternalTitle.Source.ADMIN if by.is_staff else ExternalTitle.Source.USER
    title.assigned_by = by
    with transaction.atomic():
        title.save()
        added, updated = update_library(
            imported.user, title.platform, opened_only=title.platform != ExternalTitle.Platform.STEAM
        )
        removed = False
        if previous is not None and previous != game:
            entry = LibraryEntry.objects.filter(user=imported.user, game=previous).first()
            reviewed = Review.objects.filter(user=imported.user, game=previous).exists()
            if entry and entry.playtime_minutes is None and not entry.rating and not reviewed:
                entry.delete()
                removed = True
    return {"added": added, "updated": updated, "removed": removed}
