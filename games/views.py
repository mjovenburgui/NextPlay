"""Vistas de los juegos: Inicio (la portada), Catálogo y Ficha del juego, con los trozos de la ficha que se cargan con
HTMX («Dónde comprarlo», el historial de precios, el carrusel de imágenes y vídeos y la ficha técnica) y el botón del
Catálogo para traer juegos de IGDB.
"""

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
from lists.views import game_page_context
from social.views import composer_context, feed_context

from . import details, history, igdb, media, prices
from .filters import CatalogFilters
from .models import ENDLESS_HOURS, Game, GamePrice, Genre

PAGE_SIZE = 24
TRENDING_DAYS = 30
logger = logging.getLogger(__name__)


def trending_games(limit=5):
    """Los juegos que más gente ha añadido a su biblioteca en los últimos TRENDING_DAYS días.

    Cada juego trae players (cuántos lo tienen en total) y recent (cuántos lo añadieron en ese tiempo).
    Si no llegan a limit, se completa con los más populares de IGDB.

    Pantallas: Inicio (juegos en tendencia).
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

    Pantallas: Inicio.
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

    Busca por nombre (?q=, que también busca el nombre en España y los otros nombres del juego, como sus
    siglas), filtra y ordena con los parámetros de games.filters.CatalogFilters (tipo, género, plataforma,
    año, nota, duración, modo, tema, estudio, idioma, precio, tu biblioteca…) y pagina de 24 en 24. Las
    ediciones de otro juego no salen, y los DLC y expansiones solo si se piden: si una búsqueda tiene
    alguno escondido, lo dice (hidden_count). Si hay sesión, marca en cada tarjeta el estado del juego en
    la biblioteca del usuario (library_status, su nombre, y library_status_key, su valor, que le da el
    color). Ofrece importar de IGDB cuando hay búsqueda, sesión y credenciales de IGDB. Los filtros
    cambian la lista con HTMX, que pide la misma página; la dirección que se queda en el navegador
    (HX-Push-Url) lleva solo los filtros puestos, para que se pueda compartir.

    Pantallas: Catálogo.
    """
    filters = CatalogFilters(request.GET, request.user)
    query = filters.value("q").strip()
    games = filters.games().prefetch_related("genres")
    page = Paginator(games, PAGE_SIZE).get_page(request.GET.get("page"))

    entries = {}
    if request.user.is_authenticated:
        entries = {
            entry.game_id: entry for entry in LibraryEntry.objects.filter(user=request.user, game__in=page.object_list)
        }
    for game in page.object_list:
        entry = entries.get(game.id)
        game.library_status = entry.get_status_display() if entry else ""
        game.library_status_key = entry.status if entry else ""

    hidden_count = filters.hidden_count() if query else 0
    filter_qs = filters.query_string()
    response = render(
        request,
        "games/catalog.html",
        {
            "page": page,
            "query": query,
            "filters": filters,
            "filter_qs": filter_qs,
            "hidden_count": hidden_count,
            "all_types_qs": filters.query_string(tipo="todo") if hidden_count else "",
            "can_import": bool(query) and request.user.is_authenticated and igdb.is_configured(),
        },
    )
    if request.headers.get("HX-Request"):
        response["HX-Push-Url"] = f"{reverse('games:catalog')}?{filter_qs}" if filter_qs else reverse("games:catalog")
    return response


# Lo que se pide de cada juego para su ficha: sus géneros, plataformas y precios y lo que sale de la pasada a IGDB.
DETAIL_PREFETCH = (
    "genres", "platforms", "prices", "modes", "themes", "perspectives", "developers", "publishers",
)


def _details_context(request, game, entry, edition=None):
    """Variables de lo que sale de la pasada a IGDB en la ficha (games.details): la ficha técnica, «Expansiones y
    DLC» (addons), «Ediciones y versiones» (con edition marcada), «Juegos parecidos», de qué juego es remake, DLC…
    (origin) y lo que le falta a quien lo juega según Cuánto dura (remaining).

    Pantallas: Ficha del juego (ficha técnica, Cuánto dura, «Expansiones y DLC», «Ediciones y versiones», «Juegos
    parecidos» y cabecera).
    """
    addons = details.addons(game)
    return {
        "sheet": details.sheet(game),
        "addons": addons,
        "load_addons": _addons_pending(addons),
        "versions": details.versions(game, edition),
        "similar": details.similar(game, request.user),
        "origin": details.origin(game),
        "remaining": details.remaining(game, entry),
        "endless_hours": ENDLESS_HOURS,
    }


