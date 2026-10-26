from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import Notification
from games.models import Game, format_price, search_filter
from library.models import LibraryEntry, WishlistItem
from library.views import _report, comment_thread
from social import activity

from .forms import GameListForm, ListCommentForm
from .models import LIST_MAX_GAMES, NOTE_MAX_LENGTH, GameList, ListComment, ListItem, ListLike

# Juegos que salen al buscar en el editor de una lista.
SEARCH_RESULTS = 8
# Listas que salen en la ficha de un juego («En N listas») y en el escaparate del perfil.
GAME_PAGE_LISTS = 4
SHOWCASE_LISTS = 3
# Listas por página en la de todas las listas en las que está un juego.
LISTS_PER_PAGE = 24
# Estados de la biblioteca que cuentan como jugado en el progreso de una lista: todos menos Pendiente.
PLAYED_STATUSES = [
    LibraryEntry.Status.PLAYING, LibraryEntry.Status.COMPLETED, LibraryEntry.Status.PLAYED, LibraryEntry.Status.DROPPED,
]
# Cómo se ven los juegos en la página de una lista (?vista=): portadas en cuadrícula o filas con el comentario de cada uno.
VIEWS = {"portadas": "Portadas", "detalle": "Con comentarios"}


def mark_progress(lists, viewer):
    """Apunta en cada lista de lists cuántos de sus juegos ha jugado viewer (viewer_played; None sin sesión).

    Jugado es cualquier estado de la biblioteca menos Pendiente (PLAYED_STATUSES). Devuelve la misma lista.
    """
    counts = {}
    if viewer.is_authenticated and lists:
        counts = dict(
            ListItem.objects.filter(
                game_list__in=lists, game__library_entries__user=viewer,
                game__library_entries__status__in=PLAYED_STATUSES,
            ).values_list("game_list_id").annotate(n=Count("id"))
        )
    for game_list in lists:
        game_list.viewer_played = counts.get(game_list.pk, 0) if viewer.is_authenticated else None
    return lists


def mark_liked_lists(lists, viewer):
    """Apunta en cada lista de lists (liked) si viewer le ha dado «me gusta»; devuelve la misma lista."""
    liked = set()
    if viewer.is_authenticated and lists:
        liked = set(ListLike.objects.filter(user=viewer, game_list__in=lists).values_list("game_list_id", flat=True))
    for game_list in lists:
        game_list.liked = game_list.pk in liked
    return lists


def profile_lists(owner, viewer, public_only=False):
    """Listas de owner que puede ver viewer, de la más reciente a la más antigua, con lo que enseñan sus tarjetas
    (GameListQuerySet.with_preview) y cuántos de sus juegos ha jugado viewer (mark_progress).

    Con public_only salen solo las públicas, como las vería cualquiera (la vista previa de Editar perfil).
    """
    lists = GameList.objects.filter(user=owner)
    lists = lists.filter(is_public=True) if public_only else lists.visible_to(viewer)
    return mark_progress(list(lists.with_preview()), viewer)


def _lists_with(game, viewer):
    """Listas que viewer puede ver en las que está game, de la más reciente a la más antigua, con sus tarjetas cargadas."""
    return (
        GameList.objects.visible_to(viewer)
        .filter(pk__in=ListItem.objects.filter(game=game).values("game_list_id"))
        .with_preview()
    )


def picker_context(user, game):
    """Variables del desplegable «Añadir a una lista» de la ficha de game (lists/_picker.html).

    Las listas de user, de la más reciente a la más antigua, cada una con cuántos juegos tiene (game_count) y si
    game ya está en ella (has_game), y en cuántas está (in_lists).
    """
    lists = list(
        GameList.objects.filter(user=user).annotate(
            game_count=Count("items"),
            has_game=Exists(ListItem.objects.filter(game_list=OuterRef("pk"), game=game)),
        ).order_by("-updated_at", "-id")
    )
    return {"my_lists": lists, "in_lists": sum(1 for game_list in lists if game_list.has_game)}


