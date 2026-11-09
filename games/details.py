"""Ficha técnica, duración, ediciones y juegos relacionados de cada juego, sacados de IGDB.

Una pasada a IGDB (IGDBClient.games_with_details y time_to_beat: unas pocas peticiones para todo el catálogo con el
comando fill_game_fields, que cada noche pone al día las que tienen más de MAX_AGE, o las de un juego al abrir su
ficha si nunca se han pedido) guarda:

- en campos de Game, lo que se filtra en el Catálogo: tipo, estado, modos, temas, perspectivas, estudios, editoras,
  nota de los usuarios de IGDB, duración (ttb_*) y, si es una edición de otro juego, cuál (parent);
- en Game.details, lo que la ficha solo enseña:

    {"pegi": "18", "sagas": ["Batman: Arkham"], "engines": ["Unreal Engine 3"],
     "coop": {"online": True, "online_max": 2, "local": True, "local_max": 2, "split": True, "campaign": True},
     "url": "https://www.igdb.com/games/…", "parent": juego, "editions": [juego + «title»],
     "related": {"remakes": [juego], "dlcs": [juego], …}, "similar": [ids de IGDB]}

  donde cada juego es {"id", "name", "cover", "year", "type"}: su id de IGDB, nombre, portada, año y tipo (GameType).
  «title» es el nombre de la edición («Game of the Year Edition»); «parent», el juego del que este es remake, DLC,
  port… (parent_game de IGDB), y «coop», None si IGDB no dice que tenga cooperativo.

Pantallas: Ficha del juego (ficha técnica, Cuánto dura, «Expansiones y DLC», «Ediciones y versiones» y «Juegos
parecidos»), Catálogo (filtros).
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from library.models import LibraryEntry

from . import igdb
from .models import HIDDEN_TYPES, Game, GameType, half_hours, igdb_image_url

MAX_AGE = timedelta(days=30)
# Grupos de «Ediciones y versiones» después de las ediciones: título y claves de Game.details["related"].
VERSION_GROUPS = (
    ("Otras versiones", ("remakes", "remasters", "expanded_games", "ports")),
    ("Recopilatorios en los que está", ("bundles",)),
)
# «Expansiones y DLC», que van aparte y a la vista: claves de Game.details["related"], primero las expansiones.
ADDON_KEYS = ("expansions", "standalone_expansions", "dlcs")
# Lo que separa el nombre del juego del de su DLC: «Elden Ring: Shadow of the Erdtree», «Fallout 4 - Far Harbor».
NAME_SEPARATORS = ":-–—"
SIMILAR_SHOWN = 6


def missing(game):
    """Indica si la ficha técnica del juego nunca se ha pedido a IGDB (y viene de IGDB): entonces la ficha la pide con
    HTMX al abrirse. Las que se quedan viejas las pone al día fill_game_fields cada noche.

    Pantallas: Ficha del juego (si la ficha técnica va ya o se carga después con HTMX).
    """
    return bool(game.igdb_id) and game.details_updated_at is None


def update_details(games, client=None, now=None, refs=None):
    """Pide a IGDB la clasificación, la ficha técnica y la duración de esos juegos y las guarda (formato arriba).

    Va en lotes de igdb.MAX_PAGE_SIZE, con unas tres peticiones por lote (los juegos, sus ediciones y su duración).
    Los que IGDB no devuelve se quedan con la ficha vacía y la fecha de hoy, para no volver a pedirlos cada vez. Al
    final enlaza las ediciones con su juego (igdb.link_editions), trayendo de IGDB los que aún no están en el
    catálogo. refs es la caché de etiquetas de igdb.known_refs (si no se da, se carga). Devuelve las ediciones que se
    han enlazado.

    Pantallas: Ficha del juego (ficha técnica, Cuánto dura, «Ediciones y versiones» y «Juegos parecidos»; la de una
    edición lleva a la de su juego), Catálogo (filtros de tipo, modo, tema, perspectiva, estudio, nota y duración, y
    etiqueta de las tarjetas).
    """
    client = client or igdb.IGDBClient()
    now = now or timezone.now()
    refs = igdb.known_refs() if refs is None else refs
    games = [game for game in games if game.igdb_id]
    editions = []
    for start in range(0, len(games), igdb.MAX_PAGE_SIZE):
        batch = {game.igdb_id: game for game in games[start:start + igdb.MAX_PAGE_SIZE]}
        payload = {data["id"]: data for data in client.games_with_details(sorted(batch))}
        durations = client.time_to_beat(sorted(batch))
        with transaction.atomic():
            for igdb_id, game in batch.items():
                data = payload.get(igdb_id)
                fields = _apply_duration(game, durations.get(igdb_id) or {})
                game.details = _details(data) if data else {}
                game.details_updated_at = now
                fields += ["details", "details_updated_at"]
                if data:
                    igdb.classify(game, data)
                    fields += igdb.CLASSIFY_MODEL_FIELDS
                game.save(update_fields=fields)
                if data:
                    igdb.classify_relations(game, data, refs)
                    if data.get("version_parent"):
                        editions.append((game, data["version_parent"]))
    return igdb.link_editions(editions, client)


def _apply_duration(game, row):
    """Pone en game (sin guardarlo) la duración de IGDB de row (en segundos) en minutos y devuelve los campos cambiados.

    Pantallas: Ficha del juego (Cuánto dura), Catálogo (filtro y orden por duración), Repasar la biblioteca (pista
    «suele durar…»).
    """
    game.ttb_quick = _minutes(row.get("hastily"))
    game.ttb_normal = _minutes(row.get("normally"))
    game.ttb_full = _minutes(row.get("completely"))
    game.ttb_count = row.get("count") or 0
    return ["ttb_quick", "ttb_normal", "ttb_full", "ttb_count"]


def _minutes(seconds):
    """Segundos de IGDB en minutos enteros, o None si no hay.

    Pantallas: Ficha del juego (Cuánto dura), Catálogo (filtro y orden por duración).
    """
    return round(seconds / 60) if seconds else None


def _details(data):
    """Game.details a partir de lo que trae IGDB de un juego (formato arriba). De los recopilatorios se quitan sus
    ediciones, que IGDB marca a veces como recopilatorio y ya salen aparte.

    Pantallas: Ficha del juego (ficha técnica, «Ediciones y versiones» y «Juegos parecidos»).
    """
    editions = sorted(
        (item for item in data.get("editions") or [] if item.get("name")),
        key=lambda item: (item.get("first_release_date") or 0, item.get("id") or 0),
    )
    edition_ids = {item.get("id") for item in editions}
    related = {}
    for key in igdb.RELATED_GAMES:
        items = [
            _game(item) for item in data.get(key) or []
            if isinstance(item, dict) and item.get("name") and item.get("id") not in edition_ids
        ]
        if items:
            related[key] = sorted(items, key=lambda item: (item["year"] or 9999, item["name"].casefold()))
    parent = data.get("parent_game")
    return {
        "pegi": _age_rating(data.get("age_ratings"), "PEGI"),
        "sagas": _names(data.get("collections")) or _names(data.get("franchises")),
        "engines": _names(data.get("game_engines")),
        "coop": _coop(data.get("multiplayer_modes")),
        "url": data.get("url") or "",
        "parent": _game(parent) if isinstance(parent, dict) and parent.get("name") else None,
        "editions": [{**_game(item), "title": item.get("version_title") or item["name"]} for item in editions],
        "related": related,
        "similar": [game_id for game_id in data.get("similar_games") or [] if isinstance(game_id, int)],
    }


def _game(item):
    """Un juego relacionado de IGDB tal como se guarda en Game.details: {"id", "name", "cover", "year", "type"}.

    Pantallas: Ficha del juego («Ediciones y versiones» y de qué juego es remake, DLC… en la cabecera).
    """
    released = item.get("first_release_date")
    cover = item.get("cover")
    return {
        "id": item.get("id"),
        "name": (item.get("name") or "")[:255],
        "cover": cover.get("image_id", "") if isinstance(cover, dict) else "",
        "year": datetime.fromtimestamp(released, tz=dt_timezone.utc).year if released else None,
        "type": item.get("game_type"),
    }


def _age_rating(ratings, organization):
    """Clasificación por edades de un organismo («PEGI» → «18») entre las de IGDB, o vacía si no la tiene.

    Pantallas: Ficha del juego (ficha técnica: edad).
    """
    for rating in ratings or []:
        if not isinstance(rating, dict):
            continue
        if (rating.get("organization") or {}).get("name") == organization:
            return (rating.get("rating_category") or {}).get("rating") or ""
    return ""


def _names(items, limit=4):
    """Nombres de una lista de IGDB (sagas, franquicias, motores), sin repetir y como mucho limit.

    Pantallas: Ficha del juego (ficha técnica: saga y motor).
    """
    names = []
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name and name not in names:
            names.append(name)
    return names[:limit]


def _coop(modes):
    """Resumen del cooperativo de IGDB (multiplayer_modes, uno por plataforma): si se puede en línea y en local, con
    cuántos como mucho (0 si IGDB no lo dice), si hay pantalla partida y si la campaña se juega en cooperativo; None
    si ninguno dice que tenga cooperativo.

    Pantallas: Ficha del juego (ficha técnica: cooperativo).
    """
    modes = [mode for mode in modes or [] if isinstance(mode, dict)]
    online = [mode for mode in modes if mode.get("onlinecoop")]
    local = [mode for mode in modes if mode.get("offlinecoop")]
    if not online and not local:
        return None
    return {
        "online": bool(online),
        "online_max": max((mode.get("onlinecoopmax") or 0 for mode in online), default=0),
        "local": bool(local),
        "local_max": max((mode.get("offlinecoopmax") or 0 for mode in local), default=0),
        "split": any(mode.get("splitscreen") for mode in modes),
        "campaign": any(mode.get("campaigncoop") for mode in modes),
    }


def coop_parts(coop):
    """Trozos de texto del cooperativo para la ficha técnica: «en línea, hasta 2», «en local, hasta 4», «pantalla
    partida» y «toda la campaña». Vacía sin cooperativo.

    Pantallas: Ficha del juego (ficha técnica: cooperativo).
    """
    if not coop:
        return []
    parts = []
    for key, name in (("online", "en línea"), ("local", "en local")):
        if coop.get(key):
            players = coop.get(f"{key}_max") or 0
            parts.append(f"{name}, hasta {players}" if players > 1 else name)
    if coop.get("split"):
        parts.append("pantalla partida")
    if coop.get("campaign"):
        parts.append("toda la campaña")
    return parts


def sheet(game):
    """Filas de la ficha técnica de la ficha, [(etiqueta, [(texto, enlace o vacío)])], solo las que tienen algo.

    Van primero las que más se miran (estudio, editora, modos, cooperativo y edad) y después perspectiva, temas,
    saga, motor y estado: cuando hay más de seis filas, la ficha enseña las cinco primeras y pliega el resto en «Más
    datos». El estudio, la editora (juntos si son los mismos), los modos, la perspectiva y los temas enlazan al
    Catálogo filtrado por ellos.

    Pantallas: Ficha del juego (ficha técnica).
    """
    details = game.details or {}
    catalog = reverse("games:catalog")

    def links(tags, param):
        """(nombre, enlace al Catálogo filtrado por la etiqueta) de cada etiqueta."""
        return [(tag.name, f"{catalog}?{urlencode({param: tag.slug})}") for tag in tags]

    developers, publishers = list(game.developers.all()), list(game.publishers.all())
    if developers and {tag.pk for tag in developers} == {tag.pk for tag in publishers}:
        companies = [("Estudio y editora", links(developers, "estudio"))]
    else:
        companies = [("Estudio", links(developers, "estudio")), ("Editora", links(publishers, "estudio"))]
    rows = companies + [
        ("Modos", links(game.modes.all(), "modo")),
        ("Cooperativo", [(part, "") for part in coop_parts(details.get("coop"))]),
        ("Edad", [(f"PEGI {details['pegi']}", "")] if details.get("pegi") else []),
        ("Perspectiva", links(game.perspectives.all(), "perspectiva")),
        ("Temas", links(game.themes.all(), "tema")),
        ("Saga", [(name, "") for name in details.get("sagas") or []]),
        ("Motor", [(name, "") for name in details.get("engines") or []]),
        ("Estado", [(game.status_label, "")] if game.status_label else []),
    ]
    return [(label, items) for label, items in rows if items]


def _local_games(ids):
    """Juegos del catálogo con esos ids de IGDB que tienen ficha propia (sin ediciones): {id de IGDB: Game}.

    Pantallas: Ficha del juego («Expansiones y DLC», «Ediciones y versiones», de qué juego es remake o DLC y «Juegos
    parecidos»).
    """
    ids = [game_id for game_id in ids if game_id]
    if not ids:
        return {}
    return {game.igdb_id: game for game in Game.objects.filter(igdb_id__in=ids, parent__isnull=True)}


def _item(data, local):
    """Un juego relacionado listo para la ficha: nombre, portada, año, etiqueta de su tipo y enlace (su ficha si está
    en el catálogo; si no, su búsqueda en el Catálogo, desde donde se puede traer de IGDB).

    Pantallas: Ficha del juego («Expansiones y DLC», «Ediciones y versiones» y de qué juego es remake o DLC en la
    cabecera).
    """
    game = local.get(data.get("id"))
    kind = data.get("type")
    label = GameType(kind).label if kind in GameType.values and kind != GameType.MAIN else ""
    if game is not None:
        url = game.get_absolute_url()
    else:
        url = f"{reverse('games:catalog')}?{urlencode({'q': data['name'], 'tipo': 'todo'})}"
    return {
        "name": data["name"],
        "cover_url": igdb_image_url(data.get("cover"), "t_cover_small") if data.get("cover") else "",
        "year": data.get("year"),
        "label": label,
        "url": url,
        "in_catalog": game is not None,
    }


def versions(game, edition=None):
    """Lo que enseña «Ediciones y versiones» de la ficha, que va plegada y cerrada enseña cuántos hay de cada grupo:
    {"editions": [...], "groups": [(título, [...])]} con los juegos ya preparados por _item; vacío si no hay nada.

    Las ediciones son las de IGDB (las que tienen este juego como version_parent), con su portada y el nombre de la
    edición («title»); edition es la edición desde la que se ha llegado (?edicion=), que va marcada («current»).
    Después, las otras versiones (remakes, remasters, ediciones ampliadas y ports) y los recopilatorios en los que
    está (VERSION_GROUPS). Las expansiones y los DLC van aparte, a la vista (addons).

    Pantallas: Ficha del juego («Ediciones y versiones»).
    """
    details = game.details or {}
    related = details.get("related") or {}
    ids = [item.get("id") for item in details.get("editions") or []]
    ids += [item.get("id") for _, keys in VERSION_GROUPS for key in keys for item in related.get(key) or []]
    local = _local_games(ids)
    editions = []
    for data in details.get("editions") or []:
        item = {**_item(data, {}), "title": data.get("title") or data["name"], "url": ""}
        item["current"] = edition is not None and edition.igdb_id == data.get("id")
        editions.append(item)
    groups = []
    for title, keys in VERSION_GROUPS:
        items = [_item(data, local) for key in keys for data in related.get(key) or []]
        if items:
            groups.append((title, items))
    if not editions and not groups:
        return {}
    return {"editions": editions, "groups": groups}


def short_name(base, name):
    """El nombre de una expansión o un DLC sin el del juego delante, que en una portada pequeña no deja ver lo que
    importa: «Elden Ring: Shadow of the Erdtree» queda en «Shadow of the Erdtree».

    Prueba con el nombre entero del juego (base) y con lo que va antes de sus dos puntos («The Witcher 3» de «The
    Witcher 3: Wild Hunt»), sin mirar mayúsculas, y solo lo quita si detrás viene un separador (NAME_SEPARATORS). Si
    no, deja el nombre como está: «Grand Theft Auto Online» no es «Grand Theft Auto V» con algo detrás.

    Pantallas: Ficha del juego («Expansiones y DLC»).
    """
    prefixes = [base.strip()]
    if ":" in base:
        prefixes.append(base.split(":", 1)[0].strip())
    for prefix in prefixes:
        if not prefix or name[:len(prefix)].casefold() != prefix.casefold():
            continue
        rest = name[len(prefix):].lstrip()
        if rest[:1] and rest[:1] in NAME_SEPARATORS:
            short = rest.lstrip(NAME_SEPARATORS).strip()
            if short:
                return short
    return name


def addon_ids(game):
    """Ids de IGDB de las expansiones y los DLC del juego (ADDON_KEYS de Game.details["related"]).

    Pantallas: Ficha del juego («Expansiones y DLC»).
    """
    related = (game.details or {}).get("related") or {}
    return [data["id"] for key in ADDON_KEYS for data in related.get(key) or [] if data.get("id")]


def addons(game):
    """Lo que enseña «Expansiones y DLC» de la ficha, a la vista y no plegado como «Ediciones y versiones»: primero
    las expansiones (también las independientes, como Grand Theft Auto Online) y después los DLC, cada grupo por año,
    preparados por _item y con su nombre corto («short», sin el del juego delante: short_name). Los que ya tienen
    ficha en el catálogo llevan además su precio más barato de ahora («price», Game.best_price, o None) y si sus
    precios se han buscado alguna vez («searched»). Lista vacía si no tiene.

    Pantallas: Ficha del juego («Expansiones y DLC»).
    """
    related = (game.details or {}).get("related") or {}
    found = [data for key in ADDON_KEYS for data in related.get(key) or []]
    if not found:
        return []
    local = {
        addon.igdb_id: addon
        for addon in Game.objects.filter(igdb_id__in=addon_ids(game), parent__isnull=True).prefetch_related("prices")
    }
    items = []
    for data in found:
        addon = local.get(data.get("id"))
        items.append({
            **_item(data, local),
            "short": short_name(game.name, data["name"]),
            "price": addon.best_price if addon else None,
            "searched": addon is not None and addon.prices_updated_at is not None,
        })
    return items


def import_addons(game, client=None):
    """Trae de IGDB las expansiones y los DLC del juego que aún no están en el catálogo y devuelve los juegos que ha
    guardado (igdb.upsert_games). Así cada uno tiene su propia ficha, con sus precios, sus imágenes y su ficha técnica
    como cualquier juego. Llegan con su tipo (DLC, expansión…), así que el Catálogo los esconde salvo que se pidan.

    Pantallas: Ficha del juego («Expansiones y DLC» y la ficha de cada uno), Catálogo (con ?tipo=, los DLC y las
    expansiones traídos).
    """
    ids = set(addon_ids(game))
    missing = sorted(ids - set(Game.objects.filter(igdb_id__in=ids).values_list("igdb_id", flat=True)))
    if not missing:
        return []
    return igdb.upsert_games((client or igdb.IGDBClient()).games_by_ids(missing))


def origin(game):
    """De qué juego es este remake, remaster, DLC, port... («Remake de Resident Evil 4»), para la cabecera:
    {"text", "name", "url"}, o None si no es de ninguno.

    Pantallas: Ficha del juego (cabecera: de qué juego es).
    """
    parent = (game.details or {}).get("parent")
    if not parent or not game.type_label:
        return None
    item = _item(parent, _local_games([parent.get("id")]))
    return {"text": f"{game.type_label} de", "name": item["name"], "url": item["url"]}


def similar(game, user=None, limit=SIMILAR_SHOWN):
    """Juegos parecidos (similar_games de IGDB) que están en el catálogo, sin ediciones ni DLC: primero los que el
    usuario tiene en Pendiente (marcados con in_backlog) y después los más populares; como mucho limit.

    Pantallas: Ficha del juego («Juegos parecidos», en la columna de los datos).
    """
    ids = (game.details or {}).get("similar") or []
    if not ids:
        return []
    games = list(
        Game.objects.filter(igdb_id__in=ids, parent__isnull=True).exclude(game_type__in=HIDDEN_TYPES).exclude(pk=game.pk)
    )
    backlog = set()
    if user is not None and user.is_authenticated and games:
        backlog = set(
            LibraryEntry.objects.filter(
                user=user, game__in=games, status=LibraryEntry.Status.BACKLOG,
            ).values_list("game_id", flat=True)
        )
    games.sort(key=lambda item: (item.pk not in backlog, -item.popularity, item.name.casefold()))
    for item in games:
        item.in_backlog = item.pk in backlog
    return games[:limit]


def remaining(game, entry):
    """Lo que le falta a quien lo está jugando según la duración normal: {"left": horas, redondeadas a la media hora}
    si le quedan, {"over": True} si ya lleva más de lo que suele durar, o None si no se sabe (sin horas, sin una
    duración con la que contar, o ya terminado o abandonado).

    Pantallas: Ficha del juego (Cuánto dura: «te faltan unas X h»).
    """
    if entry is None or entry.status in (LibraryEntry.Status.COMPLETED, LibraryEntry.Status.DROPPED):
        return None
    normal, played = game.normal_hours, entry.playtime_hours
    if not normal or not played:
        return None
    if normal - played >= 0.5:
        return {"left": half_hours(round((normal - played) * 60))}
    return {"over": True}
