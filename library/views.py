from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from games.models import Game

from .models import LibraryEntry


def _respond(request, game, entry):
    """Respuesta común de las acciones de biblioteca.

    Si la petición viene de HTMX devuelve el widget de biblioteca ya actualizado junto con el
    recuadro de valoración de la comunidad (que HTMX cambia en su sitio); si no, redirige a la
    ficha del juego.
    """
    # HTMX swaps the widget in place; without JS the form falls back to a full reload.
    if request.headers.get("HX-Request"):
        # The community rating box sits elsewhere on the page; it is swapped out of band.
        return render(
            request,
            "library/_widget.html",
            {
                "game": game,
                "entry": entry,
                "statuses": LibraryEntry.Status.choices,
                "community": game.community_rating(),
                "community_oob": True,
            },
        )
    return redirect(game)


@require_POST
@login_required
def set_status(request, game_id):
    """Añade el juego a la biblioteca del usuario con el estado enviado, o cambia el que tenía.

    Responde 400 si el estado no es uno de los permitidos.
    """
    game = get_object_or_404(Game, pk=game_id)
    status = request.POST.get("status")
    if status not in LibraryEntry.Status.values:
        return HttpResponseBadRequest("Estado no válido.")
    entry, _ = LibraryEntry.objects.update_or_create(
        user=request.user, game=game, defaults={"status": status}
    )
    return _respond(request, game, entry)


@require_POST
@login_required
def remove(request, game_id):
    """Quita el juego de la biblioteca del usuario (no da error si no estaba)."""
    game = get_object_or_404(Game, pk=game_id)
    LibraryEntry.objects.filter(user=request.user, game=game).delete()
    return _respond(request, game, None)


@require_POST
@login_required
def rate(request, game_id):
    """Guarda la valoración del usuario (1 a 5 estrellas) para un juego de su biblioteca.

    Con rating=0 borra la valoración. Responde 404 si el juego no está en su biblioteca y 400 si
    la valoración no es un número del 0 al 5.
    """
    game = get_object_or_404(Game, pk=game_id)
    entry = get_object_or_404(LibraryEntry, user=request.user, game=game)
    try:
        rating = int(request.POST.get("rating", ""))
    except ValueError:
        rating = -1
    if not 0 <= rating <= LibraryEntry.MAX_RATING:
        return HttpResponseBadRequest("Valoración no válida.")
    entry.rating = rating or None
    entry.save(update_fields=["rating", "updated_at"])
    return _respond(request, game, entry)