def game_page_context(request, game):
    """Variables de las listas en la ficha de game: con sesión, las del desplegable «Añadir a una lista»
    (picker_context); y para todos, «En N listas»: las GAME_PAGE_LISTS más recientes que puede ver quien mira
    (game_lists) y cuántas son (game_lists_count)."""
    lists = _lists_with(game, request.user)
    context = {"game_lists": list(lists[:GAME_PAGE_LISTS]), "game_lists_count": lists.count()}
    if request.user.is_authenticated:
        context.update(picker_context(request.user, game))
    return context


def game_lists(request, slug):
    """Todas las listas en las que está un juego que puede ver quien mira, de la más reciente a la más antigua, de
    LISTS_PER_PAGE en LISTS_PER_PAGE (?page=)."""
    game = get_object_or_404(Game, slug=slug)
    page = Paginator(_lists_with(game, request.user), LISTS_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "lists/game_lists.html", {
        "game": game, "page": page, "lists": mark_progress(list(page.object_list), request.user),
    })


def detail(request, username, list_id):
    """Página de una lista: su mosaico, el título, el autor, la descripción y los juegos, en portadas o con el
    comentario de cada uno (?vista=; sin ella, con los comentarios si alguno tiene), y debajo sus comentarios.

    La ve quien puede verla (GameList.visible_to); si no, 404. Con sesión, enseña cuánto ha jugado quien mira de
    la lista, el estado de cada juego en su biblioteca y, de lo que no tiene, cuánto cuesta (list_progress). Los
    demás pueden darle «me gusta» y denunciarla (con ?denunciar, el formulario llega abierto).
    """
    game_list = get_object_or_404(GameList.objects.with_preview(), pk=list_id, user__username=username)
    viewer = request.user
    if not game_list.visible_to(viewer):
        raise Http404
    items = list(game_list.items.select_related("game").prefetch_related("game__prices"))
    progress = list_progress([item.game for item in items], viewer) if viewer.is_authenticated else None
    for item in items:
        item.viewer_status = progress["statuses"].get(item.game_id, "") if progress else ""
    view = request.GET.get("vista", "")
    if view not in VIEWS:
        view = "detalle" if any(item.note for item in items) else "portadas"
    is_owner = viewer == game_list.user
    mark_liked_lists([game_list], viewer)
    return render(request, "lists/detail.html", {
        "game_list": game_list,
        "owner": game_list.user,
        "items": items,
        "is_owner": is_owner,
        "progress": progress,
        "view": view,
        "views": VIEWS.items(),
        "reported": viewer.is_authenticated and not is_owner and game_list.reports.filter(reporter=viewer).exists(),
        "open_report": "denunciar" in request.GET,
        **_list_comments(game_list, viewer),
        "comment_form": ListCommentForm(),
    })


def _readable_list(request, list_id):
    """Lista list_id con su dueño cargado, si el usuario puede verla; si no, 404."""
    game_list = get_object_or_404(GameList.objects.select_related("user__profile"), pk=list_id)
    if not game_list.visible_to(request.user):
        raise Http404
    return game_list


@require_POST
@login_required
def like(request, list_id):
    """Da o quita el «me gusta» del usuario a una lista de otro, avisando a su dueño la primera vez.

    Con HTMX devuelve el botón con el contador nuevo; si no, vuelve a la lista. Las propias no se pueden marcar (400).
    """
    game_list = _readable_list(request, list_id)
    if game_list.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes darle «me gusta» a tu propia lista.")
    mark, created = ListLike.objects.get_or_create(user=request.user, game_list=game_list)
    if created:
        _notify_like(game_list, request.user)
    else:
        mark.delete()
    if request.headers.get("HX-Request"):
        return render(request, "lists/_like.html", {
            "game_list": game_list, "liked": created, "like_count": game_list.likes.count(),
        })
    return redirect(game_list)


def _notify_like(game_list, actor):
    """Avisa al dueño de game_list de que a actor le ha gustado, solo la primera vez aunque lo quite y lo vuelva a dar."""
    url = game_list.get_absolute_url()
    notices = Notification.objects.filter(user=game_list.user, kind=Notification.Kind.LIST_LIKE, actor=actor, url=url)
    if not notices.exists():
        Notification.objects.create(
            user=game_list.user, kind=Notification.Kind.LIST_LIKE, actor=actor, url=url,
            text=f"A {actor.profile.name} le ha gustado tu lista «{game_list.title}»."[:300],
        )