def _addons_pending(addons):
    """Indica si a «Expansiones y DLC» le falta algo que la ficha puede traer ya con HTMX (game_addons): alguno sin
    ficha en el catálogo, si hay claves de IGDB, o con ficha pero con sus precios sin buscar nunca, si hay clave de
    IsThereAnyDeal.

    Pantallas: Ficha del juego («Expansiones y DLC»: si se piden sus fichas y sus precios al abrirla).
    """
    missing = any(not item["in_catalog"] for item in addons)
    unpriced = any(item["in_catalog"] and not item["searched"] for item in addons)
    return (missing and igdb.is_configured()) or (unpriced and prices.is_configured())


def detail(request, slug):
    """Ficha de un juego: cabecera con su arte, datos (y de qué juego es remake, DLC…), notas de la crítica y de los
    jugadores de IGDB, valoración de la comunidad, Cuánto dura, widget de biblioteca, botones de favoritos del perfil
    y de «Añadir a una lista», las listas en las que está (lists.views.game_page_context), ficha técnica, carrusel de
    imágenes y vídeos, idiomas, precios, «Expansiones y DLC», «Ediciones y versiones», «Juegos parecidos» y reseñas.

    En las pantallas anchas va en tres columnas (la carátula con las notas, Cuánto dura y tus logros; en medio tu
    biblioteca, la sinopsis, las imágenes, los precios, las expansiones y DLC, las ediciones y las reseñas; y a la
    derecha los datos: ficha técnica, idiomas, juegos parecidos y listas). Lo que menos se mira va plegado: los
    idiomas, «Ediciones y versiones» (abierta si se llega desde una edición) y las filas de la ficha técnica a partir
    de la sexta. Las expansiones y los DLC, en cambio, van a la vista, cada uno con su precio; si alguno aún no tiene
    ficha o precio, la ficha los pide después con HTMX (game_addons).

    La ficha de una edición de otro juego («Game of the Year Edition») lleva a la de su juego, con ?edicion= para
    marcarla y decir de dónde se viene. La cabecera y el carrusel salen de games.media: si sus imágenes de IGDB
    están al día se pintan ya; si no, la ficha carga el carrusel después con HTMX (game_media). Lo mismo con la ficha
    técnica (games.details), si nunca se ha pedido a IGDB (game_details). «Dónde comprarlo» (_prices_context) se pinta
    ya si los precios del juego se han buscado alguna vez; si no, la ficha lo carga después con HTMX (game_prices),
    que antes los busca. Las reseñas de perfiles privados solo las ve su autor.

    Pantallas: Ficha del juego.
    """
    game = get_object_or_404(Game.objects.select_related("parent").prefetch_related(*DETAIL_PREFETCH), slug=slug)
    if game.parent_id:
        return redirect(f"{game.parent.get_absolute_url()}?{urlencode({'edicion': game.slug})}")
    edition = None
    if request.GET.get("edicion"):
        edition = Game.objects.filter(slug=request.GET["edicion"], parent=game).first()
    entry = wish = None
    if request.user.is_authenticated:
        entry = LibraryEntry.objects.filter(user=request.user, game=game).first()
        wish = WishlistItem.objects.filter(user=request.user, game=game).first()
    # Sin precios buscados nunca, «Dónde comprarlo» llega después: la ficha los pide con HTMX y no espera a las tiendas.
    load_prices = game.prices_updated_at is None and prices.is_configured()
    # Lo mismo con las imágenes y vídeos de IGDB del carrusel.
    media_fresh = not media.needs_update(game)
    return render(
        request,
        "games/detail.html",
        {
            "game": game,
            "edition": edition,
            "load_details": details.missing(game) and igdb.is_configured(),
            **_details_context(request, game, entry, edition),
            "hero": media.hero(game),
            "carousel": media.carousel(game) if media_fresh else None,
            "load_media": not media_fresh and igdb.is_configured(),
            "can_set_banner": entry is not None,
            "entry": entry,
            "statuses": LibraryEntry.Status.choices,
            "load_prices": load_prices,
            **({} if load_prices else _prices_context(request, game)),
            "wish": wish,
            "community": game.community_rating(),
            **review_context(request, game, entry),
            **(favorite_context(request.user, game) if request.user.is_authenticated else {}),
            **game_page_context(request, game),
        },
    )


