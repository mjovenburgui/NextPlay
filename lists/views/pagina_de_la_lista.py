"""Vistas de la Página de la lista (u/<usuario>/listas/<id>/): la lista con sus juegos, cuánto llevas jugado y lo que
cuesta lo que te falta, en las tier lists abiertas la de la comunidad, y sus botones: «me gusta», comentarios,
denuncias, «Lo quiero» de lo que te falta y «Haz tu versión».
"""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts import notices
from accounts.models import Notification
from games.models import Game, format_price
from library.models import LibraryEntry, WishlistItem
from library.views import _report, comment_thread
from social import activity

from ..community import MIN_VOTERS, community_board
from ..forms import ListCommentForm
from ..models import GameList, ListComment, ListLike
from .tarjetas import PLAYED_STATUSES, mark_liked_lists

# Cómo se ven los juegos en la página de una lista (?vista=): portadas en cuadrícula o filas con el comentario de cada uno;
# en las tier lists, por niveles en vez de en portadas. La primera es la de por defecto si ningún juego tiene comentario.
VIEWS = {"portadas": "Portadas", "detalle": "Con comentarios"}
TIER_VIEWS = {"niveles": "Por niveles", "detalle": "Con comentarios"}


def detail(request, username, list_id):
    """Página de una lista: su mosaico, el título, el autor (y quien colabora), la descripción y los juegos, en
    portadas (en las tier lists, por niveles) o con el comentario de cada uno (?vista=; sin ella, con los comentarios
    si alguno tiene), y debajo sus comentarios. En el tablero de una tier list, quien puede editarla arrastra las
    portadas de un nivel a otro (lists/_tier_board.html y tier).

    En las tier lists abiertas a la comunidad enseña también cuánta gente la ha hecho, el botón «Haz tu versión»
    (make_version) o la tuya si ya la tienes, y la tier list de la comunidad (lists.community, ?vista=comunidad), que es
    la que sale primero en cuanto la ha hecho bastante gente. En las versiones dice de cuál son.

    La ve quien puede verla (GameList.visible_to); si no, 404. A quien está invitado a editarla le pregunta si acepta.
    Con sesión, enseña cuánto ha jugado quien mira de la lista, el estado de cada juego en su biblioteca y, de lo que
    no tiene, cuánto cuesta (list_progress). Los demás pueden darle «me gusta» y denunciarla (con ?denunciar, el
    formulario llega abierto).

    Pantallas: Página de la lista.
    """
    game_list = get_object_or_404(GameList.objects.with_preview(), pk=list_id, user__username=username)
    viewer = request.user
    if not game_list.visible_to(viewer):
        raise Http404
    items = list(game_list.items.select_related("game", "added_by__profile").prefetch_related("game__prices"))
    progress = list_progress([item.game for item in items], viewer) if viewer.is_authenticated else None
    # En las tier lists, el nivel de cada juego (con su color) para la vista con comentarios.
    tiers = {level["id"]: level for level in game_list.tier_list} if game_list.is_tiered else {}
    for item in items:
        item.viewer_status = progress["statuses"].get(item.game_id, "") if progress else ""
        item.viewer_status_key = progress["status_keys"].get(item.game_id, "") if progress else ""
        item.tier_info = tiers.get(item.tier)
    is_owner = viewer == game_list.user
    community = community_board(game_list, items) if game_list.accepts_versions else None
    views = TIER_VIEWS if game_list.is_tiered else VIEWS
    if community:
        # En las abiertas, la de la comunidad va primero y el tablero de su autor es uno más.
        views = {
            "comunidad": "La de la comunidad",
            "niveles": "La tuya" if is_owner else f"La de {game_list.user.profile.name}",
            "detalle": TIER_VIEWS["detalle"],
        }
    view = request.GET.get("vista", "")
    if view not in views:
        if community:
            view = "niveles" if community["missing"] else "comunidad"
        else:
            view = "detalle" if any(item.note for item in items) else next(iter(views))
    collaborators = list(game_list.collaborators.select_related("user__profile", "user__psn_account", "user__xbox_account"))
    mine = next((collaborator for collaborator in collaborators if collaborator.user_id == viewer.pk), None)
    can_edit = is_owner or bool(mine and mine.accepted_at)
    mark_liked_lists([game_list], viewer)
    return render(request, "lists/detail.html", {
        "game_list": game_list,
        "owner": game_list.user,
        "items": items,
        "is_owner": is_owner,
        # Quien colabora (ya ha aceptado), para decirlo junto al autor, y si quien mira está invitado y aún no ha aceptado.
        "collaborators": [collaborator for collaborator in collaborators if collaborator.accepted_at],
        "invitation": mine if mine and not mine.accepted_at else None,
        "can_edit": can_edit,
        "progress": progress,
        "view": view,
        "views": views.items(),
        # Quien puede editarla arrastra las portadas del tablero: necesita la fila «Sin clasificar» aunque esté vacía.
        "tier_rows": game_list.tier_rows(items, keep_loose=can_edit) if game_list.is_tiered else [],
        "community": community,
        "min_voters": MIN_VOTERS,
        # De las abiertas, la versión de quien mira si ya la ha hecho; de las versiones, de cuál son (no «parent»: ese
        # nombre ya lo usan los comentarios).
        "my_version": (
            game_list.versions.filter(user=viewer).first() if community and viewer.is_authenticated and not is_owner else None
        ),
        "source_list": game_list.public_parent,
        "reported": viewer.is_authenticated and not is_owner and game_list.reports.filter(reporter=viewer).exists(),
        "open_report": "denunciar" in request.GET,
        **_list_comments(game_list, viewer),
        "comment_form": ListCommentForm(),
    })


