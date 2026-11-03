"""Las listas de un juego: la pantalla Listas del juego (juegos/<juego>/listas/) y lo que enseña de ellas la Ficha del
juego (el desplegable «Añadir a una lista» y «En N listas»).
"""

from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef
from django.shortcuts import get_object_or_404, render

from games.models import Game

from ..models import GameList, ListItem
from .tarjetas import mark_progress

# Listas que salen en la ficha de un juego («En N listas»).
GAME_PAGE_LISTS = 4
# Listas por página en la de todas las listas en las que está un juego.
LISTS_PER_PAGE = 24


def _lists_with(game, viewer):
    """Listas que viewer puede ver en las que está game, de la más reciente a la más antigua, con sus tarjetas cargadas.

    Pantallas: Ficha del juego (listas en las que sale), Listas del juego.
    """
    return (
        GameList.objects.visible_to(viewer)
        .filter(pk__in=ListItem.objects.filter(game=game).values("game_list_id"))
        .with_preview()
    )


def picker_context(user, game):
    """Variables del desplegable «Añadir a una lista» de la ficha de game (lists/_picker.html).

    Las listas que user puede editar (las suyas y en las que colabora), de la más reciente a la más antigua, cada una
    con su dueño, cuántos juegos tiene (game_count) y si game ya está en ella (has_game), y en cuántas está (in_lists).

    Pantallas: Ficha del juego (desplegable «Añadir a una lista»).
    """
    lists = list(
        GameList.objects.editable_by(user).select_related("user__profile").annotate(
            game_count=Count("items"),
            has_game=Exists(ListItem.objects.filter(game_list=OuterRef("pk"), game=game)),
        ).order_by("-updated_at", "-id")
    )
    return {"my_lists": lists, "in_lists": sum(1 for game_list in lists if game_list.has_game)}


def game_page_context(request, game):
    """Variables de las listas en la ficha de game: con sesión, las del desplegable «Añadir a una lista»
    (picker_context); y para todos, «En N listas»: las GAME_PAGE_LISTS más recientes que puede ver quien mira
    (game_lists) y cuántas son (game_lists_count).

    Pantallas: Ficha del juego (desplegable «Añadir a una lista» y «En N listas»).
    """
    lists = _lists_with(game, request.user)
    context = {"game_lists": list(lists[:GAME_PAGE_LISTS]), "game_lists_count": lists.count()}
    if request.user.is_authenticated:
        context.update(picker_context(request.user, game))
    return context


def game_lists(request, slug):
    """Todas las listas en las que está un juego que puede ver quien mira, de la más reciente a la más antigua, de
    LISTS_PER_PAGE en LISTS_PER_PAGE (?page=).

    Pantallas: Listas del juego.
    """
    game = get_object_or_404(Game, slug=slug)
    page = Paginator(_lists_with(game, request.user), LISTS_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "lists/game_lists.html", {
        "game": game, "page": page, "lists": mark_progress(list(page.object_list), request.user),
    })
