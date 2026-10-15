from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from library.models import LibraryEntry

from . import igdb
from .models import Game, GamePrice, Genre, Platform

PAGE_SIZE = 24


def catalog(request):
    """Catálogo de juegos, que también es la portada.

    Filtra por nombre (?q=), género (?genero=) y plataforma (?plataforma=) y pagina de 24 en 24.
    Si hay sesión, marca en cada tarjeta el estado del juego en la biblioteca del usuario. Ofrece
    importar de IGDB cuando hay búsqueda, sesión y credenciales de IGDB.
    """
    query = request.GET.get("q", "").strip()
    genre = request.GET.get("genero", "")
    platform = request.GET.get("plataforma", "")

    games = Game.objects.all()
    if query:
        games = games.filter(name__icontains=query)
    if genre:
        games = games.filter(genres__slug=genre)
    if platform:
        games = games.filter(platforms__slug=platform)

    page = Paginator(games, PAGE_SIZE).get_page(request.GET.get("page"))

    statuses = {}
    if request.user.is_authenticated:
        entries = LibraryEntry.objects.filter(user=request.user, game__in=page.object_list)
        statuses = {entry.game_id: entry.get_status_display() for entry in entries}
    for game in page.object_list:
        game.library_status = statuses.get(game.id, "")

    filters = {k: v for k, v in (("q", query), ("genero", genre), ("plataforma", platform)) if v}
    return render(
        request,
        "games/catalog.html",
        {
            "page": page,
            "query": query,
            "active_genre": genre,
            "active_platform": platform,
            "genres": Genre.objects.filter(games__isnull=False).distinct(),
            "platforms": Platform.objects.filter(games__isnull=False).distinct(),
            "filter_qs": urlencode(filters),
            "can_import": bool(query) and request.user.is_authenticated and igdb.is_configured(),
        },
    )


def detail(request, slug):
    """Ficha de un juego: datos, nota de la crítica, valoración de la comunidad, widget de biblioteca y precios.

    Los precios llegan ya repartidos por Game.price_groups(); store_searches() da los enlaces de
    búsqueda a las tiendas de consola sin precio.
    """
    game = get_object_or_404(Game.objects.prefetch_related("genres", "platforms", "prices"), slug=slug)
    entry = None
    if request.user.is_authenticated:
        entry = LibraryEntry.objects.filter(user=request.user, game=game).first()
    featured_prices, listed_prices, folded_prices, best_price = game.price_groups()
    return render(
        request,
        "games/detail.html",
        {
            "game": game,
            "entry": entry,
            "statuses": LibraryEntry.Status.choices,
            "featured_prices": featured_prices,
            "listed_prices": listed_prices,
            "folded_prices": folded_prices,
            "best_price": best_price,
            "has_pc_prices": any(price.source == GamePrice.Source.ITAD for price in game.prices.all()),
            "store_searches": game.store_searches(),
            "community": game.community_rating(),
        },
    )


@require_POST
@login_required
def import_search(request):
    """Importa de IGDB los resultados de una búsqueda que no estaban en el catálogo local.

    Después vuelve al catálogo con la misma búsqueda y avisa de cuántos juegos se importaron, o
    de que IGDB no respondió.
    """
    query = request.POST.get("q", "").strip()
    if query:
        try:
            imported = igdb.upsert_games(igdb.IGDBClient().search(query))
        except igdb.IGDBError:
            messages.error(request, "No se pudo consultar IGDB. Inténtalo de nuevo más tarde.")
        else:
            messages.success(request, f"{len(imported)} juegos importados de IGDB.")
    return redirect(f"{reverse('games:catalog')}?{urlencode({'q': query})}")
