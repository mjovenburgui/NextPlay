"""Qué jugar después: un perfil de gustos sacado de cómo juega cada uno (sus horas respecto a lo que dura cada juego, sus
notas, lo que termina y lo que deja) y, con él, juegos de su pendiente y del catálogo que le pueden gustar, cada uno con
su porqué. Casi nadie pone nota a todo, así que lo que más pesa son las horas.

Pantallas: Qué jugar después, Inicio (el enlace de tu menú), Perfil (pestaña Pendiente: el enlace).
"""

from collections import Counter

from games.models import HIDDEN_TYPES, Game

from .models import LibraryEntry, WishlistItem

Status = LibraryEntry.Status
# Cuánto pesa cada parte de un juego al comparar gustos: el género y los temas dicen más que la perspectiva o los modos.
FEATURE_WEIGHTS = {"genres": 1.0, "themes": 0.8, "perspectives": 0.4, "modes": 0.3}
FEATURE_PREFETCH = [f"game__{kind}" for kind in FEATURE_WEIGHTS]
# Lo que suma salir entre los «juegos parecidos» (similar_games de IGDB) de uno de los juegos que más pesan en tus gustos.
SIMILAR_BONUS = 1.5
# Los juegos tuyos que más pesan, de los que se buscan parecidos y salen en los porqués.
SEEDS = 12
# Horas que cuentan como un juego bien jugado si IGDB no dice cuánto dura, y el tope de veces su duración que cuenta.
DEFAULT_HOURS = 20
PLAYED_CAP = 3
# Juegos de cada tanda de la página y juegos del catálogo que se miran como candidatos (los más populares).
PAGE_SIZE = 6
DISCOVER_POOL = 1500
# Como mucho estos «parecido a…» del mismo juego tuyo seguidos, para que una tanda no salga entera de uno solo.
PER_SOURCE = 2


def weight(entry):
    """Cuánto dice entry de tus gustos: las veces que lo has jugado respecto a lo que dura (40 h en uno de 10 h dicen más
    que 40 h en uno de 100 h; sin duración, respecto a DEFAULT_HOURS), con un tope de PLAYED_CAP; más la nota si la tiene
    (de −1,2 con media estrella a +1,5 con cinco); medio punto más si lo terminó y uno menos si lo dejó.

    Pantallas: Qué jugar después (por qué te recomienda cada juego).
    """
    hours = (entry.playtime_minutes or 0) / 60
    score = min(hours / (entry.game.normal_hours or DEFAULT_HOURS), PLAYED_CAP)
    if entry.rating:
        score += (entry.rating - 5) / 5 * 1.5
    if entry.status == Status.COMPLETED:
        score += 0.5
    elif entry.status == Status.DROPPED:
        score -= 1
    return score


def _features(game):
    """Las características de game como {(tipo, id): nombre}: sus géneros, temas, perspectivas y modos, ya cargados.

    Pantallas: Qué jugar después.
    """
    return {(kind, item.pk): item.name for kind in FEATURE_WEIGHTS for item in getattr(game, kind).all()}


def tastes(user):
    """El perfil de gustos de user: {"entries" (su biblioteca, cada entrada con su peso en taste), "points" ({(tipo, id):
    puntos} de cada característica, sumando el peso de los juegos que la tienen), "scale" (el mayor, para comparar) y
    "seeds" (sus SEEDS juegos con más peso, si pesan a favor)}.

    Pantallas: Qué jugar después.
    """
    entries = list(LibraryEntry.objects.filter(user=user).select_related("game").prefetch_related(*FEATURE_PREFETCH))
    points = Counter()
    for entry in entries:
        entry.taste = weight(entry)
        for kind, pk in _features(entry.game):
            points[(kind, pk)] += entry.taste * FEATURE_WEIGHTS[kind]
    seeds = sorted((entry for entry in entries if entry.taste > 0), key=lambda entry: (-entry.taste, entry.pk))[:SEEDS]
    return {"entries": entries, "points": points, "scale": max(points.values(), default=0) or 1, "seeds": seeds}


def _similar_to(seeds):
    """{id de un juego del catálogo: el juego tuyo (LibraryEntry) entre cuyos parecidos de IGDB sale}, con los tuyos de
    más peso primero.

    Pantallas: Qué jugar después (los «parecido a…»).
    """
    wanted = {}
    for entry in seeds:
        for igdb_id in (entry.game.details or {}).get("similar") or []:
            wanted.setdefault(igdb_id, entry)
    found = Game.objects.filter(igdb_id__in=list(wanted)).values_list("igdb_id", "pk")
    return {pk: wanted[igdb_id] for igdb_id, pk in found}


def _played_text(entry):
    """Lo que dice el porqué de un juego tuyo: sus horas, tu nota o que lo terminaste.

    Pantallas: Qué jugar después.
    """
    if entry.playtime_minutes and entry.playtime_minutes >= 60:
        return f"al que has jugado {round(entry.playtime_minutes / 60)} h"
    if entry.rating:
        return f"al que pusiste {entry.stars:g}".replace(".", ",") + " ★"
    if entry.status == Status.COMPLETED:
        return "que terminaste"
    return "que tienes en tu biblioteca"