def _list_comments(game_list, viewer):
    """Variables de library/_comments.html para los comentarios de game_list (ver library.views.comment_thread)."""
    return comment_thread(
        game_list, game_list.comments.select_related("game_list"), viewer,
        comment_url=reverse("lists:comment", args=[game_list.pk]),
        delete_url_name="lists:comment_delete", report_url_name="lists:comment_report",
        count_id=f"list-comment-count-{game_list.pk}",
    )


@require_POST
@login_required
def comment(request, list_id):
    """Publica un comentario en una lista (avisando a su dueño si no es él) y vuelve a sus comentarios.

    Con HTMX devuelve los comentarios ya con el nuevo (o el formulario con el error) y el contador de la página.
    """
    game_list = _readable_list(request, list_id)
    form = ListCommentForm(request.POST)
    if form.is_valid():
        new = form.save(commit=False)
        new.game_list, new.user = game_list, request.user
        new.save()
        if game_list.user_id != request.user.pk:
            _notify_comment(game_list, request.user)
        form = ListCommentForm()
    elif not request.headers.get("HX-Request"):
        messages.error(request, " ".join(form.errors.get("text", ["El comentario no es válido."])))
    if request.headers.get("HX-Request"):
        return render(request, "library/_comments.html", {
            "comment_form": form, "oob": True, **_list_comments(game_list, request.user),
        })
    return redirect(f"{game_list.get_absolute_url()}#comentarios")


def _notify_comment(game_list, actor):
    """Avisa al dueño de game_list de que actor la ha comentado, salvo si ya tiene un aviso igual sin leer."""
    url = f"{game_list.get_absolute_url()}#comentarios"
    notices = Notification.objects.filter(
        user=game_list.user, kind=Notification.Kind.LIST_COMMENT, actor=actor, url=url, read_at=None,
    )
    if not notices.exists():
        Notification.objects.create(
            user=game_list.user, kind=Notification.Kind.LIST_COMMENT, actor=actor, url=url,
            text=f"{actor.profile.name} ha comentado tu lista «{game_list.title}»."[:300],
        )


@require_POST
@login_required
def comment_delete(request, comment_id):
    """Borra un comentario de una lista: solo quien lo escribió o el dueño de la lista (si no, 403)."""
    target = get_object_or_404(ListComment.objects.select_related("game_list__user__profile"), pk=comment_id)
    if not target.can_delete(request.user):
        return HttpResponseForbidden("No puedes borrar este comentario.")
    game_list = _readable_list(request, target.game_list_id)
    target.delete()
    if request.headers.get("HX-Request"):
        return render(request, "library/_comments.html", {
            "comment_form": ListCommentForm(), "oob": True, **_list_comments(game_list, request.user),
        })
    return redirect(f"{game_list.get_absolute_url()}#comentarios")


@require_POST
@login_required
def comment_report(request, comment_id):
    """Denuncia un comentario de otro en una lista; los propios no (400) y los de listas que no se pueden ver dan 404."""
    target = get_object_or_404(ListComment.objects.select_related("user"), pk=comment_id)
    _readable_list(request, target.game_list_id)
    if target.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propio comentario.")
    return _report(request, target, "comentario", None, target.get_absolute_url())


@require_POST
@login_required
def report(request, list_id):
    """Denuncia una lista de otro para que la revise un administrador (library.views._report); las propias no (400)."""
    game_list = _readable_list(request, list_id)
    if game_list.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propia lista.")
    return _report(request, game_list, "lista", None, game_list.get_absolute_url())


