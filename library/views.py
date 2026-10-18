from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.models import Game

from .forms import ReviewForm
from .models import LibraryEntry, WishlistItem


REVIEWS_SHOWN = 30


def review_context(game, entry, viewer):
    """Variables de la sección de reseñas de un juego: las reseñas que viewer puede leer, cuántas son
    y el formulario de su propia reseña (None si el juego no está en su biblioteca)."""
    reviews = LibraryEntry.objects.filter(game=game).reviews_visible_to(viewer)
    return {
        "reviews": reviews[:REVIEWS_SHOWN],
        "review_count": reviews.count(),
        "review_form": ReviewForm(instance=entry) if entry else None,
    }


def _respond(request, game, entry):
    """Respuesta común de las acciones de biblioteca.

    Si la petición viene de HTMX devuelve el widget de biblioteca ya actualizado junto con el
    recuadro de valoración de la comunidad y la sección de reseñas (que HTMX cambia en su sitio);
    si no, redirige a la ficha del juego.
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
                **review_context(game, entry, request.user),
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


@require_POST
@login_required
def review(request, game_id):
    """Guarda, cambia o borra (si llega vacía) la reseña del usuario sobre un juego de su biblioteca.

    Responde 404 si el juego no está en su biblioteca. Si la reseña no es válida (demasiado larga)
    avisa del error. Siempre vuelve a la sección de reseñas de la ficha del juego.
    """
    game = get_object_or_404(Game, pk=game_id)
    entry = get_object_or_404(LibraryEntry, user=request.user, game=game)
    old_review = entry.review
    form = ReviewForm(request.POST, instance=entry)
    if form.is_valid():
        entry = form.save(commit=False)
        if not entry.review:
            entry.review_has_spoilers = False
            entry.reviewed_at = None
            messages.success(request, "Reseña borrada." if old_review else "La reseña estaba vacía.")
        else:
            if entry.review != old_review or entry.reviewed_at is None:
                entry.reviewed_at = timezone.now()
            messages.success(request, "Reseña publicada." if not old_review else "Reseña actualizada.")
        entry.save()
    else:
        messages.error(request, " ".join(form.errors.get("review", ["La reseña no es válida."])))
    return redirect(f"{game.get_absolute_url()}#resenas")


def _wish_response(request, game, wish, error=""):
    """Respuesta de las acciones de la lista de deseos: el botón «Lo quiero» actualizado si es HTMX; si no,
    vuelve a la página de la que vino (la ficha por defecto) avisando del error si lo hay."""
    if request.headers.get("HX-Request"):
        return render(request, "library/_wish.html", {"game": game, "wish": wish, "wish_error": error})
    if error:
        messages.error(request, error)
    target = request.POST.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(game)


@require_POST
@login_required
def wish_toggle(request, game_id):
    """Añade el juego a la lista de deseos del usuario o lo quita si ya estaba."""
    game = get_object_or_404(Game, pk=game_id)
    wish, created = WishlistItem.objects.get_or_create(user=request.user, game=game)
    if not created:
        wish.delete()
        wish = None
    return _wish_response(request, game, wish)


@require_POST
@login_required
def wish_target(request, game_id):
    """Guarda el precio por debajo del cual avisar (vacío = solo avisar del mínimo histórico).

    Al cambiarlo se olvida el último aviso, para volver a comparar con el precio nuevo. Responde 404
    si el juego no está en su lista.
    """
    game = get_object_or_404(Game, pk=game_id)
    wish = get_object_or_404(WishlistItem, user=request.user, game=game)
    raw = request.POST.get("target_price", "").strip().replace(",", ".")
    try:
        target = Decimal(raw).quantize(Decimal("0.01")) if raw else None
    except InvalidOperation:
        target = Decimal(-1)
    if target is not None and not Decimal(0) <= target < Decimal(100000):
        return _wish_response(request, game, wish, "Escribe un precio en euros, por ejemplo 19,99.")
    wish.target_price = target
    wish.alerted_price = None
    wish.save(update_fields=["target_price", "alerted_price"])
    return _wish_response(request, game, wish)


# Valor de ?orden= en la lista de deseos → (etiqueta, clave de orden). Los que no tienen precio van al final.
WISHLIST_SORTS = {
    "descuento": ("Mayor descuento", lambda row: -(row["best"].cut if row["best"] else -1)),
    "precio": ("Más baratos", lambda row: (row["best"] is None, row["best"].price if row["best"] else 0)),
    "nombre": ("Nombre", lambda row: row["wish"].game.name.casefold()),
}


@login_required
def wishlist(request):
    """Lista de deseos del usuario con el mejor precio de hoy de cada juego (en sus tiendas, si las ha
    elegido), su mínimo histórico y si está por debajo de su límite. Se ordena con ?orden= (recientes
    por defecto, descuento, precio o nombre)."""
    wishes = WishlistItem.objects.filter(user=request.user).select_related("game").prefetch_related("game__prices")
    owned = set(
        LibraryEntry.objects.filter(user=request.user, game__wishlisted_by__user=request.user)
        .values_list("game_id", flat=True)
    )
    shops = set(request.user.profile.shops) or None
    rows = []
    for wish in wishes:
        best = wish.game.cheapest(shops)
        low = wish.game.historical_low
        rows.append({
            "wish": wish,
            "best": best,
            "owned": wish.game_id in owned,
            "below_target": bool(best and wish.target_price is not None and best.price <= wish.target_price),
            "at_low": bool(best and best.cut and low is not None and best.price <= low),
        })
    sort = request.GET.get("orden", "")
    if sort in WISHLIST_SORTS:
        rows.sort(key=WISHLIST_SORTS[sort][1])
    else:
        sort = ""
    return render(request, "library/wishlist.html", {
        "rows": rows,
        "sorts": [(value, label) for value, (label, _) in WISHLIST_SORTS.items()],
        "active_sort": sort,
    })
