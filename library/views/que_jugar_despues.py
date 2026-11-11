"""Vistas de Qué jugar después (biblioteca/que-jugar/): juegos de tu pendiente y del catálogo que te pueden gustar según
cómo juegas (library.taste), con su porqué y un botón «No me interesa».
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from games.models import Game

from .. import taste


def _page(value):
    """El número de tanda de ?pendiente= o ?descubrir=, desde 1; 1 si no es un número.

    Pantallas: Qué jugar después («Ver otros»).
    """
    return int(value) if value.isdigit() and int(value) > 0 else 1


@login_required
def next_games(request):
    """Qué jugar después: de tu pendiente y del catálogo, lo que te puede gustar con su porqué, cada bloque en tandas
    (?pendiente= y ?descubrir=, para «Ver otros»).

    Pantallas: Qué jugar después.
    """
    found = taste.suggestions(request.user, _page(request.GET.get("pendiente", "")), _page(request.GET.get("descubrir", "")))
    return render(request, "library/next.html", found)


@require_POST
@login_required
def not_interested(request, game_id):
    """«No me interesa»: Qué jugar después no vuelve a proponer ese juego (library.taste.not_interested). Con HTMX quita
    su tarjeta (respuesta vacía); sin HTMX, vuelve a la página.

    Pantallas: Qué jugar después (botón «No me interesa»).
    """
    taste.not_interested(request.user, get_object_or_404(Game, pk=game_id))
    if request.headers.get("HX-Request"):
        return HttpResponse("")
    return redirect("library:next")
