"""La tier list de la comunidad: la que sale de juntar una tier list abierta (GameList.is_open) con las versiones que
hace la gente con «Haz tu versión», con cada juego en el nivel del medio de sus votos. Se ve en la Página de la lista de
las tier lists abiertas.
"""

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q

from .models import GameList, ListItem

# Gente que tiene que haberla hecho (contando a su autor, si ha colocado algo) para enseñar la de la comunidad: con
# menos, sería la opinión de una o dos personas.
MIN_VOTERS = 3


def counted_lists(game_list):
    """Las tier lists que cuentan en la de la comunidad de game_list: ella misma y sus versiones, solo las que puede
    ver cualquiera (públicas y de perfiles públicos), para que no se pueda adivinar lo que ha votado una privada.

    Pantallas: Página de la lista (la tier list de la comunidad).
    """
    return GameList.objects.visible_to(AnonymousUser()).filter(Q(pk=game_list.pk) | Q(parent=game_list))


def middle_tier(positions):
    """El nivel que le da la comunidad a un juego, con positions el puesto en los niveles (0 es el mejor) de cada voto:
    el del medio. Con un número par de votos y los dos del medio distintos, el que queda entre ellos o, si no queda
    ninguno, el de los dos más cercano a la media de todos (el mejor, si están igual de cerca).

    Pantallas: Página de la lista (el nivel de cada juego en la tier list de la comunidad).
    """
    votes = sorted(positions)
    low, high = votes[(len(votes) - 1) // 2], votes[len(votes) // 2]
    if (low + high) % 2 == 0:
        return (low + high) // 2
    mean = sum(votes) / len(votes)
    return low if mean - low <= high - mean else high


def community_board(game_list, items=None):
    """La tier list de la comunidad de game_list, una tier list abierta, con sus juegos items (sus ListItem con su
    juego, en orden; si no se dan, se cargan).

    Devuelve voters (cuánta gente la ha hecho: las de counted_lists con algún juego suyo colocado en uno de sus
    niveles), missing (cuánta falta para MIN_VOTERS; 0 si ya se enseña) y rows, como GameList.tier_rows: [(nivel,
    juegos)] con los niveles de game_list y, al final, los juegos que nadie ha colocado (nivel None), si hay alguno.
    Cada juego lleva votes (cuánta gente lo ha colocado) y spread ([(nivel, cuántos)] de los niveles en los que lo han
    puesto, de mejor a peor, para su barra de colores). En cada nivel van de los de mejor media a los de peor y, si
    empatan, de los más votados a los que menos; si no, en el orden de game_list.

    Solo cuentan los votos a sus juegos y en sus niveles (por su id): los de un nivel que ella no tiene, no.

    Pantallas: Página de la lista (la tier list de la comunidad).
    """
    if items is None:
        items = list(game_list.items.select_related("game"))
    levels = game_list.tier_list
    rank = {level["id"]: n for n, level in enumerate(levels)}
    votes, voters = {}, set()
    placed = ListItem.objects.filter(
        game_list__in=counted_lists(game_list), game__in=[item.game_id for item in items], tier__in=list(rank),
    ).values_list("game_list_id", "game_id", "tier")
    for list_id, game_id, tier in placed:
        votes.setdefault(game_id, []).append(rank[tier])
        voters.add(list_id)
    rows = [(level, []) for level in levels]
    loose = []
    for item in items:
        positions = votes.get(item.game_id, [])
        item.votes = len(positions)
        item.spread = [(levels[n], positions.count(n)) for n in sorted(set(positions))]
        if positions:
            item.mean = sum(positions) / len(positions)
            rows[middle_tier(positions)][1].append(item)
        else:
            loose.append(item)
    for _, row in rows:
        row.sort(key=lambda item: (item.mean, -item.votes))
    return {
        "voters": len(voters),
        "missing": max(0, MIN_VOTERS - len(voters)),
        "rows": rows + ([(None, loose)] if loose else []),
    }
