"""Emparejamiento de los juegos importados (Steam, PlayStation, Xbox) con el catálogo y su paso a la biblioteca.

Cada título importado se guarda en ExternalTitle (compartido por todos los usuarios) y en ImportedTitle
(del usuario, con sus minutos). Los títulos ya emparejados —por una importación anterior o porque
alguien lo corrigió a mano— no se vuelven a buscar; los demás se buscan con la función de cada
plataforma: Steam por su ID de app y las demás por nombre, sin fijarse en cómo está escrito (name_key), en el
catálogo local y, si falta, en IGDB. Las horas de cada plataforma en la biblioteca salen después de todos los
ImportedTitle del usuario.
"""

import re
import unicodedata
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from challenges import progress as challenge_progress
from games import igdb, spanish
from games.models import Game
from library.models import LibraryEntry, PlaySession, Review
from library.reminders import remind_outdated_reviews
from social.activity import games_imported

from .models import ExternalTitle, ImportedTitle

# Búsquedas sueltas en IGDB como máximo por sincronización, para los juegos que no salen por nombre exacto, y
# resultados de cada una entre los que se busca el que se llama como el título.
MAX_SEARCHES = 25
SEARCH_RESULTS = 10
# Títulos que no son un juego concreto sino un lanzador de varios (p. ej. «Call of Duty®» en PS5, desde el que se
# abren MWII, MWIII y Black Ops 6). Se cuentan como no encontrados.
LAUNCHERS = {"call of duty"}
# Año o región al final del nombre de un juego («resident evil 4 (2005)», «LittleBigPlanet3 (LATAM)»), que su lista de
# trofeos y el catálogo no suelen llevar.
YEAR_OR_REGION = re.compile(r"\s*\((\d{4}|[A-Z]{2,5})\)\s*$")
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
    """Nombre de un juego sin ™ ® © ni coletillas de plataforma, para buscarlo en el catálogo.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    name = re.sub(r"[™®©]", "", name or "")
    name = PLATFORM_SUFFIX.sub("", name)
    return " ".join(name.split())


def normalize(name):
    """Nombre para comparar: sin ™ ® ©, en minúsculas y con los espacios simplificados.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    return igdb._normalize(name)


def name_key(name):
    """Nombre para comparar sin fijarse en cómo está escrito: sin ™ ® ©, signos, espacios, tildes ni mayúsculas, para que
    «WATCH_DOGS 2» y «Watch Dogs 2», «Tom Clancy’s» y «Tom Clancy's» o «Ghostrunner2» y «Ghostrunner 2» sean el mismo.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    # Fuera ™ ® © antes de separar las tildes, que NFKD convierte ™ en «TM».
    name = unicodedata.normalize("NFKD", re.sub(r"[™®©]", "", name or ""))
    return re.sub(r"[\W_]+", "", "".join(char for char in name if not unicodedata.combining(char)).casefold())


def _name_keys(name, name_es, search_names):
    """Claves (name_key) de todos los nombres de un juego: el suyo, el de España y los otros que guarda para el buscador.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    return {name_key(other) for other in (name, name_es, *search_names.split(" | "))} - {""}


def _catalog_games(keys):
    """Juegos del catálogo que tienen alguna de las claves keys entre sus nombres, con sus plataformas ya cargadas.

    La comparación no cabe en una consulta, así que recorre los nombres de todo el catálogo. Solo se hace cuando hay
    títulos que buscar, no en cada visita: si un día el catálogo tiene cientos de miles de juegos, habrá que guardar las claves.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    rows = Game.objects.values_list("pk", "name", "name_es", "search_names").iterator()
    ids = [pk for pk, *names in rows if keys & _name_keys(*names)]
    return Game.objects.filter(pk__in=ids).prefetch_related("platforms")


def _title_parts(name):
    """Lo que se busca de un título: (clave de su nombre, clave sin el año o la región del final, nombre sin ellos, año o None).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    stem = YEAR_OR_REGION.sub("", name)
    suffix = YEAR_OR_REGION.search(name)
    return name_key(name), name_key(stem), stem, int(suffix[1]) if suffix and suffix[1].isdigit() else None


def add_title(titles, external_id, name, minutes=0, last_played=None, platforms=(), first_played=None, deck_minutes=0):
    """Añade un título importado al dict titles ({ID externo: datos}).

    Si el ID ya estaba (el mismo juego en dos cuentas de Steam, o dos versiones con el mismo nombre)
    suma los minutos (y los jugados en una Steam Deck), se queda con la última partida más reciente y con
    la primera más antigua, y junta las plataformas, que son nombres de plataforma de IGDB («PlayStation 5»,
    «Xbox One»...): sirven para desempatar al buscar por nombre y para rellenar la plataforma de las reseñas.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
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
    """Nombres de las plataformas del juego (de IGDB), aprovechando el prefetch si lo hay.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    return {platform.name for platform in game.platforms.all()}


