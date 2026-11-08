"""Vistas de Editar lista (listas/<id>/editar/): los datos de la lista, su buscador y los botones de cada juego (añadir,
quitar, mover, comentar y poner en un nivel), que también se pulsan desde «Añadir a una lista» de la Ficha del juego y
desde el tablero de la Página de la lista. Quién puede editarla va en colaboradores.py.
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from games.models import Game, search_filter
from library.models import LibraryEntry
from social import activity

from ..forms import GameListForm
from ..models import LIST_MAX_GAMES, NOTE_MAX_LENGTH, GameList, ListItem
from .colaboradores import collaborators_context
from .comun import _editable_list, _own_list
from .listas_del_juego import picker_context

# Juegos que salen al buscar en el editor de una lista.
SEARCH_RESULTS = 8


def editor_context(game_list):
    """Variables de los juegos del editor (lists/_editor_items.html): la lista, sus juegos en orden (con quién añadió
    cada uno), si es una tier list sus niveles (tiers) y el tablero en el que se arrastran sus portadas (tier_rows,
    siempre con la fila «Sin clasificar»), y los máximos.

    Pantallas: Editar lista (los juegos del editor), Ficha del juego (al añadir desde «Añadir a una lista»),
    Página de la lista.
    """
    items = list(game_list.items.select_related("game", "added_by__profile"))
    return {
        "game_list": game_list,
        "items": items,
        "tiers": game_list.tier_list if game_list.is_tiered else [],
        "tier_rows": game_list.tier_rows(items, keep_loose=True) if game_list.is_tiered else [],
        "max_games": LIST_MAX_GAMES,
        "note_max": NOTE_MAX_LENGTH,
    }


def board_context(game_list, viewer):
    """Variables del tablero de la página de una tier list (lists/_tier_board.html) para quien puede editarla, que es
    lo que se le devuelve al soltar una portada en él: sus juegos por niveles, con la fila «Sin clasificar» aunque esté
    vacía, y el estado de cada uno en la biblioteca de viewer (viewer_status), como en detail.

    Pantallas: Página de la lista (tablero de la tier list al soltar una portada).
    """
    items = list(game_list.items.select_related("game"))
    entries = LibraryEntry.objects.filter(user=viewer, game__in=[item.game_id for item in items])
    statuses = {entry.game_id: entry.get_status_display() for entry in entries}
    for item in items:
        item.viewer_status = statuses.get(item.game_id, "")
    return {"game_list": game_list, "tier_rows": game_list.tier_rows(items, keep_loose=True), "editable": True, "where": "tablero"}


def search_context(game_list, query):
    """Variables del buscador del editor: la búsqueda (search_query; no query, que es la del buscador de la cabecera)
    y, si la hay, los SEARCH_RESULTS juegos más populares que la cumplen (results, como el buscador del catálogo), cada
    uno marcado si ya está en la lista (in_list).

    Pantallas: Editar lista (buscador de juegos para añadir).
    """
    results = []
    if query:
        # Sin ediciones de otro juego: su ficha es la del juego, que es el que se añade.
        results = list(Game.objects.filter(search_filter(query), parent__isnull=True)[:SEARCH_RESULTS])
        listed = set(game_list.items.filter(game__in=results).values_list("game_id", flat=True))
        for game in results:
            game.in_list = game.pk in listed
    return {"search_query": query, "results": results}


@login_required
def edit(request, list_id):
    """Editor de una lista: sus datos (título, descripción, si la ven los demás, su tipo y sus niveles), que guarda su
    dueño con el formulario; sus juegos, que se buscan (?q=, ver search), se añaden, se ordenan, se ponen en un nivel,
    se comentan y se quitan uno a uno (add, move, tier, note y remove); y quién puede editarla (invite y
    collaborator_remove). Quien colabora solo ve los juegos (y puede irse, leave); a los demás les da 404.

    El formulario trabaja con una copia de la lista para que, si no es válido, la página siga con lo guardado.

    Pantallas: Editar lista.
    """
    game_list = _editable_list(request, list_id)
    is_owner = game_list.user == request.user
    form = GameListForm(request.POST or None, instance=GameList.objects.get(pk=game_list.pk)) if is_owner else None
    if request.method == "POST":
        if not is_owner:
            return HttpResponseForbidden("Solo su dueño puede cambiar los datos de la lista.")
        if form.is_valid():
            saved = form.save()
            messages.success(request, f"{saved.noun.capitalize()} guardada.")
            return redirect(game_list)
        messages.error(request, "No se ha guardado: revisa los campos marcados en rojo.")
    return render(request, "lists/edit.html", {
        "form": form,
        "is_owner": is_owner,
        **editor_context(game_list),
        **search_context(game_list, request.GET.get("q", "").strip()),
        **(collaborators_context(game_list, request.user) if is_owner else {}),
    })


@require_POST
@login_required
def delete(request, list_id):
    """Borra una lista del usuario (los juegos siguen en su biblioteca y en el catálogo) y vuelve a sus listas, o a sus
    tier lists si lo era.

    Pantallas: Editar lista (botón «Borrar»), Perfil (pestañas Listas y Tier lists).
    """
    game_list = _own_list(request, list_id)
    title, noun, tab = game_list.title, game_list.noun, game_list.profile_tab
    game_list.delete()
    messages.success(request, f"{noun.capitalize()} «{title}» borrada.")
    return redirect(f"{reverse('profile', args=[request.user.username])}?vista={tab}")


@login_required
def search(request, list_id):
    """Busca en el catálogo juegos para añadir a una lista que el usuario puede editar (?q=, ver search_context).

    Con HTMX devuelve solo los resultados; sin HTMX lleva al editor con la búsqueda hecha.

    Pantallas: Editar lista (resultados del buscador).
    """
    game_list = _editable_list(request, list_id)
    query = request.GET.get("q", "").strip()
    if not request.headers.get("HX-Request"):
        return redirect(f"{reverse('lists:edit', args=[game_list.pk])}?{urlencode({'q': query})}#juegos")
    return render(request, "lists/_search_results.html", {"game_list": game_list, **search_context(game_list, query)})


@require_POST
@login_required
def add(request, list_id, game_id):
    """Añade un juego al final de una lista que el usuario puede editar (si ya estaba, no hace nada); caben LIST_MAX_GAMES.

    Se apunta quién lo añade y su actividad (social.activity.list_games_added), y responde según desde dónde se pulsa
    (ver _item_response).

    Pantallas: Editar lista (los juegos: añadir un juego), Ficha del juego (desplegable «Añadir a una lista»),
    Página de la lista (tablero de las tier lists).
    """
    game_list = _editable_list(request, list_id)
    game = get_object_or_404(Game, pk=game_id)
    error = ""
    if not game_list.items.filter(game=game).exists() and game_list.items.count() >= LIST_MAX_GAMES:
        error = f"Caben {LIST_MAX_GAMES} juegos por lista: quita alguno para añadir más."
    elif game_list.add(game, request.user):
        activity.list_games_added(game_list, request.user)
    return _item_response(request, game_list, game, error)


@require_POST
@login_required
def remove(request, list_id, game_id):
    """Quita un juego de una lista que el usuario puede editar (si no estaba, no hace nada) y responde como add. Si lo
    acababa de añadir él, también se descuenta de su actividad (social.activity.list_game_removed).

    Pantallas: Editar lista (los juegos: quitar un juego), Ficha del juego (desplegable «Añadir a una lista»),
    Página de la lista (tablero de las tier lists).
    """
    game_list = _editable_list(request, list_id)
    game = get_object_or_404(Game, pk=game_id)
    if game_list.remove(game):
        activity.list_game_removed(game_list, request.user)
    return _item_response(request, game_list, game)


@require_POST
@login_required
def move(request, list_id, game_id):
    """Cambia de puesto un juego de una lista que el usuario puede editar: uno arriba o abajo (a=arriba o a=abajo) o al
    puesto que se escriba (puesto=, desde 1; si se pasa, al principio o al final). Responde como add.

    Da 404 si el juego no está en la lista y 400 si el puesto no es un número.

    Pantallas: Editar lista (subir, bajar o arrastrar un juego), Página de la lista.
    """
    game_list = _editable_list(request, list_id)
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

    Lo pueden escribir su dueño y quien colabora. Con HTMX devuelve solo su formulario, marcado como guardado o con el
    error, para no rehacer los demás juegos del editor; sin HTMX vuelve al editor, a ese juego.

    Pantallas: Editar lista (comentario de cada juego), Página de la lista (los comentarios de los juegos).
    """
    game_list = _editable_list(request, list_id)
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


