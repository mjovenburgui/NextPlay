"""Lo que comparten las vistas que cambian una lista: la del usuario, o una en la que colabora, o un 404."""

from django.http import Http404
from django.shortcuts import get_object_or_404

from ..models import GameList


def _own_list(request, list_id):
    """Lista list_id del usuario; las de otros dan 404.

    Pantallas: Editar lista (404 si no es tuya).
    """
    return get_object_or_404(GameList, pk=list_id, user=request.user)


def _editable_list(request, list_id):
    """Lista list_id si el usuario puede editar sus juegos (su dueño o quien colabora en ella); si no, 404.

    Pantallas: Editar lista (los juegos: 404 si no puedes editarla), Ficha del juego (desplegable «Añadir a una
    lista»), Página de la lista (tablero de las tier lists).
    """
    game_list = get_object_or_404(GameList.objects.select_related("user__profile"), pk=list_id)
    if not game_list.can_edit_games(request.user):
        raise Http404
    return game_list