def _fits(game, platforms):
    """Indica si el juego puede ser uno jugado en esas plataformas.

    Sin plataformas conocidas (del título o del juego) cualquiera vale.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    return _share_platform(_platform_names(game), platforms)


def _share_platform(names, platforms):
    """Indica si un juego que sale en las plataformas names puede ser uno jugado en platforms (ver _fits).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    return not platforms or not names or bool(set(names) & set(platforms))


def _seen_games(keys):
    """Juegos de los títulos ya emparejados, de cualquier plataforma, que se llaman como alguna de las claves keys, como
    {clave: [Game]}: Steam los empareja por su ID de app y los demás pueden estar corregidos a mano, así que son una pista fiable.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    ids = defaultdict(set)
    for name, game_id in ExternalTitle.objects.filter(game__isnull=False).values_list("name", "game_id").iterator():
        key = name_key(clean_name(name))
        if key in keys:
            ids[key].add(game_id)
    games = Game.objects.filter(pk__in={game_id for found in ids.values() for game_id in found}).prefetch_related("platforms")
    by_id = {game.pk: game for game in games}
    seen = defaultdict(list)
    seen.update({key: [by_id[game_id] for game_id in found] for key, found in ids.items()})
    return seen


def _score(game, group, platforms, year):
    """Lo bueno que es game para un título, para quedarse con el mayor: primero que sea del año que dice el título, después
    que salga en sus plataformas, después cómo se ha encontrado (group: cuanto más alto, más se parece el nombre), después
    el año más cercano y, por último, el más popular.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    released = game.release_year
    return (
        year is not None and released == year,
        _fits(game, platforms),
        group,
        -abs(released - year) if year and released else 0,
        game.popularity,
    )


def match_by_name(titles):
    """Busca en el catálogo los juegos de {clave: (nombre limpio, plataformas)} y devuelve {clave: Game}.

    Los nombres se comparan sin fijarse en cómo están escritos (name_key), con el nombre de cada juego, con sus otros
    nombres (el de España y los alternativos que guarda para el buscador) y con los títulos ya emparejados de otras
    plataformas (_seen_games). Un título que acaba en un año o una región («resident evil 4 (2005)», «LittleBigPlanet3
    (LATAM)») también vale sin ellos. Primero mira lo que ya hay; después pide a IGDB por nombre exacto (tal cual y con
    mayúsculas de título, porque a veces vienen en mayúsculas) los que no tengan ningún juego que se llame así en sus
    plataformas y, para los que sigan sin ningún candidato, hace hasta MAX_SEARCHES búsquedas sueltas quedándose solo con
    los resultados que tienen ese nombre entre los suyos y salen en sus plataformas. Los lanzadores (LAUNCHERS) no se
    buscan. Si varios juegos encajan (remakes como God of War o Resident Evil 4), decide _score.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    wanted = {
        key: _title_parts(name) for key, (name, _) in titles.items()
        if name_key(name) and normalize(name) not in LAUNCHERS
    }
    keys = {key for full, stem, *_ in wanted.values() for key in (full, stem)}
    # Clave → juegos que se llaman así (named) y juegos que también se llaman así, con otro de sus nombres (aliased).
    named, aliased = defaultdict(list), defaultdict(list)
    seen = _seen_games(keys)

    def take(games):
        """Apunta cada juego como candidato de las claves buscadas que tiene entre sus nombres, sin repetir."""
        for game in games:
            own = name_key(game.name)
            for key in keys & _name_keys(game.name, game.name_es, game.search_names):
                found = named[key] if key == own else aliased[key]
                if game not in found:
                    found.append(game)

    def options(key):
        """Candidatos de un título como (juego, grupo), con el grupo de peor a mejor: sin el año o la región y con ellos, y
        en cada caso, con otro nombre o con el suyo. Lo que no se llama así de verdad solo vale si sale en sus plataformas."""
        full, stem, *_ = wanted[key]
        platforms = titles[key][1]
        groups = (
            [game for game in aliased[stem] if _fits(game, platforms)], named[stem],
            [game for game in aliased[full] + seen[full] if _fits(game, platforms)], named[full],
        )
        return [(game, group) for group, games in enumerate(groups) for game in games]

    take(_catalog_games(keys))
    if igdb.is_configured():
        client = igdb.IGDBClient()
        # Sin ningún juego que se llame así en sus plataformas: con otro nombre puede ser otro juego, así que se pregunta.
        missing = [
            stem_name for key, (full, stem, stem_name, _) in wanted.items()
            if not any(_fits(game, titles[key][1]) for game in named[full] + named[stem])
        ]
        missing = list(dict.fromkeys(missing))
        if missing:
            take(igdb.upsert_games(client.games_by_names(missing + [name.title() for name in missing])))
        empty = {}  # nombre sin candidatos → plataformas de sus títulos
        for key, (_, _, stem_name, _) in wanted.items():
            if not options(key):
                empty.setdefault(stem_name, set()).update(titles[key][1])
        for name, platforms in list(empty.items())[:MAX_SEARCHES]:
            found = [
                data for data in client.search(name, limit=SEARCH_RESULTS)
                if name_key(name) in _name_keys(data.get("name"), *spanish.spanish_names(data))
                and _share_platform({item.get("name") for item in data.get("platforms") or []}, platforms)
            ]
            if found:
                take(igdb.upsert_games(found))

    matches = {}
    for key, (*_, year) in wanted.items():
        found = options(key)
        if found:
            matches[key] = max(found, key=lambda option: _score(*option, titles[key][1], year))[0]
    return matches


def save_titles(user, platform, titles, auto_match):
    """Guarda los títulos importados de un usuario en una plataforma y devuelve {ID externo: Game o None}.

    titles es el dict que llena add_title. Los títulos que ya tienen juego, o que alguien dejó sin
    juego a mano, se respetan; los demás se buscan con auto_match(ids), que devuelve {ID: Game}. Si lo
    encontrado es una edición de otro juego (la «Game of the Year Edition» que vende Steam con su propio
    ID), el título se queda con el juego (Game.main), para que sus horas cuenten en él. Los minutos, las
    partidas y las plataformas del usuario se sobrescriben con los de esta importación.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    ids = list(titles)
    known = {t.external_id: t for t in ExternalTitle.objects.filter(platform=platform, external_id__in=ids)}
    pending = [
        i for i in ids
        if i not in known or (known[i].game_id is None and known[i].source == ExternalTitle.Source.AUTO)
    ]
    found = {i: game.main for i, game in (auto_match(pending) if pending else {}).items()}

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


