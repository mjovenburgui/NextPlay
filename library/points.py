"""«Lo mejor» y «Lo peor» de las reseñas de un juego, sumados: las sugerencias del editor y lo que más se repite.

El editor sugiere los puntos que ya han escrito otros en el mismo juego. Si la gente los reutiliza, salen
las mismas frases y la ficha puede enseñar «Lo que más destacan» con cuántas reseñas lo dicen. Dos puntos
cuentan como el mismo si solo cambian las mayúsculas, los espacios o la puntuación del principio y del final.
"""

import re
from collections import Counter

from accounts.models import Profile

from . import markup
from .models import Review

# Puntuación y espacios que no cuentan al comparar puntos: «¡La banda sonora!» = «la banda sonora».
EDGES = re.compile(r"^[\W_]+|[\W_]+$")
SUGGESTIONS = 12
CONSENSUS_SHOWN = 5
# Reseñas que tienen que decir lo mismo para que salga en «Lo que más destacan».
CONSENSUS_MINIMUM = 2


def key(point):
    """Forma de comparar un punto: sin formato, en minúsculas, con los espacios simplificados y sin puntuación en los extremos."""
    return EDGES.sub("", " ".join(markup.plain(point).casefold().split()))


def _tally(rows):
    """Cuenta los puntos de rows ([(pros, cons)]): {"pros": [(punto, veces)], "cons": [...]}, de más a menos repetido.

    Cada reseña cuenta una vez por punto; de cada punto se enseña cómo lo escribió el primero.
    """
    counts = {"pros": Counter(), "cons": Counter()}
    shown = {"pros": {}, "cons": {}}
    for pros, cons in rows:
        for name, points in (("pros", pros), ("cons", cons)):
            for point_key, point in {key(point): point for point in points or [] if key(point)}.items():
                counts[name][point_key] += 1
                shown[name].setdefault(point_key, point)
    return {name: [(shown[name][k], n) for k, n in counts[name].most_common()] for name in counts}


def _visible(game, viewer):
    """Reseñas publicadas del juego que viewer puede leer (las de perfiles públicos y las suyas), de la más
    antigua a la más nueva: así cada punto se enseña como lo escribió el primero."""
    return Review.objects.filter(game=game).published().filter(Profile.visible_filter(viewer)).order_by("published_at", "id")


def suggestions(game, viewer):
    """Puntos que más han escrito otros en las reseñas del juego, para sugerirlos en el editor: {"pros": [...], "cons": [...]}."""
    rows = _visible(game, viewer)
    if viewer.is_authenticated:
        rows = rows.exclude(user=viewer)
    counted = _tally(rows.values_list("pros", "cons"))
    return {name: [point for point, _ in points[:SUGGESTIONS]] for name, points in counted.items()}


def consensus(game, viewer):
    """Lo que más destacan y lo que menos gusta del juego: {"pros": [(punto, reseñas)], "cons": [...]}.

    Solo los puntos que dicen al menos CONSENSUS_MINIMUM reseñas, como mucho CONSENSUS_SHOWN de cada lista.
    """
    counted = _tally(_visible(game, viewer).values_list("pros", "cons"))
    return {
        name: [(point, n) for point, n in points if n >= CONSENSUS_MINIMUM][:CONSENSUS_SHOWN]
        for name, points in counted.items()
    }
