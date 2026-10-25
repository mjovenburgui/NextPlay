from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts import images
from accounts.models import Notification, Profile
from games.models import Game
from library.models import LibraryEntry, Report, Review
from library.views import _report, comment_thread, mark_liked

from .activity import post_published
from .forms import PostCommentForm, PostForm
from .models import POST_MAX_IMAGES, Activity, Follow, Post, PostComment, PostLike

User = get_user_model()
# Personas por página en las listas de seguidores y seguidos.
PEOPLE_PER_PAGE = 30
# Actividades por tanda en la pestaña Actividad del perfil y en el feed de la portada; «Cargar más» trae la siguiente.
ACTIVITY_PAGE = 20
# Pestañas del feed de la portada: valor de ?ver= → nombre. La primera es la de por defecto.
FEED_TABS = {"siguiendo": "Siguiendo", "descubrir": "Descubrir"}
# Gente que se sugiere seguir a quien no sigue a nadie, y cuántos días cuenta la actividad reciente para elegirla.
SUGGESTIONS = 5
SUGGESTION_DAYS = 30
# Juegos que la caja de publicar ofrece para elegir de cuál va la publicación: los últimos que estás jugando.
COMPOSER_GAMES = 6
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


def _batch(activities, viewer, cursor):
    """Una tanda de ACTIVITY_PAGE actividades de activities (ya filtradas) tal como las ve viewer, de lo más reciente a lo más antiguo.

    Devuelve (actividades, cursor de la tanda siguiente o "" si no hay más). cursor dice desde dónde seguir:
    lo da la tanda anterior, y vacío (o roto) es desde el principio. Cada actividad trae su reseña solo si
    viewer puede leerla (si su autor la volvió a dejar en borrador, no), con sus imágenes y sus «me gusta» y
    comentarios contados, y las importaciones traen sus juegos (import_games). Las que se quedan sin nada que
    enseñar no salen. No mira de quién son: eso lo decide quien la llama.
    """
    start = _parse_cursor(cursor)
    if start:
        moment, pk = start
        activities = activities.filter(Q(updated_at__lt=moment) | Q(updated_at=moment, pk__lt=pk))
    activities = list(
        activities.select_related("game")
        .prefetch_related(
            Prefetch("review", queryset=Review.objects.visible_to(viewer)),
            Prefetch("post", queryset=_posts()),
        )[: ACTIVITY_PAGE + 1]
    )
    next_cursor = _cursor(activities[ACTIVITY_PAGE - 1]) if len(activities) > ACTIVITY_PAGE else ""
    activities = activities[:ACTIVITY_PAGE]
    mark_liked([activity.review for activity in activities if activity.review], viewer)
    mark_liked_posts([activity.post for activity in activities if activity.post], viewer)
    imported = [activity for activity in activities if activity.kind == Activity.Kind.IMPORT]
    games = Game.objects.in_bulk({pk for activity in imported for pk in activity.data.get("games", [])})
    for activity in imported:
        activity.import_games = [games[pk] for pk in activity.data.get("games", []) if pk in games]
    return [activity for activity in activities if activity.has_content], next_cursor


def activity_context(user, viewer, cursor=""):
    """Variables de la pestaña Actividad del perfil de user tal como la ve viewer, desde cursor (ver _batch).

    La tanda que toca (activities), el cursor de la siguiente (next_cursor) y, si quedan más, las direcciones de
    «Cargar más»: la que pide HTMX (more_url) y la de la pestaña desde ahí, para quien no tenga JavaScript
    (more_href). No mira si viewer puede ver el perfil: eso lo hace quien la llama.
    """
    activities, next_cursor = _batch(Activity.objects.filter(user=user), viewer, cursor)
    context = {"activities": activities, "next_cursor": next_cursor}
    if next_cursor:
        context["more_url"] = f"{reverse('social:activity', args=[user.username])}?{urlencode({'antes': next_cursor})}"
        context["more_href"] = (
            f"{reverse('profile', args=[user.username])}?{urlencode({'vista': 'actividad', 'antes': next_cursor})}"
        )
    return context


