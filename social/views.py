from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from games.models import Game
from library.models import Review
from library.views import mark_liked

from .models import Activity, Follow

User = get_user_model()
# Personas por página en las listas de seguidores y seguidos.
PEOPLE_PER_PAGE = 30
# Actividades por tanda en la pestaña Actividad del perfil; «Cargar más» trae la siguiente.
ACTIVITY_PAGE = 20
# Origen de los cursores de «Cargar más» (ver _cursor).
EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)


def follow_context(viewer, user):
    """Variables del botón de seguir y de los contadores de user para quien mira (viewer).

    Cuántos seguidores tiene user (follower_count) y a cuántos sigue (following_count), si viewer ya sigue a
    user (is_following) y si user sigue a viewer (follows_you). Las dos últimas son falsas sin sesión y en el
    propio perfil.
    """
    pairs = set()
    if viewer.is_authenticated and viewer != user:
        pairs = set(
            Follow.objects.filter(Q(follower=viewer, followed=user) | Q(follower=user, followed=viewer))
            .values_list("follower_id", flat=True)
        )
    return {
        "follower_count": user.followers.count(),
        "following_count": user.following.count(),
        "is_following": viewer.pk in pairs,
        "follows_you": user.pk in pairs,
    }


@require_POST
@login_required
def follow(request, username):
    """Sigue a username (seguir=1) o deja de seguirlo (seguir=0).

    Seguir a quien ya se sigue, o dejar a quien no se sigue, no hace nada: pulsar dos veces no deshace lo hecho.
    Seguirse a uno mismo da 400. Un perfil privado ajeno da 404, como su página, salvo para dejar de seguirlo
    (pudo hacerse privado después). Al seguir se avisa en la campana (Follow.objects.follow). Con HTMX
    devuelve el botón nuevo, del tamaño de donde estaba (desde: «perfil» o «lista»), y en el perfil también
    el número de seguidores de la cabecera; sin HTMX vuelve a la página de «next» o al perfil.
    """
    target = get_object_or_404(User.objects.select_related("profile"), username=username)
    if target == request.user:
        return HttpResponseBadRequest("No puedes seguirte a ti mismo.")
    if request.POST.get("seguir") == "0":
        Follow.objects.unfollow(request.user, target)
    else:
        if not target.profile.visible_to(request.user):
            raise Http404
        Follow.objects.follow(request.user, target)
    back = request.POST.get("next", "")
    if request.headers.get("HX-Request"):
        where = request.POST.get("desde", "")
        return render(request, "social/_follow_button.html", {
            "target": target,
            **follow_context(request.user, target),
            "where": where,
            "next": back,
            "oob": where == "perfil",
        })
    if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        return redirect(back)
    return redirect(target.profile)


def followers(request, username):
    """Seguidores de username, del más reciente al más antiguo (ver _people)."""
    return _people(request, username, "seguidores")


def following(request, username):
    """Gente a la que sigue username, de la más reciente a la más antigua (ver _people)."""
    return _people(request, username, "siguiendo")


def _people(request, username, view):
    """Página con los seguidores de username (view «seguidores») o con la gente a la que sigue («siguiendo»).

    Va de PEOPLE_PER_PAGE en PEOPLE_PER_PAGE (?page=). Un perfil privado da 404 a cualquiera que no sea su
    dueño, como su página. En las listas salen también los perfiles privados (a quién sigue cada uno se ve,
    como lo que comenta), pero sin enlace a su página (profile_visible) ni botón para seguirlos, solo para
    dejar de seguirlos. En cada persona se apunta si quien mira la sigue (viewer_follows) y si ella sigue a
    quien mira (follows_viewer).
    """
    owner = get_object_or_404(User.objects.select_related("profile"), username=username)
    viewer = request.user
    if not owner.profile.visible_to(viewer):
        raise Http404
    side = "follower" if view == "seguidores" else "followed"
    links = (owner.followers if view == "seguidores" else owner.following).select_related(
        f"{side}__profile", f"{side}__psn_account", f"{side}__xbox_account"
    ).prefetch_related(f"{side}__steam_accounts")
    page = Paginator(links, PEOPLE_PER_PAGE).get_page(request.GET.get("page"))
    people = [getattr(link, side) for link in page.object_list]
    followed_by_viewer, following_viewer = set(), set()
    if viewer.is_authenticated and people:
        followed_by_viewer = set(
            Follow.objects.filter(follower=viewer, followed__in=people).values_list("followed_id", flat=True)
        )
        following_viewer = set(
            Follow.objects.filter(followed=viewer, follower__in=people).values_list("follower_id", flat=True)
        )
    for person in people:
        person.profile_visible = person.profile.visible_to(viewer)
        person.viewer_follows = person.pk in followed_by_viewer
        person.follows_viewer = person.pk in following_viewer
    return render(request, "social/people.html", {
        "owner": owner,
        "view": view,
        "people": people,
        "page": page,
        "follower_count": owner.followers.count(),
        "following_count": owner.following.count(),
        "is_owner": viewer == owner,
    })


