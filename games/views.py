import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from accounts.views import favorite_context
from library.models import LibraryEntry, WishlistItem
from library.views import review_context

from . import history, igdb, prices
from .models import Game, GamePrice, Genre, Platform

PAGE_SIZE = 24
TRENDING_DAYS = 30
logger = logging.getLogger(__name__)


def trending_games(limit=5):
    """Los juegos que más gente ha añadido a su biblioteca en los últimos TRENDING_DAYS días.

    Cada juego trae players (cuántos lo tienen en total) y recent (cuántos lo añadieron en ese tiempo).
    Si no llegan a limit, se completa con los más populares de IGDB.
    """
    since = timezone.now() - timedelta(days=TRENDING_DAYS)
    games = Game.objects.prefetch_related("genres").annotate(
        players=Count("library_entries", distinct=True),
        recent=Count("library_entries", filter=Q(library_entries__created_at__gte=since), distinct=True),
    )
    trending = list(games.filter(recent__gt=0).order_by("-recent", "-players", "-popularity")[:limit])
    if len(trending) < limit:
        trending += games.exclude(id__in=[g.id for g in trending]).order_by("-popularity", "name")[: limit - len(trending)]
    return trending


def home(request):
    """Portada: tu resumen y tu menú a la izquierda; lo que estás jugando, los juegos populares y las
    últimas reseñas en el centro; los juegos en tendencia a la derecha."""
    playing = summary = None
    statuses = {}
    if request.user.is_authenticated:
        library = LibraryEntry.objects.filter(user=request.user)
        summary = library.aggregate(
            games=Count("id"),
            completed=Count("id", filter=Q(status=LibraryEntry.Status.COMPLETED)),
            reviews=Count("id", filter=~Q(review="")),
        )
        playing = library.filter(status=LibraryEntry.Status.PLAYING).select_related("game").order_by("-updated_at")[:4]
    popular = list(Game.objects.order_by("-popularity", "name")[:12])
    if request.user.is_authenticated:
        entries = LibraryEntry.objects.filter(user=request.user, game__in=popular)
        statuses = {entry.game_id: entry.get_status_display() for entry in entries}
    for game in popular:
        game.library_status = statuses.get(game.id, "")
    reviews = LibraryEntry.objects.reviews_visible_to(request.user)[:5]
    return render(
        request,
        "games/home.html",
        {
            "summary": summary,
            "playing": playing,
            "popular": popular,
            "recent_reviews": reviews,
            "trending": trending_games(),
            "genres": Genre.objects.annotate(n=Count("games")).filter(n__gt=0).order_by("-n")[:10],
        },
    )


def catalog(request):
    """Catálogo de juegos.

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
    """Ficha de un juego: datos, nota de la crítica, valoración de la comunidad, widget de biblioteca,
    botón de favoritos del perfil, precios y reseñas.

    Los precios llegan ya repartidos por Game.price_groups(); store_searches() da los enlaces de
    búsqueda a las tiendas de consola sin precio. Si el usuario ha elegido sus tiendas solo se enseñan
    esas, salvo con ?tiendas=todas; la gráfica de precios elige tienda con ?tienda=. Las reseñas de
    perfiles privados solo las ve su autor.
    """
    game = get_object_or_404(Game.objects.prefetch_related("genres", "platforms", "prices"), slug=slug)
    entry = wish = None
    if request.user.is_authenticated:
        entry = LibraryEntry.objects.filter(user=request.user, game=game).first()
        wish = WishlistItem.objects.filter(user=request.user, game=game).first()
    user_shops = _user_shops(request)
    show_all = request.GET.get("tiendas") == history.ALL_SHOPS or not user_shops
    featured_prices, listed_prices, folded_prices, best_price = game.price_groups(None if show_all else user_shops)
    hidden_prices = 0 if show_all else sum(1 for price in game.prices.all() if price.shop_id not in user_shops)
    has_pc_prices = any(price.source == GamePrice.Source.ITAD for price in game.prices.all())
    # Con el historial reciente se pinta ya; si hay que pedirlo a ITAD, la ficha lo carga después con HTMX.
    fresh = not history.needs_update(game)
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
            "has_pc_prices": has_pc_prices,
            "hidden_prices": hidden_prices,
            "user_shops": user_shops,
            "load_price_history": not fresh and has_pc_prices and not game.is_free and prices.is_configured(),
            **(_history_context(request, game, user_shops) if fresh and has_pc_prices else {}),
            "wish": wish,
            "store_searches": game.store_searches(),
            "community": game.community_rating(),
            **review_context(game, entry, request.user),
            **(favorite_context(request.user, game) if request.user.is_authenticated else {}),
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


def _user_shops(request):
    """Tiendas en las que compra el usuario con sesión (conjunto de ids), o vacío si no ha elegido o no hay sesión."""
    if not request.user.is_authenticated:
        return set()
    return set(request.user.profile.shops)


def _history_context(request, game, user_shops):
    """Variables de la sección del historial: el resumen de las tiendas elegidas con ?tienda= (por defecto,
    las del usuario), las opciones del selector y la elegida."""
    shops, selected = history.selected_shops(request.GET.get("tienda", ""), user_shops)
    return {
        "price_history": history.summary(game, shops=shops),
        "history_shops": history.shop_options(game, user_shops),
        "history_shop": selected,
        "history_has_data": isinstance(game.price_history, dict) and bool(game.price_history.get("shops")),
    }


def price_history(request, slug):
    """Historial de precios de PC del juego con el veredicto «¿compro ahora o espero?» (fragmento HTML).

    Si el guardado tiene más de un día, lo pide antes a IsThereAnyDeal; si ITAD falla, enseña el que
    hubiera. La ficha lo carga con HTMX para no esperar a ITAD, y el selector de tienda lo vuelve a
    pedir con ?tienda=.
    """
    game = get_object_or_404(Game.objects.prefetch_related("prices"), slug=slug)
    has_pc_prices = any(price.source == GamePrice.Source.ITAD for price in game.prices.all())
    if has_pc_prices and history.needs_update(game) and prices.is_configured():
        try:
            history.update_history(game)
        except prices.ITADError:
            logger.exception("Falló el historial de precios de %s", game.slug)
    return render(
        request, "games/_price_history.html", {"game": game, **_history_context(request, game, _user_shops(request))}
    )