def feed_context(viewer, tab="", cursor=""):
    """Variables del feed de la portada para viewer en la pestaña tab, desde cursor (ver _batch).

    «siguiendo» (la de por defecto) es lo de viewer y la gente que sigue; «descubrir», lo de toda la
    comunidad; sin sesión solo hay «descubrir». En las dos sale solo lo de perfiles que viewer puede ver
    (Profile.visible_filter): si alguien a quien sigue se hace privado, deja de ver lo suyo. Además de la
    tanda y de «Cargar más» (como activity_context), lleva la pestaña (feed_tab) y las que hay (feed_tabs); si
    viewer no sigue a nadie (follows_nobody), en la primera tanda va también gente a la que seguir (suggestions).
    """
    if tab not in FEED_TABS:
        tab = "siguiendo"
    if not viewer.is_authenticated:
        tab = "descubrir"
    activities = (
        Activity.objects.filter(Profile.visible_filter(viewer))
        .select_related("user__profile", "user__psn_account", "user__xbox_account")
        .prefetch_related("user__steam_accounts")
    )
    following = None
    if tab == "siguiendo":
        following = list(viewer.following.values_list("followed_id", flat=True))
        activities = activities.filter(user_id__in=[viewer.pk, *following])
    items, next_cursor = _batch(activities, viewer, cursor)
    context = {
        "activities": items,
        "next_cursor": next_cursor,
        "feed_tab": tab,
        "feed_tabs": FEED_TABS.items(),
        "follows_nobody": following == [],
        "show_user": True,
    }
    if next_cursor:
        query = urlencode({"ver": tab, "antes": next_cursor})
        context["more_url"] = f"{reverse('social:feed')}?{query}"
        context["more_href"] = f"{reverse('home')}?{query}"
    if following == [] and not cursor:
        context["suggestions"] = suggested_people(viewer)
    return context


def suggested_people(viewer, limit=SUGGESTIONS):
    """Gente a la que viewer podría seguir: perfiles públicos a los que aún no sigue, primero los que tienen más
    juegos en común con viewer y después los que más han hecho en los últimos SUGGESTION_DAYS días.

    Solo salen quienes tienen algún juego en común o actividad reciente. Cada uno trae cuántos juegos tiene en
    común (shared) y si sigue a viewer (follows_viewer), y lo que necesitan su foto y el botón de seguir.
    Se cuenta con subconsultas para no cruzar la biblioteca con la actividad de cada uno.
    """
    since = timezone.now() - timedelta(days=SUGGESTION_DAYS)
    mine = LibraryEntry.objects.filter(user=viewer).values("game_id")
    shared = (
        LibraryEntry.objects.filter(user=OuterRef("pk"), game_id__in=mine)
        .values("user").annotate(n=Count("pk")).values("n")
    )
    recent = (
        Activity.objects.filter(user=OuterRef("pk"), updated_at__gte=since)
        .values("user").annotate(n=Count("pk")).values("n")
    )
    people = list(
        User.objects.filter(Profile.visible_filter(viewer, path=""))
        .exclude(pk=viewer.pk)
        .exclude(pk__in=Follow.objects.filter(follower=viewer).values("followed_id"))
        .annotate(shared=Coalesce(Subquery(shared), 0), recent=Coalesce(Subquery(recent), 0))
        .filter(Q(shared__gt=0) | Q(recent__gt=0))
        .select_related("profile", "psn_account", "xbox_account")
        .prefetch_related("steam_accounts")
        .order_by("-shared", "-recent", "username")[:limit]
    )
    followers = set(
        Follow.objects.filter(followed=viewer, follower__in=people).values_list("follower_id", flat=True)
    )
    for person in people:
        person.profile_visible = True
        person.viewer_follows = False
        person.follows_viewer = person.pk in followers
    return people


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
    return render(request, "social/_activities.html", activity_context(owner, request.user, cursor))


def feed(request):
    """Tanda siguiente del feed de la portada (?ver=, la pestaña, y ?antes=, el cursor de la anterior) para «Cargar más».

    Con HTMX devuelve solo las actividades nuevas y el botón para la siguiente; sin HTMX lleva a la portada
    desde ese punto.
    """
    tab, cursor = request.GET.get("ver", ""), request.GET.get("antes", "")
    if not request.headers.get("HX-Request"):
        return redirect(f"{reverse('home')}?{urlencode({'ver': tab, 'antes': cursor})}")
    return render(request, "social/_activities.html", feed_context(request.user, tab, cursor))