def list_progress(games, viewer):
    """Cómo va viewer con los juegos games de una lista (con sus precios ya cargados).

    Cuántos son (total), cuántos tiene en su biblioteca (owned), cuántos ha jugado (played: cualquier estado
    menos Pendiente; percent, en porcentaje), terminado (completed) y tiene en Pendiente (backlog), las horas que
    lleva en ellos (hours: las de Steam, PlayStation y Xbox), el estado de cada uno (statuses: id del juego →
    nombre del estado) y lo que le falta (ver _missing).
    """
    entries = {entry.game_id: entry for entry in LibraryEntry.objects.filter(user=viewer, game__in=games)}
    played = sum(1 for entry in entries.values() if entry.status in PLAYED_STATUSES)
    minutes = sum(entry.playtime_minutes or 0 for entry in entries.values())
    return {
        "total": len(games),
        "owned": len(entries),
        "played": played,
        "percent": round(100 * played / len(games)) if games else 0,
        "completed": sum(1 for entry in entries.values() if entry.status == LibraryEntry.Status.COMPLETED),
        "backlog": sum(1 for entry in entries.values() if entry.status == LibraryEntry.Status.BACKLOG),
        "hours": round(minutes / 60),
        "statuses": {game_id: entry.get_status_display() for game_id, entry in entries.items()},
        **_missing([game for game in games if game.pk not in entries], viewer),
    }


def _missing(games, viewer):
    """Lo que le falta a viewer de una lista: games, los juegos que no están en su biblioteca, y lo que cuestan.

    Cuántos son (missing); cuántos tienen precio en sus tiendas (priced) y lo que suman hoy (today) y si espera a
    que cada uno baje a su mínimo histórico de PC (at_lows; el de hoy si no lo tiene o si hoy está más barato),
    con saving si así sale más barato; cuántos no tienen precio (unpriced: los de consolas sin tienda); y cuántos
    están ya en su lista de deseos (wished) y cuántos no (to_wish), para el botón de pasarlos a «Lo quiero».
    """
    shops = set(viewer.profile.shops) or None
    wished = WishlistItem.objects.filter(user=viewer, game__in=games).count()
    today = lows = Decimal(0)
    priced, symbol = 0, "€"
    for game in games:
        best = game.cheapest(shops)
        if best is None:
            continue
        priced += 1
        symbol = best.symbol
        today += best.price
        lows += min(game.historical_low, best.price) if game.historical_low is not None else best.price
    return {
        "missing": len(games),
        "priced": priced,
        "unpriced": len(games) - priced,
        "today": format_price(today, symbol),
        "at_lows": format_price(lows, symbol),
        "saving": lows < today,
        "wished": wished,
        "to_wish": len(games) - wished,
    }


@require_POST
@login_required
def wish_missing(request, list_id):
    """Pasa a la lista de deseos del usuario los juegos de una lista que ni tiene en su biblioteca ni quiere ya, y
    vuelve a la lista. Como «Lo quiero» en la ficha: si alguno lo había quitado de su lista de deseos de Steam, se
    olvida, para que la importación de Steam no lo vuelva a quitar."""
    game_list = get_object_or_404(GameList.objects.select_related("user__profile"), pk=list_id)
    if not game_list.visible_to(request.user):
        raise Http404
    games = list(
        Game.objects.filter(list_items__game_list=game_list)
        .exclude(pk__in=LibraryEntry.objects.filter(user=request.user).values("game_id"))
        .exclude(pk__in=WishlistItem.objects.filter(user=request.user).values("game_id"))
    )
    WishlistItem.objects.bulk_create([WishlistItem(user=request.user, game=game) for game in games], ignore_conflicts=True)
    added = {game.pk for game in games}
    profile = request.user.profile
    if added & set(profile.steam_wishlist_skip):
        profile.steam_wishlist_skip = [game_id for game_id in profile.steam_wishlist_skip if game_id not in added]
        profile.save(update_fields=["steam_wishlist_skip"])
    if games:
        count = len(games)
        verb = "baje" if count == 1 else "bajen"
        messages.success(
            request,
            f"{count} juego{pluralize(count)} añadido{pluralize(count)} a tu lista de deseos: te avisaremos cuando {verb} de precio.",
        )
    else:
        messages.success(request, "Ya tienes o quieres todos los juegos de esta lista.")
    return redirect(game_list)


def _own_list(request, list_id):
    """Lista list_id del usuario; las de otros dan 404."""
    return get_object_or_404(GameList, pk=list_id, user=request.user)


def editor_context(game_list):
    """Variables de los juegos del editor (lists/_editor_items.html): la lista, sus juegos en orden y los máximos."""
    return {
        "game_list": game_list,
        "items": list(game_list.items.select_related("game")),
        "max_games": LIST_MAX_GAMES,
        "note_max": NOTE_MAX_LENGTH,
    }