def update_library(user, platform, opened_only=False, record_activity=False):
    """Pasa a la biblioteca las horas de una plataforma a partir de todos los títulos importados del usuario.

    Por juego suma los minutos de sus títulos (puede venir de varias cuentas o de la versión de PS4 y
    la de PS5) y toma la última partida más reciente. Los juegos nuevos entran como «Jugando» si se
    jugaron en los últimos días y como «Jugado» si no; sin horas ni partida, como «Pendiente», salvo
    con opened_only (plataformas que solo listan juegos abiertos alguna vez). Los que ya estaban
    conservan su estado (salvo «Pendiente», que pasa a jugado si hay horas). Si un título ha cambiado
    de juego desde la última vez (porque se corrigió el emparejamiento), al juego anterior se le quitan
    sus horas de esta plataforma. Si han cambiado horas, avisa al usuario de las reseñas que se le han quedado
    atrás (library.reminders). Con record_activity (lo piden las importaciones, no las correcciones del
    emparejamiento), los juegos nuevos se apuntan en su actividad como una sola importación
    (social.activity.games_imported) y lo que ha subido el tiempo de los que ya tenían horas de esta plataforma se
    apunta como sesiones de juego (library.models.PlaySession), con las que se mira si ha conseguido algún reto de
    horas (challenges.progress.check). Devuelve (juegos añadidos, juegos actualizados).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas),
    Ficha del juego (tus horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso), Panel de administración (al corregir un título importado).
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
    # (juego, última partida, minutos) de lo que ha subido el tiempo de los juegos que ya tenían horas de esta plataforma.
    played = []
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
                continue
            before = getattr(entry, minutes_field)
            if entry.update_import(platform, minutes, last_played):
                updated += 1
                if before is not None and minutes > before:
                    played.append((game_id, last_played, minutes - before))
        for game_id in stale:
            entry = existing.get(game_id)
            if entry is not None and entry.update_import(platform, None, None):
                updated += 1
        LibraryEntry.objects.bulk_create(new)
        for row in moved:
            row.game_id = row.title.game_id
        ImportedTitle.objects.bulk_update(moved, ["game"])
        if record_activity:
            for game_id, last_played, minutes in played:
                day = timezone.localdate(last_played) if last_played and timezone.is_aware(last_played) else timezone.localdate()
                PlaySession.add(user, game_id, platform, day, minutes)
    if record_activity:
        games_imported(user, platform, new)
        if played:
            challenge_progress.check(user)
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

    Pantallas: Juegos importados, Perfil (biblioteca y horas), Ficha del juego (tus horas).
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