def _why(features, source, seed_features):
    """Por qué se recomienda un juego (de características features): si sale entre los parecidos de uno tuyo (source),
    ese juego y lo que has jugado; si no, el tuyo de más peso con el que comparte más géneros y temas, y cuáles.

    Pantallas: Qué jugar después.
    """
    if source is not None:
        return f"Parecido a {source.game.name}, {_played_text(source)}."
    keys = set(features)
    best = max(seed_features, key=lambda pair: len(pair[1] & keys), default=None)
    if best is None or not best[1] & keys:
        return "Encaja con lo que más juegas."
    shared = [features[key] for key in features if key in best[1] and key[0] in ("genres", "themes")][:2]
    if shared:
        # En minúscula solo la primera letra: «rol (RPG)», no «rol (rpg)».
        return f"Como {best[0].game.name}: {' y '.join(name[:1].lower() + name[1:] for name in shared)}."
    return f"Como {best[0].game.name}."


def _rank(games, taste, similar_to):
    """games ordenados de lo que más a lo que menos te puede gustar, cada uno con su encaje (fit: los puntos de sus
    características en tus gustos, SIMILAR_BONUS si sale entre los parecidos de uno tuyo y un poco por su nota) y su
    porqué (why).

    Pantallas: Qué jugar después.
    """
    seed_features = [(entry, set(_features(entry.game))) for entry in taste["seeds"]]
    for game in games:
        features = _features(game)
        source = similar_to.get(game.pk)
        game.fit = sum(taste["points"].get(key, 0) for key in features) / taste["scale"]
        game.source = source
        if source is not None:
            game.fit += SIMILAR_BONUS
        score = game.critic_score or game.igdb_score
        if score:
            game.fit += (score - 70) / 100
        game.why = _why(features, source, seed_features)
    return sorted(games, key=lambda game: (-game.fit, -game.popularity, game.name.casefold()))


def _diversify(games, per_source=PER_SOURCE):
    """games en el mismo orden, salvo que de cada juego tuyo del que son parecidos (game.source) solo van per_source
    delante: los demás pasan detrás, para que una tanda no salga entera de un solo juego.

    Pantallas: Qué jugar después.
    """
    counts, first, rest = Counter(), [], []
    for game in games:
        source = getattr(game, "source", None)
        if source is not None and counts[source.pk] >= per_source:
            rest.append(game)
            continue
        if source is not None:
            counts[source.pk] += 1
        first.append(game)
    return first + rest


def _page(items, page):
    """La tanda page (desde 1) de PAGE_SIZE de items y si hay otra después.

    Pantallas: Qué jugar después («Ver otros»).
    """
    start = (page - 1) * PAGE_SIZE
    return items[start:start + PAGE_SIZE], len(items) > start + PAGE_SIZE


def suggestions(user, backlog_page=1, discover_page=1):
    """Lo que enseña Qué jugar después a user: de su pendiente («backlog») y del catálogo («discover», sin lo que ya
    tiene ni lo que quiere, sin ediciones ni DLC), cada uno en su tanda (backlog_page y discover_page, con «…_more» si
    hay otra) y ordenado por lo que le puede gustar (_rank, con _diversify), sin lo que marcó «No me interesa»
    (Profile.not_interested); y sus juegos que más pesan («seeds»), para decir de dónde sale.

    Pantallas: Qué jugar después.
    """
    taste = tastes(user)
    skip = set(user.profile.not_interested)
    similar_to = _similar_to(taste["seeds"])
    pending = [entry.game for entry in taste["entries"] if entry.status == Status.BACKLOG and entry.game_id not in skip]
    taken = {entry.game_id for entry in taste["entries"]} | skip
    taken |= set(WishlistItem.objects.filter(user=user).values_list("game_id", flat=True))
    catalog = Game.objects.filter(parent__isnull=True).exclude(game_type__in=HIDDEN_TYPES)
    pool = set(similar_to) | set(catalog.filter(popularity__gt=0).order_by("-popularity").values_list("pk", flat=True)[:DISCOVER_POOL])
    discover = list(catalog.filter(pk__in=pool - taken).prefetch_related(*FEATURE_WEIGHTS))
    backlog, backlog_more = _page(_diversify(_rank(pending, taste, similar_to)), backlog_page)
    found, discover_more = _page(_diversify(_rank(discover, taste, similar_to)), discover_page)
    return {
        "backlog": backlog,
        "backlog_more": backlog_more,
        "backlog_page": backlog_page,
        "discover": found,
        "discover_more": discover_more,
        "discover_page": discover_page,
        "seeds": taste["seeds"][:3],
    }


def not_interested(user, game):
    """Apunta que a user no le interesa game, para que Qué jugar después no se lo vuelva a proponer.

    Pantallas: Qué jugar después (botón «No me interesa»).
    """
    profile = user.profile
    if game.pk not in profile.not_interested:
        profile.not_interested = [*profile.not_interested, game.pk]
        profile.save(update_fields=["not_interested"])