def _readable_list(request, list_id):
    """Lista list_id con su dueño cargado, si el usuario puede verla; si no, 404.

    Pantallas: Página de la lista, Perfil, Inicio (404 al pulsar algo de una lista que no se puede ver).
    """
    game_list = get_object_or_404(GameList.objects.select_related("user__profile"), pk=list_id)
    if not game_list.visible_to(request.user):
        raise Http404
    return game_list


@require_POST
@login_required
def make_version(request, list_id):
    """«Haz tu versión» de una tier list abierta a la comunidad: crea la del usuario (GameList.make_version), la apunta
    en su actividad, se lo cuenta a quien la abrió (una vez por persona: accounts.notices) y lleva a su página, para
    colocar los juegos en su tablero; si ya la tiene, lleva a ella. Las que no están abiertas dan 404, y la propia 400:
    su tablero ya cuenta en la de la comunidad.

    Pantallas: Página de la lista (botón «Haz tu versión»), Perfil (pestaña Tier lists), Tier lists, Inicio (feed:
    «hizo su versión de…»), Avisos (a quien la abrió).
    """
    game_list = _readable_list(request, list_id)
    if not game_list.accepts_versions:
        raise Http404
    if game_list.user_id == request.user.pk:
        return HttpResponseBadRequest("Tu tablero ya cuenta en la tier list de la comunidad.")
    version, created = game_list.make_version(request.user)
    if created:
        activity.list_created(version)
        notices.notify(
            game_list.user, Notification.Kind.LIST_VERSION,
            f"{request.user.profile.name} ha hecho su versión de tu tier list «{game_list.title}».",
            version.get_absolute_url(), actor=request.user,
        )
        messages.success(
            request, "Ya tienes tu versión: arrastra los juegos a su nivel. Cuenta en la de la comunidad en cuanto colocas alguno.",
        )
    return redirect(version)