def game_media(request, slug):
    """Carrusel de imágenes y vídeos de la ficha (fragmento HTML que la ficha carga con HTMX).

    Si nunca se pidieron a IGDB o tienen más de media.MAX_AGE, los pide antes; si IGDB falla, enseña los
    que hubiera. Si con ellos cambia la cabecera (un juego que no está en Steam y ahora tiene arte),
    manda también la nueva para que HTMX la cambie en la página.

    Pantallas: Ficha del juego (carrusel de imágenes y vídeos que se carga con HTMX).
    """
    game = get_object_or_404(Game.objects.prefetch_related("genres", "platforms", "modes"), slug=slug)
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
            "origin": details.origin(game),
            "can_set_banner": request.user.is_authenticated
            and LibraryEntry.objects.filter(user=request.user, game=game).exists(),
        },
    )


def game_details(request, slug):
    """Ficha técnica de la ficha, con «Expansiones y DLC», «Ediciones y versiones», «Juegos parecidos» y Cuánto dura
    en su sitio (fragmento HTML que la ficha carga con HTMX cuando nunca se han pedido a IGDB).

    Si siguen sin pedirse, antes los pide (details.update_details); si IGDB falla, la ficha se queda sin ellos y lo
    vuelve a intentar la próxima vez que se abra. Si con ellos se sabe de qué juego es (la ficha de un DLC, un
    remake…), manda también la cabecera, que ya lo dice («DLC de Borderlands 2»).

    Pantallas: Ficha del juego (ficha técnica, Cuánto dura, «Expansiones y DLC», «Ediciones y versiones» y «Juegos
    parecidos», cuando se cargan con HTMX; y la cabecera: de qué juego es).
    """
    game = get_object_or_404(Game, slug=slug)
    if details.missing(game) and igdb.is_configured():
        try:
            details.update_details([game])
        except igdb.IGDBError:
            logger.exception("No se pudo pedir a IGDB la ficha técnica de %s", game.slug)
    game = Game.objects.prefetch_related(*DETAIL_PREFETCH).get(pk=game.pk)
    entry = None
    if request.user.is_authenticated:
        entry = LibraryEntry.objects.filter(user=request.user, game=game).first()
    context = _details_context(request, game, entry)
    hero = media.hero(game) if context["origin"] else None
    return render(
        request,
        "games/_details.html",
        {"game": game, "entry": entry, "oob": True, **context, "hero": hero},
    )


def game_addons(request, slug):
    """«Expansiones y DLC» de la ficha con la ficha y el precio de cada uno (fragmento HTML que la ficha carga con HTMX
    cuando alguno aún no está en el catálogo o nunca se han buscado sus precios: _addons_pending).

    Antes trae de IGDB los que faltan (details.import_addons) y busca los precios de los que nunca se han buscado
    (prices.fetch_missing: los de PC a IsThereAnyDeal y, a los que se quedan sin ninguno, los de Xbox y Nintendo).
    Si IGDB falla, enseña lo que haya; si falla IsThereAnyDeal, fetch_missing lo apunta y siguen sin buscar. En los
    dos casos se vuelve a intentar la próxima vez que se abra la ficha.

    Pantallas: Ficha del juego («Expansiones y DLC», cuando se carga con HTMX; y la ficha de cada expansión o DLC, que
    ya llega con sus precios).
    """
    game = get_object_or_404(Game, slug=slug)
    if igdb.is_configured():
        try:
            details.import_addons(game)
        except igdb.IGDBError:
            logger.exception("No se pudieron traer de IGDB las expansiones y los DLC de %s", game.slug)
    unpriced = Game.objects.filter(
        igdb_id__in=details.addon_ids(game), parent__isnull=True, prices_updated_at__isnull=True,
    ).prefetch_related("platforms")
    prices.fetch_missing(list(unpriced))
    return render(request, "games/_addons.html", {"game": game, "addons": details.addons(game)})


