from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from games.models import Game

from .models import LibraryEntry


def _respond(request, game, entry):
    # HTMX swaps the widget in place; without JS the form falls back to a full reload.
    if request.headers.get("HX-Request"):
        return render(
            request,
            "library/_widget.html",
            {"game": game, "entry": entry, "statuses": LibraryEntry.Status.choices},
        )
    return redirect(game)


@require_POST
@login_required
def set_status(request, game_id):
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
    game = get_object_or_404(Game, pk=game_id)
    LibraryEntry.objects.filter(user=request.user, game=game).delete()
    return _respond(request, game, None)