def search_context(game_list, query):
    """Variables del buscador del editor: la búsqueda (search_query; no query, que es la del buscador de la cabecera)
    y, si la hay, los SEARCH_RESULTS juegos más populares que la cumplen (results, como el buscador del catálogo), cada
    uno marcado si ya está en la lista (in_list)."""
    results = []
    if query:
        results = list(Game.objects.filter(search_filter(query))[:SEARCH_RESULTS])
        listed = set(game_list.items.filter(game__in=results).values_list("game_id", flat=True))
        for game in results:
            game.in_list = game.pk in listed
    return {"search_query": query, "results": results}


@login_required
def new(request):
    """Crea una lista del usuario (POST) o enseña el formulario para hacerlo (GET).

    Con juego= (desde «Nueva lista con este juego» en la ficha) la lista empieza con ese juego. Se apunta en su
    actividad (social.activity.list_created) y lleva a su editor, para buscar y añadir más.
    """
    raw = request.POST.get("juego") or request.GET.get("juego", "")
    game = Game.objects.filter(pk=raw).first() if raw.isdigit() else None
    form = GameListForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        game_list = form.save(commit=False)
        game_list.user = request.user
        game_list.save()
        activity.list_created(game_list)
        if game:
            game_list.add(game)
            activity.list_games_added(game_list)
        messages.success(request, f"Lista creada{' con ' + game.name if game else ''}. Busca aquí los juegos que quieras añadir.")
        return redirect(f"{reverse('lists:edit', args=[game_list.pk])}#juegos")
    return render(request, "lists/form.html", {"form": form, "game": game})


@login_required
def edit(request, list_id):
    """Editor de una lista del usuario: sus datos (título, descripción, si la ven los demás y si va numerada), que
    se guardan con el formulario, y sus juegos, que se buscan (?q=, ver search), se añaden, se ordenan, se
    comentan y se quitan uno a uno (add, move, note y remove). Las de otros dan 404.

    El formulario trabaja con una copia de la lista para que, si no es válido, la página siga con lo guardado.
    """
    game_list = _own_list(request, list_id)
    form = GameListForm(request.POST or None, instance=GameList.objects.get(pk=game_list.pk))
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Lista guardada.")
            return redirect(game_list)
        messages.error(request, "No se ha guardado: revisa los campos marcados en rojo.")
    return render(request, "lists/edit.html", {
        "form": form, **editor_context(game_list), **search_context(game_list, request.GET.get("q", "").strip()),
    })


@require_POST
@login_required
def delete(request, list_id):
    """Borra una lista del usuario (los juegos siguen en su biblioteca y en el catálogo) y vuelve a sus listas."""
    game_list = _own_list(request, list_id)
    title = game_list.title
    game_list.delete()
    messages.success(request, f"Lista «{title}» borrada.")
    return redirect(f"{reverse('profile', args=[request.user.username])}?vista=listas")


@login_required
def search(request, list_id):
    """Busca en el catálogo juegos para añadir a una lista del usuario (?q=, ver search_context).

    Con HTMX devuelve solo los resultados; sin HTMX lleva al editor con la búsqueda hecha.
    """
    game_list = _own_list(request, list_id)
    query = request.GET.get("q", "").strip()
    if not request.headers.get("HX-Request"):
        return redirect(f"{reverse('lists:edit', args=[game_list.pk])}?{urlencode({'q': query})}#juegos")
    return render(request, "lists/_search_results.html", {"game_list": game_list, **search_context(game_list, query)})


@require_POST
@login_required
def add(request, list_id, game_id):
    """Añade un juego al final de una lista del usuario (si ya estaba, no hace nada); caben LIST_MAX_GAMES.

    Se apunta en su actividad (social.activity.list_games_added) y responde según desde dónde se pulsa (ver
    _item_response).
    """
    game_list = _own_list(request, list_id)
    game = get_object_or_404(Game, pk=game_id)
    error = ""
    if not game_list.items.filter(game=game).exists() and game_list.items.count() >= LIST_MAX_GAMES:
        error = f"Caben {LIST_MAX_GAMES} juegos por lista: quita alguno para añadir más."
    elif game_list.add(game):
        activity.list_games_added(game_list)
    return _item_response(request, game_list, game, error)