def _posts():
    """Publicaciones con lo que enseñan sus tarjetas ya cargado: autor (con su foto), juego, imágenes, «me gusta»
    (like_count) y comentarios (comment_count)."""
    return (
        Post.objects.select_related("user__profile", "user__psn_account", "user__xbox_account", "game")
        .prefetch_related("images", "user__steam_accounts")
        .annotate(like_count=Count("likes", distinct=True), comment_count=Count("comments", distinct=True))
    )


def mark_liked_posts(posts, user):
    """Apunta en cada publicación de posts (liked) si user le ha dado «me gusta»; devuelve la misma lista."""
    liked = set()
    if user.is_authenticated and posts:
        liked = set(PostLike.objects.filter(user=user, post__in=posts).values_list("post_id", flat=True))
    for post in posts:
        post.liked = post.pk in liked
    return posts


def composer_context(user, game=None, form=None):
    """Variables de la caja de publicar de user (social/_composer.html).

    El formulario (post_form: form o uno vacío), los juegos que ofrece para elegir de cuál va la publicación
    (composer_games: game, si se da, y los últimos COMPOSER_GAMES que está jugando), el elegido
    (composer_game, su id o None) y las medidas con las que el navegador reduce las imágenes muy grandes.
    """
    playing = [
        entry.game for entry in LibraryEntry.objects.filter(user=user, status=LibraryEntry.Status.PLAYING)
        .select_related("game").order_by("-updated_at")[:COMPOSER_GAMES]
    ]
    return {
        "post_form": form or PostForm(),
        "composer_games": ([game] if game else []) + [other for other in playing if other != game],
        "composer_game": game.pk if game else None,
        "post_image_kind": images.KINDS["post"],
        "post_max_images": POST_MAX_IMAGES,
    }


@login_required
def post_new(request):
    """Publica lo que escriba el usuario (POST) o enseña la página para hacerlo (GET).

    Con ?juego= (desde la ficha de un juego) la publicación ya va sobre ese juego. Al publicar se apunta en su
    actividad (social.activity.post_published) y vuelve a la portada, donde sale la primera de Siguiendo; si
    falta algo, enseña esta página con los errores y lo que había escrito.
    """
    form = PostForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        post_published(form.save(request.user))
        messages.success(request, "Publicado.")
        return redirect("home")
    if form.is_bound:
        game = form.cleaned_data.get("game")
    else:
        raw = request.GET.get("juego", "")
        game = Game.objects.filter(pk=raw).first() if raw.isdigit() else None
    return render(request, "social/post_form.html", composer_context(request.user, game, form if form.is_bound else None))


def _readable_post(request, post_id):
    """Publicación post_id con sus tarjetas cargadas, si el usuario puede verla; 404 si es de un perfil privado ajeno."""
    post = get_object_or_404(_posts(), pk=post_id)
    if not post.user.profile.visible_to(request.user):
        raise Http404
    return post


def post_detail(request, username, post_id):
    """Página de una publicación: su autor, el juego del que habla, el texto entero, sus imágenes, sus «me gusta»
    y sus comentarios.

    La ve quien puede ver el perfil de su autor (si no, 404). Su autor puede borrarla; los demás, denunciarla
    (con ?denunciar llega abierto el formulario).
    """
    post = _readable_post(request, post_id)
    if post.user.username != username:
        raise Http404
    mark_liked_posts([post], request.user)
    viewer = request.user
    is_author = viewer == post.user
    reported = viewer.is_authenticated and not is_author and Report.objects.filter(
        content_type=ContentType.objects.get_for_model(Post), object_id=post.pk, reporter=viewer
    ).exists()
    return render(request, "social/post_detail.html", {
        "post": post, "is_author": is_author, "reported": reported, "open_report": "denunciar" in request.GET,
        **_post_comments(post, viewer), "comment_form": PostCommentForm(),
    })


def _post_comments(post, viewer):
    """Variables de library/_comments.html para los comentarios de post (ver library.views.comment_thread)."""
    return comment_thread(
        post, post.comments.select_related("post"), viewer,
        comment_url=reverse("social:post_comment", args=[post.pk]),
        delete_url_name="social:post_comment_delete", report_url_name="social:post_comment_report",
        count_id=f"post-comment-count-{post.pk}",
    )


