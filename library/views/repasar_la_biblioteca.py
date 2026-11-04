"""Vistas de Repasar la biblioteca (biblioteca/repasar/): los juegos que las importaciones no saben si terminaste, de
uno en uno, para contestarlos con un botón (library.backlog).
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import backlog
from ..models import LibraryEntry


def _card_context(user):
    """Variables de la tarjeta del repaso (library/_tidy_card.html) para user: el siguiente juego (entry, o None si no
    queda ninguno), su pista (hint), cuántos quedan (remaining) y lo que lleva repasado hoy (summary).

    Pantallas: Repasar la biblioteca.
    """
    left = backlog.queue(user)
    entry = left[0] if left else None
    return {
        "entry": entry,
        "hint": backlog.hint(entry) if entry else None,
        "remaining": len(left),
        "summary": backlog.today_summary(user),
    }


@login_required
def tidy(request):
    """Repasar la biblioteca: el primer juego por repasar con sus pistas y sus botones, o el resumen si no queda ninguno.

    Pantallas: Repasar la biblioteca.
    """
    return render(request, "library/tidy.html", _card_context(request.user))


@require_POST
@login_required
def tidy_answer(request, entry_id):
    """Contesta un juego del repaso (respuesta=, una clave de library.backlog.ANSWERS) y pasa al siguiente: con HTMX
    devuelve la tarjeta siguiente; sin HTMX, vuelve a la página. Las entradas de otros dan 404 y una respuesta que no
    existe, 400.

    Pantallas: Repasar la biblioteca (botones de cada tarjeta), Perfil (biblioteca), Inicio (feed: «ordenó su
    biblioteca»), Retos, Página del reto (progreso).
    """
    entry = get_object_or_404(LibraryEntry.objects.select_related("game", "user"), pk=entry_id, user=request.user)
    choice = request.POST.get("respuesta", "")
    if choice not in backlog.ANSWERS:
        return HttpResponseBadRequest("Respuesta no válida.")
    backlog.answer(entry, choice)
    if request.headers.get("HX-Request"):
        return render(request, "library/_tidy_card.html", _card_context(request.user))
    return redirect("library:tidy")