@require_POST
@login_required
def remove(request, list_id, game_id):
    """Quita un juego de una lista del usuario (si no estaba, no hace nada) y responde como add. Si lo acababa de
    añadir, también se descuenta de su actividad (social.activity.list_game_removed)."""
    game_list = _own_list(request, list_id)
    game = get_object_or_404(Game, pk=game_id)
    if game_list.remove(game):
        activity.list_game_removed(game_list)
    return _item_response(request, game_list, game)


@require_POST
@login_required
def move(request, list_id, game_id):
    """Cambia de puesto un juego de una lista del usuario: uno arriba o abajo (a=arriba o a=abajo) o al puesto que se
    escriba (puesto=, desde 1; si se pasa, al principio o al final). Responde como add.

    Da 404 si el juego no está en la lista y 400 si el puesto no es un número.
    """
    game_list = _own_list(request, list_id)
    game = get_object_or_404(Game, pk=game_id)
    order = list(game_list.items.values_list("game_id", flat=True))
    if game.pk not in order:
        raise Http404
    direction = request.POST.get("a", "")
    if direction in ("arriba", "abajo"):
        position = order.index(game.pk) + (-1 if direction == "arriba" else 1)
    else:
        try:
            position = int(request.POST.get("puesto", "")) - 1
        except ValueError:
            return HttpResponseBadRequest("Puesto no válido.")
    game_list.move(game, position)
    return _item_response(request, game_list, game)


@require_POST
@login_required
def note(request, list_id, game_id):
    """Guarda el comentario de quien hace la lista sobre uno de sus juegos (vacío lo quita); caben NOTE_MAX_LENGTH caracteres.

    Con HTMX devuelve solo su formulario, marcado como guardado o con el error, para no rehacer los demás juegos
    del editor; sin HTMX vuelve al editor, a ese juego.
    """
    game_list = _own_list(request, list_id)
    item = get_object_or_404(ListItem.objects.select_related("game"), game_list=game_list, game_id=game_id)
    text = " ".join(request.POST.get("note", "").split())
    error = ""
    if len(text) > NOTE_MAX_LENGTH:
        error = f"Caben {NOTE_MAX_LENGTH} caracteres."
    elif text != item.note:
        item.note = text
        item.save(update_fields=["note"])
        game_list.touch()
    if request.headers.get("HX-Request"):
        return render(request, "lists/_note_form.html", {
            "game_list": game_list, "item": item, "note_max": NOTE_MAX_LENGTH,
            "saved": not error, "note_error": error, "draft": text if error else None,
        })
    if error:
        messages.error(request, f"El comentario de {item.game.name} no se ha guardado: caben {NOTE_MAX_LENGTH} caracteres.")
    return redirect(f"{reverse('lists:edit', args=[game_list.pk])}#juego-{game_id}")


def _item_response(request, game_list, game, error=""):
    """Respuesta de añadir, quitar o mover un juego de una lista, según desde dónde se pulsa (desde=).

    Con HTMX: desde la ficha («ficha»), el desplegable «Añadir a una lista» abierto; desde el buscador del editor
    («buscador»), los juegos del editor y, fuera de su sitio, el resultado ya marcado como añadido; desde el
    editor, sus juegos. Sin HTMX vuelve a next (si es de esta web) o al editor, avisando del error si lo hay.
    """
    where = request.POST.get("desde", "")
    if request.headers.get("HX-Request"):
        if where == "ficha":
            return render(request, "lists/_picker.html", {
                "game": game, **picker_context(request.user, game), "open": True, "picker_error": error,
            })
        context = {**editor_context(game_list), "list_error": error}
        if where == "buscador":
            game.in_list = game_list.items.filter(game=game).exists()
            context["added_result"] = game
        return render(request, "lists/_editor_items.html", context)
    if error:
        messages.error(request, error)
    back = request.POST.get("next", "")
    if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        return redirect(back)
    return redirect(f"{reverse('lists:edit', args=[game_list.pk])}#juegos")