@require_POST
@login_required
def tier(request, list_id, game_id):
    """Pone un juego de una tier list en uno de sus niveles (nivel=, su id; vacío lo deja sin clasificar). Con orden=
    (los ids de los juegos de esa fila separados por comas, como han quedado al soltarlo en el tablero) además lo deja
    en su sitio dentro del nivel (GameList.place). Lo pueden hacer su dueño y quien colabora. Responde como add; 404 si
    el juego no está en la lista y 400 si el nivel no existe.

    Pantallas: Página de la lista (arrastrar por el tablero), Editar lista (nivel de cada juego).
    """
    game_list = _editable_list(request, list_id)
    item = get_object_or_404(ListItem.objects.select_related("game"), game_list=game_list, game_id=game_id)
    raw = request.POST.get("nivel", "")
    if raw and not (raw.isdigit() and int(raw) in {level["id"] for level in game_list.tiers}):
        return HttpResponseBadRequest("Nivel no válido.")
    level = int(raw) if raw else None
    if "orden" in request.POST:
        row = [int(part) for part in request.POST["orden"].split(",") if part.strip().isdigit()]
        game_list.place(item.game, level, row)
    elif item.tier != level:
        item.tier = level
        item.save(update_fields=["tier"])
        game_list.touch()
    return _item_response(request, game_list, item.game)


def _item_response(request, game_list, game, error=""):
    """Respuesta de añadir, quitar, mover o poner en un nivel un juego de una lista, según desde dónde se pulsa (desde=).

    Con HTMX: desde la ficha («ficha»), el desplegable «Añadir a una lista» abierto; desde el buscador del editor
    («buscador»), los juegos del editor y, fuera de su sitio, el resultado ya marcado como añadido; desde el tablero
    de la página de la lista («tablero»), el tablero; desde el editor, sus juegos. Sin HTMX vuelve a next (si es de
    esta web) o al editor, avisando del error si lo hay.

    Pantallas: Editar lista (los juegos: lo que cambia tras añadir, quitar, mover o poner en un nivel),
    Ficha del juego (desplegable «Añadir a una lista»), Página de la lista (tablero de las tier lists).
    """
    where = request.POST.get("desde", "")
    if request.headers.get("HX-Request"):
        if where == "ficha":
            return render(request, "lists/_picker.html", {
                "game": game, **picker_context(request.user, game), "open": True, "picker_error": error,
            })
        if where == "tablero":
            return render(request, "lists/_tier_board.html", board_context(game_list, request.user))
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
