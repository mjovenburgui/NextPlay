"""Botones de las cuentas en la Ficha del juego: «Añadir a mis favoritos», «Tus logros» (que se cargan con HTMX) y poner de
cabecera del perfil una imagen del carrusel.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from games import media
from games.models import Game
from library.models import LibraryEntry

from .. import achievements
from ..models import FavoriteGame, Profile


def favorite_context(user, game, error=""):
    """Variables del botón «Añadir a mis favoritos» de la ficha de game para user.

    Pantallas: Ficha del juego (botón «Añadir a mis favoritos»).
    """
    favorites = list(user.favorite_games.values_list("game_id", flat=True))
    return {
        "is_favorite": game.pk in favorites,
        "favorites_count": len(favorites),
        "max_favorites": FavoriteGame.MAX_PER_USER,
        "favorite_error": error,
    }


@require_POST
@login_required
def favorite_toggle(request, game_id):
    """Añade el juego a los favoritos del perfil del usuario o lo quita si ya estaba.

    Como mucho FavoriteGame.MAX_PER_USER: con el cupo lleno avisa de que quite alguno. Los nuevos van al
    final. Con HTMX devuelve el botón actualizado; si no, vuelve a la ficha del juego.

    Pantallas: Ficha del juego (botón «Añadir a mis favoritos»), Perfil (favoritos del escaparate), Editar perfil
    (Juegos favoritos).
    """
    game = get_object_or_404(Game, pk=game_id)
    favorites = request.user.favorite_games
    error = ""
    removed, _ = favorites.filter(game=game).delete()
    if not removed:
        if favorites.count() >= FavoriteGame.MAX_PER_USER:
            error = f"Ya tienes {FavoriteGame.MAX_PER_USER} favoritos: quita alguno en Editar perfil › Escaparate."
        else:
            last = favorites.aggregate(last=Max("position"))["last"]
            FavoriteGame.objects.create(user=request.user, game=game, position=0 if last is None else last + 1)
    if request.headers.get("HX-Request"):
        return render(request, "accounts/_favorite.html", {"game": game, **favorite_context(request.user, game, error)})
    if error:
        messages.error(request, error)
    return redirect(game)


@login_required
def game_achievements(request, game_id):
    """«Tus logros» de un juego, uno a uno, en la plataforma de ?plataforma= (Steam o PlayStation; fragmento HTML que
    la ficha pide con HTMX al desplegarlos y al cambiar de orden con ?orden=): los que tiene quien mira, con cuándo los
    consiguió, y los que le faltan, con su rareza (accounts.achievements.detail). Da 404 si no tiene logros de ese juego
    leídos uno a uno.

    Pantallas: Ficha del juego («Tus logros», al desplegarlos y al cambiar el orden).
    """
    game = get_object_or_404(Game, pk=game_id)
    found = achievements.detail(
        request.user, game, request.GET.get("orden", ""), request.GET.get("plataforma", ""),
    )
    if found is None:
        raise Http404
    return render(request, "accounts/_achievements.html", {"game": game, **found})


@require_POST
@login_required
def banner_from_game(request, game_id):
    """Pone de cabecera del perfil una imagen de la ficha de un juego (el botón del visor del carrusel).

    Como en Editar perfil, solo con juegos de la biblioteca del usuario, y solo con imágenes apaisadas de
    la ficha (games.media.banner_arts). El encuadre vuelve al centro, como con cualquier imagen nueva. Con
    HTMX devuelve el aviso del visor; si no, vuelve a la ficha con un mensaje.

    Pantallas: Ficha del juego (botón del visor del carrusel para ponerla de cabecera), Perfil (cabecera).
    """
    game = get_object_or_404(Game, pk=game_id)
    art = request.POST.get("art", "")
    error = ""
    if not LibraryEntry.objects.filter(user=request.user, game=game).exists():
        error = "Añade el juego a tu biblioteca para usar sus imágenes de cabecera."
    elif art not in media.banner_arts(game):
        error = "Esa imagen no se puede poner de cabecera."
    else:
        profile = request.user.profile
        profile.banner_mode = Profile.BannerMode.GAME
        profile.banner_game = game
        profile.banner_art = art
        profile.banner_x = profile.banner_y = 50
        profile.banner_zoom = 100
        profile.banner_fit = False
        profile.save(update_fields=[
            "banner_mode", "banner_game", "banner_art", "banner_x", "banner_y", "banner_zoom", "banner_fit",
        ])
    if request.headers.get("HX-Request"):
        return render(request, "accounts/_banner_toast.html", {"error": error})
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Hecho: es la nueva cabecera de tu perfil.")
    return redirect(game)
