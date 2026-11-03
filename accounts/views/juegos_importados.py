"""Vistas de Juegos importados (cuenta/juegos-importados/): los títulos importados de Steam, PlayStation o Xbox que no se
encontraron en el catálogo, para asignarles su juego a mano u ocultarlos, y los que han llevado a un juego, para
corregirlo.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from games import igdb
from games.models import Game

from .. import matching
from ..models import ImportedTitle
from .comun import _back

logger = logging.getLogger(__name__)
# Resultados como máximo al buscar el juego de un título importado; si en el catálogo salen menos de
# IMPORTED_SEARCH_IGDB_BELOW, también se busca en IGDB.
IMPORTED_SEARCH_RESULTS = 12
IMPORTED_SEARCH_IGDB_BELOW = 5


def _search_games(query):
    """Juegos del catálogo cuyo nombre contiene query, de más a menos popular, completando con IGDB si salen pocos.

    Pantallas: Juegos importados (buscador para elegir el juego de un título).
    """
    games = list(Game.objects.filter(name__icontains=query).prefetch_related("platforms")[:IMPORTED_SEARCH_RESULTS])
    if len(games) < IMPORTED_SEARCH_IGDB_BELOW and igdb.is_configured():
        try:
            found = igdb.upsert_games(igdb.IGDBClient().search(query, limit=IMPORTED_SEARCH_RESULTS))
        except igdb.IGDBError:
            logger.exception("Falló la búsqueda en IGDB de «%s»", query)
            found = []
        games += [game for game in found if game not in games]
    return games[:IMPORTED_SEARCH_RESULTS]


@login_required
def imported_titles(request):
    """Juegos importados para revisar a mano.

    Sin parámetros, los títulos de Steam, PlayStation o Xbox del usuario que no se encontraron en el
    catálogo (con ?ocultos=1, los que ocultó). Con ?juego=<id>, los que han llevado a ese juego de su
    biblioteca, para corregirlo si no es el que jugó. Con ?titulo=<id>&q= busca candidatos para ese
    título (por defecto, por su nombre).

    Pantallas: Juegos importados.
    """
    rows = request.user.imported_titles.select_related("title__game")
    game, hidden = None, request.GET.get("ocultos") == "1"
    if request.GET.get("juego", "").isdigit():
        game = get_object_or_404(Game, pk=request.GET["juego"])
        rows = rows.filter(title__game=game)
        back = f"{request.path}?juego={game.pk}"
    else:
        rows = rows.filter(title__game__isnull=True, hidden=hidden)
        back = f"{request.path}?ocultos=1" if hidden else request.path
    rows = list(rows)

    selected = None
    if request.GET.get("titulo", "").isdigit():
        selected = next((row for row in rows if row.pk == int(request.GET["titulo"])), None)
    search = (request.GET.get("q") or "").strip() or (selected.title.name if selected else "")
    return render(
        request,
        "accounts/imported_titles.html",
        {
            "rows": rows,
            "game": game,
            "hidden": hidden,
            "back": back,
            "selected": selected,
            # «search» y no «query»: query es la caja de búsqueda del catálogo en la cabecera.
            "search": search,
            "results": _search_games(search) if selected and search else [],
            "hidden_count": request.user.imported_titles.filter(title__game__isnull=True, hidden=True).count(),
        },
    )


@require_POST
@login_required
def imported_title_assign(request, pk):
    """Asigna a un título importado del usuario el juego elegido (o ninguno si «game» llega vacío).

    La corrección sirve para todos los usuarios con ese título y sus horas pasan al juego nuevo en su
    biblioteca. Responde 404 si el título no es suyo o el juego no existe.

    Pantallas: Juegos importados, Perfil (biblioteca y horas), Ficha del juego (tus horas).
    """
    imported = get_object_or_404(ImportedTitle.objects.select_related("title"), pk=pk, user=request.user)
    game_id = request.POST.get("game", "")
    game = get_object_or_404(Game, pk=game_id) if game_id.isdigit() else None
    try:
        result = matching.reassign(imported, game, request.user)
    except PermissionError as exc:
        messages.error(request, str(exc))
        return _back(request, reverse("imported_titles"))
    name = imported.title.name
    if game is None:
        messages.success(request, f"«{name}» queda sin emparejar.")
    else:
        text = f"«{name}» es ahora {game.name}, también para quien lo importe después"
        if result["removed"]:
            text += "; el juego anterior se ha quitado de tu biblioteca"
        messages.success(request, text + ".")
    return _back(request, reverse("imported_titles"))


@require_POST
@login_required
def imported_title_hide(request, pk):
    """Oculta (hidden=1) un título sin emparejar del usuario —herramientas, demos, apps— o lo vuelve a mostrar.

    Pantallas: Juegos importados (ocultar un título o volver a mostrarlo).
    """
    imported = get_object_or_404(ImportedTitle.objects.select_related("title"), pk=pk, user=request.user)
    imported.hidden = request.POST.get("hidden") == "1"
    imported.save(update_fields=["hidden"])
    verb = "ocultado" if imported.hidden else "vuelto a mostrar"
    messages.success(request, f"Has {verb} «{imported.title.name}».")
    return _back(request, reverse("imported_titles"))