@require_POST
@login_required
def import_search(request):
    """Importa de IGDB los resultados de una búsqueda que no estaban en el catálogo local.

    Después vuelve al catálogo con la misma búsqueda y avisa de cuántos juegos se importaron, o
    de que IGDB no respondió.

    Pantallas: Catálogo (botón para traer de IGDB lo que buscas).
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
    """Tiendas en las que compra el usuario con sesión (conjunto de ids), o vacío si no ha elegido o no hay sesión.

    Pantallas: Ficha del juego (Dónde comprarlo e historial: primero tus tiendas).
    """
    if not request.user.is_authenticated:
        return set()
    return set(request.user.profile.shops)


def _prices_context(request, game):
    """Variables de «Dónde comprarlo» (games/_prices.html).

    Los precios llegan ya repartidos por Game.price_groups(), y el mejor de ahora, de Game.cheapest() con las mismas
    tiendas, esté a la vista o plegado; store_searches() da los enlaces de búsqueda a las tiendas de consola sin
    precio. Si el usuario ha elegido sus tiendas solo se enseñan esas, salvo con
    ?tiendas=todas. El historial de precios de PC va ya pintado si está al día; si hay que pedirlo a ITAD, se carga
    después con HTMX (price_history), y su gráfica elige tienda con ?tienda=.

    Pantallas: Ficha del juego (Dónde comprarlo).
    """
    user_shops = _user_shops(request)
    show_all = request.GET.get("tiendas") == history.ALL_SHOPS or not user_shops
    shown_shops = None if show_all else user_shops
    featured_prices, listed_prices, folded_prices = game.price_groups(shown_shops)
    best_price = game.cheapest(shown_shops)
    has_pc_prices = any(price.source == GamePrice.Source.ITAD for price in game.prices.all())
    # Con el historial reciente se pinta ya; si hay que pedirlo a ITAD, la ficha lo carga después con HTMX.
    fresh = not history.needs_update(game)
    return {
        "featured_prices": featured_prices,
        "listed_prices": listed_prices,
        "folded_prices": folded_prices,
        "best_price": best_price,
        "has_pc_prices": has_pc_prices,
        "hidden_prices": 0 if show_all else sum(1 for price in game.prices.all() if price.shop_id not in user_shops),
        "user_shops": user_shops,
        "load_price_history": not fresh and has_pc_prices and not game.is_free and prices.is_configured(),
        **(_history_context(request, game, user_shops) if fresh and has_pc_prices else {}),
        "store_searches": game.store_searches(),
    }


def game_prices(request, slug):
    """«Dónde comprarlo» de la ficha (fragmento HTML que la ficha carga con HTMX si los precios del juego nunca se
    han buscado).

    Si siguen sin buscarse, antes los pide a todas las tiendas: las de PC a IsThereAnyDeal y también Xbox y
    Nintendo (prices.fetch_missing con consoles), y la ficha dice que se acaban de buscar (just_searched). Si
    IsThereAnyDeal falla, enseña los que haya y avisa de que no se han podido buscar (prices_failed); se volverá a
    intentar la próxima vez que se abra la ficha.

    Pantallas: Ficha del juego (Dónde comprarlo, cuando se carga con HTMX).
    """
    game = get_object_or_404(Game.objects.prefetch_related("platforms"), slug=slug)
    tried = game.prices_updated_at is None and prices.is_configured()
    if tried:
        prices.fetch_missing([game], consoles=True)
    # Otra vez de la base de datos, con los precios que se acaban de guardar.
    game = Game.objects.prefetch_related("platforms", "prices").get(pk=game.pk)
    wish = None
    if request.user.is_authenticated:
        wish = WishlistItem.objects.filter(user=request.user, game=game).first()
    return render(
        request,
        "games/_prices.html",
        {
            "game": game,
            "wish": wish,
            "just_searched": tried and game.prices_updated_at is not None,
            "prices_failed": tried and game.prices_updated_at is None,
            **_prices_context(request, game),
        },
    )


def _history_context(request, game, user_shops):
    """Variables de la sección del historial: el resumen de las tiendas elegidas con ?tienda= (por defecto,
    las del usuario), las opciones del selector y la elegida.

    Pantallas: Ficha del juego (historial de precios).
    """
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

    Pantallas: Ficha del juego (historial de precios: «¿compro ahora o espero?»).
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
