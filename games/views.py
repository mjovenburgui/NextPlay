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
from library.models import LibraryEntry, Review, WishlistItem
from library.views import review_context
from social.views import composer_context, feed_context

from . import history, igdb, media, prices
from .models import LANGUAGE_FILTERS, Game, GamePrice, Genre, Platform, language_filter

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
    """Portada: tu resumen (con tus seguidores y seguidos) y tu menú a la izquierda; en el centro la caja para
    publicar (social.views.composer_context, con los juegos que estás jugando para elegir de cuál hablas) y el
    feed, y los juegos en tendencia a la derecha.

    El feed tiene dos pestañas (?ver=, ver social.views.feed_context): «siguiendo», lo tuyo y lo de la gente
    que sigues, con sugerencias de gente a la que seguir si no sigues a nadie, y «descubrir», lo de toda la
    comunidad; ?antes= es el cursor de «Cargar más» para quien no tiene JavaScript. Sin sesión, en el centro
    van los juegos populares y lo último de la comunidad.
    """
    summary = None
    popular, composer = [], {}
    if request.user.is_authenticated:
        library = LibraryEntry.objects.filter(user=request.user)
        summary = library.aggregate(
            games=Count("id"),
            completed=Count("id", filter=Q(status=LibraryEntry.Status.COMPLETED)),
        )
        summary["reviews"] = Review.objects.filter(user=request.user).published().count()
        summary["followers"] = request.user.followers.count()
        summary["following"] = request.user.following.count()
        composer = composer_context(request.user)
    else:
        popular = list(Game.objects.order_by("-popularity", "name")[:12])
    return render(
        request,
        "games/home.html",
        {
            "summary": summary,
            **composer,
            "popular": popular,
            **feed_context(request.user, request.GET.get("ver", ""), request.GET.get("antes", "")),
            "trending": trending_games(),
            "genres": Genre.objects.annotate(n=Count("games")).filter(n__gt=0).order_by("-n")[:10],
        },
    )


def catalog(request):
    """Catálogo de juegos.

    Filtra por nombre (?q=, que también busca el nombre en España y los otros nombres del juego, como
    sus siglas), género (?genero=), plataforma (?plataforma=) e idioma (?idioma=, ver LANGUAGE_FILTERS)
    y pagina de 24 en 24. Si hay sesión, marca en cada tarjeta el estado del juego en la biblioteca del
    usuario. Ofrece importar de IGDB cuando hay búsqueda, sesión y credenciales de IGDB.
    """
    query = request.GET.get("q", "").strip()
    genre = request.GET.get("genero", "")
    platform = request.GET.get("plataforma", "")
    language = request.GET.get("idioma", "")
    if language not in LANGUAGE_FILTERS:
        language = ""

    games = Game.objects.all()
    if query:
        games = games.filter(
            Q(name__icontains=query) | Q(name_es__icontains=query) | Q(search_names__icontains=query)
        )
    if genre:
        games = games.filter(genres__slug=genre)
    if platform:
        games = games.filter(platforms__slug=platform)
    if language:
        games = games.filter(language_filter(language))

    page = Paginator(games, PAGE_SIZE).get_page(request.GET.get("page"))

    statuses = {}
    if request.user.is_authenticated:
        entries = LibraryEntry.objects.filter(user=request.user, game__in=page.object_list)
        statuses = {entry.game_id: entry.get_status_display() for entry in entries}
    for game in page.object_list:
        game.library_status = statuses.get(game.id, "")

    filters = {
        k: v for k, v in (("q", query), ("genero", genre), ("plataforma", platform), ("idioma", language)) if v
    }
    return render(
        request,
        "games/catalog.html",
        {
            "page": page,
            "query": query,
            "active_genre": genre,
            "active_platform": platform,
            "active_language": language,
            "language_filters": LANGUAGE_FILTERS.items(),
            "genres": Genre.objects.filter(games__isnull=False).distinct(),
            "platforms": Platform.objects.filter(games__isnull=False).distinct(),
            "filter_qs": urlencode(filters),
            "can_import": bool(query) and request.user.is_authenticated and igdb.is_configured(),
        },
    )


def detail(request, slug):
    """Ficha de un juego: cabecera con su arte, datos, nota de la crítica, valoración de la comunidad,
    widget de biblioteca, botón de favoritos del perfil, carrusel de imágenes y vídeos, precios y reseñas.

    La cabecera y el carrusel salen de games.media: si sus imágenes de IGDB están al día se pintan ya;
    si no, la ficha carga el carrusel después con HTMX (game_media). Los precios llegan ya repartidos por
    Game.price_groups(); store_searches() da los enlaces de búsqueda a las tiendas de consola sin
    precio. Si el usuario ha elegido sus tiendas solo se enseñan esas, salvo con ?tiendas=todas; la
    gráfica de precios elige tienda con ?tienda=. Las reseñas de perfiles privados solo las ve su autor.
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
    # Lo mismo con las imágenes y vídeos de IGDB del carrusel.
    media_fresh = not media.needs_update(game)
    return render(
        request,
        "games/detail.html",
        {
            "game": game,
            "hero": media.hero(game),
            "carousel": media.carousel(game) if media_fresh else None,
            "load_media": not media_fresh and igdb.is_configured(),
            "can_set_banner": entry is not None,
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
            **review_context(request, game, entry),
            **(favorite_context(request.user, game) if request.user.is_authenticated else {}),
        },
    )


def game_media(request, slug):
    """Carrusel de imágenes y vídeos de la ficha (fragmento HTML que la ficha carga con HTMX).

    Si nunca se pidieron a IGDB o tienen más de media.MAX_AGE, los pide antes; si IGDB falla, enseña los
    que hubiera. Si con ellos cambia la cabecera (un juego que no está en Steam y ahora tiene arte),
    manda también la nueva para que HTMX la cambie en la página.
    """
    game = get_object_or_404(Game.objects.prefetch_related("genres", "platforms"), slug=slug)
    before = media.hero(game)
    if media.needs_update(game) and igdb.is_configured():
        try:
            media.update_media(game)
        except igdb.IGDBError:
            logger.exception("No se pudieron pedir a IGDB las imágenes de %s", game.slug)
    hero = media.hero(game)
    return render(
        request,
        "games/_media.html",
        {
            "game": game,
            "carousel": media.carousel(game),
            "hero": hero,
            "hero_oob": hero != before,
            "can_set_banner": request.user.is_authenticated
            and LibraryEntry.objects.filter(user=request.user, game=game).exists(),
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
