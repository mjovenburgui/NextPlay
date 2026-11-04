"""Vistas de la Lista de deseos (lista-de-deseos/) y del botón «Lo quiero» de la Ficha del juego, con el precio por debajo
del cual avisar.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts import notices
from accounts.models import Notification
from games.models import LANGUAGE_FILTERS, Game, language_filter

from ..models import LibraryEntry, WishlistItem
from ..wishlist import at_historical_low


def _wish_response(request, game, wish, error=""):
    """Respuesta de las acciones de la lista de deseos: el botón «Lo quiero» actualizado si es HTMX; si no,
    vuelve a la página de la que vino (campo «next», solo si es de esta web; si no, la ficha) avisando del error si
    lo hay.

    Pantallas: Ficha del juego (botón «Lo quiero»), Lista de deseos.
    """
    if request.headers.get("HX-Request"):
        return render(request, "library/_wish.html", {"game": game, "wish": wish, "wish_error": error})
    if error:
        messages.error(request, error)
    target = request.POST.get("next", "")
    # La misma comprobación que accounts.views.comun._back: «/\otra.web» también sería otra web.
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(game)


@require_POST
@login_required
def wish_toggle(request, game_id):
    """Añade el juego a la lista de deseos del usuario o lo quita si ya estaba.

    Si lo quita estando en su lista de deseos de Steam, se apunta para que la importación de Steam no lo
    vuelva a traer; si lo vuelve a añadir él, se olvida.

    Pantallas: Ficha del juego (botón «Lo quiero»), Lista de deseos.
    """
    game = get_object_or_404(Game, pk=game_id)
    wish, created = WishlistItem.objects.get_or_create(user=request.user, game=game)
    profile = request.user.profile
    if not created:
        if wish.on_steam and game.pk not in profile.steam_wishlist_skip:
            profile.steam_wishlist_skip = profile.steam_wishlist_skip + [game.pk]
            profile.save(update_fields=["steam_wishlist_skip"])
        wish.delete()
        wish = None
    elif game.pk in profile.steam_wishlist_skip:
        profile.steam_wishlist_skip = [game_id for game_id in profile.steam_wishlist_skip if game_id != game.pk]
        profile.save(update_fields=["steam_wishlist_skip"])
    return _wish_response(request, game, wish)


@require_POST
@login_required
def wish_target(request, game_id):
    """Guarda el precio por debajo del cual avisar (vacío = solo avisar del mínimo histórico).

    Al cambiarlo se olvida el último aviso, para volver a comparar con el precio nuevo. Responde 404
    si el juego no está en su lista.

    Pantallas: Ficha del juego (precio para avisar), Lista de deseos (tu precio límite), Avisos (bajadas de precio).
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


def _steam_order(row):
    """Clave de orden «Como en Steam»: primero los que ha ordenado allí, por su puesto; después los demás de su lista
    de Steam, del añadido más recientemente al más antiguo; al final, los que no están en Steam.

    Pantallas: Lista de deseos (orden «Como en Steam»).
    """
    wish = row["wish"]
    if not wish.on_steam:
        return (2, 0)
    if wish.steam_priority is None:
        return (1, -wish.steam_added_at.timestamp())
    return (0, wish.steam_priority)


# Valor de ?orden= en la lista de deseos → (etiqueta, clave de orden). Los que no tienen precio van al final.
WISHLIST_SORTS = {
    "descuento": ("Mayor descuento", lambda row: -(row["best"].cut if row["best"] else -1)),
    "precio": ("Más baratos", lambda row: (row["best"] is None, row["best"].price if row["best"] else 0)),
    "nombre": ("Nombre", lambda row: row["wish"].game.name.casefold()),
    "steam": ("Como en tu lista de Steam", _steam_order),
}


@login_required
def wishlist(request):
    """Lista de deseos del usuario con el mejor precio de hoy de cada juego (en sus tiendas, si las ha
    elegido), su mínimo histórico, si está por debajo de su límite y si está en su lista de Steam. Se ordena
    con ?orden= (recientes por defecto, descuento, precio, nombre o como en Steam) y se filtra por idioma
    con ?idioma= (ver LANGUAGE_FILTERS).

    Pantallas: Lista de deseos.
    """
    wishes = WishlistItem.objects.filter(user=request.user).select_related("game").prefetch_related("game__prices")
    total = wishes.count()
    language = request.GET.get("idioma", "")
    if language in LANGUAGE_FILTERS:
        wishes = wishes.filter(language_filter(language, prefix="game__"))
    else:
        language = ""
    owned = set(
        LibraryEntry.objects.filter(user=request.user, game__wishlisted_by__user=request.user)
        .values_list("game_id", flat=True)
    )
    shops = set(request.user.profile.shops) or None
    rows = []
    for wish in wishes:
        best = wish.game.cheapest(shops)
        rows.append({
            "wish": wish,
            "best": best,
            "owned": wish.game_id in owned,
            "below_target": bool(best and wish.target_price is not None and best.price <= wish.target_price),
            "at_low": at_historical_low(wish.game, best),
        })
    sort = request.GET.get("orden", "")
    if sort in WISHLIST_SORTS:
        rows.sort(key=WISHLIST_SORTS[sort][1])
    else:
        sort = ""
    on_steam = any(row["wish"].on_steam for row in rows)
    return render(request, "library/wishlist.html", {
        "rows": rows,
        "total": total,
        # «Como en Steam» solo si hay algo de su lista de Steam.
        "sorts": [(value, label) for value, (label, _) in WISHLIST_SORTS.items() if value != "steam" or on_steam],
        "active_sort": sort,
        "language_filters": LANGUAGE_FILTERS.items(),
        "active_language": language,
        "on_steam": on_steam,
        "skipped": len(request.user.profile.steam_wishlist_skip),
        # Por dónde le avisamos de las bajadas de precio, según lo que eligió en Editar perfil › Avisos.
        "price_bell": notices.wants(request.user, Notification.Kind.PRICE),
        "price_email": (
            bool(request.user.email) and notices.wants(request.user, Notification.Kind.PRICE, notices.EMAIL)
        ),
    })