@require_POST
@login_required
def post_comment(request, post_id):
    """Publica un comentario en una publicación (avisando a su autor si no es él) y vuelve a sus comentarios.

    Con HTMX devuelve los comentarios ya con el nuevo (o el formulario con el error) y el contador de la página.
    """
    post = _readable_post(request, post_id)
    form = PostCommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.post, comment.user = post, request.user
        comment.save()
        if post.user_id != request.user.pk:
            _notify_post_comment(post, request.user)
        form = PostCommentForm()
    elif not request.headers.get("HX-Request"):
        messages.error(request, " ".join(form.errors.get("text", ["El comentario no es válido."])))
    if request.headers.get("HX-Request"):
        return render(request, "library/_comments.html", {
            "comment_form": form, "oob": True, **_post_comments(post, request.user),
        })
    return redirect(f"{post.get_absolute_url()}#comentarios")


def _notify_post_comment(post, actor):
    """Avisa al autor de post de que actor la ha comentado, salvo si ya tiene un aviso igual sin leer."""
    url = f"{post.get_absolute_url()}#comentarios"
    notices = Notification.objects.filter(
        user=post.user, kind=Notification.Kind.POST_COMMENT, actor=actor, url=url, read_at=None
    )
    if not notices.exists():
        Notification.objects.create(
            user=post.user, kind=Notification.Kind.POST_COMMENT, actor=actor, url=url, game=post.game,
            text=f"{actor.profile.name} ha comentado tu publicación."[:300],
        )


@require_POST
@login_required
def post_comment_delete(request, comment_id):
    """Borra un comentario de una publicación: solo quien lo escribió o el autor de la publicación (si no, 403)."""
    comment = get_object_or_404(PostComment.objects.select_related("post__user__profile"), pk=comment_id)
    if not comment.can_delete(request.user):
        return HttpResponseForbidden("No puedes borrar este comentario.")
    post = _readable_post(request, comment.post_id)
    comment.delete()
    if request.headers.get("HX-Request"):
        return render(request, "library/_comments.html", {
            "comment_form": PostCommentForm(), "oob": True, **_post_comments(post, request.user),
        })
    return redirect(f"{post.get_absolute_url()}#comentarios")


@require_POST
@login_required
def post_comment_report(request, comment_id):
    """Denuncia un comentario de otro en una publicación; los propios no (400) y los de publicaciones que no se pueden ver dan 404."""
    comment = get_object_or_404(PostComment.objects.select_related("user", "post__game"), pk=comment_id)
    _readable_post(request, comment.post_id)
    if comment.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propio comentario.")
    return _report(request, comment, "comentario", comment.post.game, comment.get_absolute_url())


@require_POST
@login_required
def post_delete(request, post_id):
    """Borra una publicación del usuario, con sus imágenes, sus «me gusta» y su actividad, y vuelve a la portada; las de otros dan 404."""
    get_object_or_404(Post, pk=post_id, user=request.user).delete()
    messages.success(request, "Publicación borrada.")
    return redirect("home")


@require_POST
@login_required
def post_like(request, post_id):
    """Da o quita el «me gusta» del usuario a una publicación de otro, avisando a su autor la primera vez.

    Con HTMX devuelve el botón con el contador nuevo; si no, vuelve a la página de la publicación. Las propias no
    se pueden marcar (400).
    """
    post = _readable_post(request, post_id)
    if post.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes darle «me gusta» a tu propia publicación.")
    like, created = PostLike.objects.get_or_create(user=request.user, post=post)
    if created:
        _notify_post_like(post, request.user)
    else:
        like.delete()
    if request.headers.get("HX-Request"):
        return render(request, "social/_post_like.html", {"post": post, "liked": created, "like_count": post.likes.count()})
    return redirect(post)


def _notify_post_like(post, actor):
    """Avisa al autor de post de que a actor le ha gustado, solo la primera vez aunque lo quite y lo vuelva a dar."""
    url = post.get_absolute_url()
    notices = Notification.objects.filter(user=post.user, kind=Notification.Kind.POST_LIKE, actor=actor, url=url)
    if not notices.exists():
        Notification.objects.create(
            user=post.user, kind=Notification.Kind.POST_LIKE, actor=actor, url=url, game=post.game,
            text=f"A {actor.profile.name} le ha gustado tu publicación."[:300],
        )


@require_POST
@login_required
def post_report(request, post_id):
    """Denuncia una publicación de otro para que la revise un administrador (library.views._report); las propias no (400)."""
    post = _readable_post(request, post_id)
    if post.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propia publicación.")
    return _report(request, post, "publicacion", post.game, post.get_absolute_url())