@require_POST
@login_required
def like(request, list_id):
    """Da o quita el «me gusta» del usuario a una lista de otro, avisando a su dueño la primera vez.

    Con HTMX devuelve el botón con el contador nuevo; si no, vuelve a la lista. Las propias no se pueden marcar (400).

    Pantallas: Página de la lista, Perfil, Inicio (botón «me gusta» de las listas), Avisos (aviso a su dueño).
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
    """Avisa al dueño de game_list de que a actor le ha gustado, solo la primera vez aunque lo quite y lo vuelva a dar
    (accounts.notices).

    Pantallas: Avisos (a quien le gusta tu lista), todas (el número de la campana).
    """
    notices.notify(
        game_list.user, Notification.Kind.LIST_LIKE,
        f"A {actor.profile.name} le ha gustado tu {game_list.noun} «{game_list.title}».",
        game_list.get_absolute_url(), actor=actor,
    )


def _list_comments(game_list, viewer):
    """Variables de library/_comments.html para los comentarios de game_list (ver library.views.comment_thread).

    Pantallas: Página de la lista (comentarios).
    """
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

    Pantallas: Página de la lista (comentarios), Avisos (aviso a su dueño).
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
    """Avisa al dueño de game_list de que actor la ha comentado, salvo si ya tiene otro suyo sin leer
    (accounts.notices).

    Pantallas: Avisos (comentarios en tus listas), todas (el número de la campana).
    """
    notices.notify(
        game_list.user, Notification.Kind.LIST_COMMENT,
        f"{actor.profile.name} ha comentado tu {game_list.noun} «{game_list.title}».",
        f"{game_list.get_absolute_url()}#comentarios", actor=actor,
    )


@require_POST
@login_required
def comment_delete(request, comment_id):
    """Borra un comentario de una lista: solo quien lo escribió o el dueño de la lista (si no, 403).

    Pantallas: Página de la lista (botón «Borrar» de un comentario).
    """
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
    """Denuncia un comentario de otro en una lista; los propios no (400) y los de listas que no se pueden ver dan 404.

    Pantallas: Página de la lista (denunciar un comentario), Panel de administración (denuncias).
    """
    target = get_object_or_404(ListComment.objects.select_related("user"), pk=comment_id)
    _readable_list(request, target.game_list_id)
    if target.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propio comentario.")
    return _report(request, target, "comentario", None, target.get_absolute_url())


@require_POST
@login_required
def report(request, list_id):
    """Denuncia una lista de otro para que la revise un administrador (library.views._report); las propias no (400).

    Pantallas: Página de la lista (botón «Denunciar»), Panel de administración (denuncias).
    """
    game_list = _readable_list(request, list_id)
    if game_list.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propia lista.")
    return _report(request, game_list, "lista", None, game_list.get_absolute_url())


def list_progress(games, viewer):
    """Cómo va viewer con los juegos games de una lista (con sus precios ya cargados).

    Cuántos son (total), cuántos tiene en su biblioteca (owned), cuántos ha jugado (played: cualquier estado
    menos Pendiente; percent, en porcentaje), terminado (completed) y tiene en Pendiente (backlog), las horas que
    lleva en ellos (hours: las de Steam, PlayStation y Xbox), el estado de cada uno (statuses: id del juego →
    nombre del estado; status_keys: id del juego → valor del estado, para su color) y lo que le falta (ver _missing).

    Pantallas: Página de la lista (cuánto llevas jugado y lo que cuesta lo que te falta).
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
        "status_keys": {game_id: entry.status for game_id, entry in entries.items()},
        **_missing([game for game in games if game.pk not in entries], viewer),
    }


def _missing(games, viewer):
    """Lo que le falta a viewer de una lista: games, los juegos que no están en su biblioteca, y lo que cuestan.

    Cuántos son (missing); cuántos tienen precio en sus tiendas (priced) y lo que suman hoy (today) y si espera a
    que cada uno baje a su mínimo histórico de PC (at_lows; el de hoy si no lo tiene o si hoy está más barato),
    con saving si así sale más barato; cuántos no tienen precio (unpriced: los de consolas sin tienda); y cuántos
    están ya en su lista de deseos (wished) y cuántos no (to_wish), para el botón de pasarlos a «Lo quiero».

    Pantallas: Página de la lista (lo que te falta y lo que cuesta).
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
    olvida, para que la importación de Steam no lo vuelva a quitar.

    Pantallas: Página de la lista (botón «Lo quiero» de lo que te falta), Lista de deseos.
    """
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