def activity_page(user, viewer, cursor=""):
    """Una tanda de la actividad de user tal como la ve viewer, de lo más reciente a lo más antiguo.

    Devuelve (actividades, cursor de la tanda siguiente o "" si no hay más). cursor dice desde dónde seguir:
    lo da la tanda anterior, y vacío (o roto) es desde el principio. Cada actividad trae su reseña solo si
    viewer puede leerla (si su autor la volvió a dejar en borrador, no), con sus «me gusta» y comentarios
    contados, y las importaciones traen sus juegos (import_games). Las que se quedan sin nada que enseñar no
    salen. No mira si viewer puede ver el perfil de user: eso lo hace quien la llama.
    """
    activities = Activity.objects.filter(user=user).select_related("game")
    start = _parse_cursor(cursor)
    if start:
        moment, pk = start
        activities = activities.filter(Q(updated_at__lt=moment) | Q(updated_at=moment, pk__lt=pk))
    activities = list(
        activities.prefetch_related(Prefetch("review", queryset=Review.objects.visible_to(viewer)))[: ACTIVITY_PAGE + 1]
    )
    next_cursor = _cursor(activities[ACTIVITY_PAGE - 1]) if len(activities) > ACTIVITY_PAGE else ""
    activities = activities[:ACTIVITY_PAGE]
    mark_liked([activity.review for activity in activities if activity.review], viewer)
    imported = [activity for activity in activities if activity.kind == Activity.Kind.IMPORT]
    games = Game.objects.in_bulk({pk for activity in imported for pk in activity.data.get("games", [])})
    for activity in imported:
        activity.import_games = [games[pk] for pk in activity.data.get("games", []) if pk in games]
    shown = [
        activity for activity in activities
        if activity.import_count or activity.status or activity.rating or activity.review
    ]
    return shown, next_cursor


def _cursor(activity):
    """Cursor de «Cargar más» para seguir después de activity: «microsegundos desde 1970 (UTC)-id».

    Se cuenta en microsegundos enteros, sin decimales, para que la fecha vuelva exacta al leerlo.
    """
    return f"{(activity.updated_at - EPOCH) // timedelta(microseconds=1)}-{activity.pk}"


def _parse_cursor(cursor):
    """Lo contrario de _cursor: (fecha, id), o None si cursor está vacío o no tiene esa forma."""
    try:
        micros, pk = (int(part) for part in cursor.split("-"))
        return EPOCH + timedelta(microseconds=micros), pk
    except (ValueError, OverflowError):
        return None


def activity(request, username):
    """Tanda siguiente de la actividad de username (?antes=, el cursor de la anterior) para «Cargar más».

    Con HTMX devuelve solo las actividades nuevas y el botón para la siguiente; sin HTMX lleva a la pestaña
    Actividad del perfil desde ese punto. Un perfil privado da 404 a cualquiera que no sea su dueño.
    """
    owner = get_object_or_404(User.objects.select_related("profile"), username=username)
    if not owner.profile.visible_to(request.user):
        raise Http404
    cursor = request.GET.get("antes", "")
    if not request.headers.get("HX-Request"):
        return redirect(f"{reverse('profile', args=[username])}?{urlencode({'vista': 'actividad', 'antes': cursor})}")
    activities, next_cursor = activity_page(owner, request.user, cursor)
    return render(request, "social/_activities.html", {
        "owner": owner, "activities": activities, "next_cursor": next_cursor,
    })
