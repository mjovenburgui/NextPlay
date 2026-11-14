"""La comunidad de cada juego: su pestaña Comunidad (Todo, Hilos, Preguntas y Con imágenes), su adelanto en la Ficha del
juego, la página de cada hilo o pregunta con sus respuestas, abrir uno nuevo y el «¿Cómo se consigue?» de «Tus logros».

Los hilos y las preguntas son publicaciones (models.Post) con título y de un juego, y sus respuestas, comentarios
(models.PostComment) que pueden contestar a otro, con su cita. Cada mensaje guarda cómo iba su autor con el juego al
escribirlo (models.PlaySnapshot), que sale a su lado, y a quien lee se le pliega (fold), hasta que lo abra:

- lo que su autor dice que destripa hasta un logro (Post.spoiler_achievement), si quien lee aún no lo tiene;
- lo que se escribió con AHEAD_HOURS horas o más de las que lleva quien lee, si no lo ha terminado: la misma regla que
  las reseñas de la ficha.

Lo propio nunca se pliega por esto, y lo que su autor marca como spoiler va tapado para todos, como en el resto de la web.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts import images, notices
from accounts.models import Achievement, Notification, Profile, UnlockedAchievement
from games.models import Game
from library.models import LibraryEntry, Report
from library.views.ficha_del_juego import AHEAD_HOURS
from lists.views.listas_del_juego import _lists_with

from .activity import post_published
from .forms import ReplyForm, ThreadForm
from .models import POST_MAX_IMAGES, Post, PostComment, PostImage
from .views import _posts, _readable_post, mark_liked_posts, mark_saved_posts, post_batch

# Pestañas de la comunidad (?ver=): valor → nombre. La primera es la de por defecto.
TABS = {"": "Todo", "hilos": "Hilos", "preguntas": "Preguntas", "imagenes": "Con imágenes"}
# Cosas por tanda en la comunidad; «Cargar más» trae la siguiente.
PAGE = 15
# Lo último de la comunidad que sale en la ficha del juego.
PREVIEW = 3
# Por qué se le pliega algo a quien lee: un logro que aún no tiene o las horas de más con que se escribió.
FOLD_ACHIEVEMENT = "logro"
FOLD_HOURS = "horas"


@dataclass(frozen=True)
class Reader:
    """Cómo va quien lee (user) con un juego, para saber qué se le pliega: su entrada de biblioteca (entry), sus horas
    importadas (hours, None si no se saben), si lo ha terminado (finished) y los ids de los logros que tiene de ese juego
    (achievements).
    """

    user: object
    entry: object = None
    hours: float | None = None
    finished: bool = False
    achievements: frozenset = frozenset()

    @property
    def ahead_from(self):
        """Desde cuántas horas se le pliega lo que se escribió (las suyas más AHEAD_HOURS), o None si nada se le pliega
        por horas: no se saben las suyas o ya lo ha terminado.

        Pantallas: Comunidad del juego, Hilo o pregunta («Lo que llevas»).
        """
        if self.hours is None or self.finished:
            return None
        return self.hours + AHEAD_HOURS


def reader(viewer, game):
    """El Reader de viewer en game: su entrada de biblioteca, sus horas, si lo ha terminado y sus logros de ese juego.
    Sin sesión (o sin juego), vacío: nada se le pliega por horas, pero sí lo que destripa hasta un logro.

    Pantallas: Comunidad del juego, Hilo o pregunta, Ficha del juego (comunidad: qué se le pliega a quien lee).
    """
    if not viewer.is_authenticated or game is None:
        return Reader(viewer)
    entry = LibraryEntry.objects.filter(user=viewer, game=game).first()
    owned = UnlockedAchievement.objects.filter(imported__user=viewer, achievement__title__game=game)
    return Reader(
        viewer, entry, entry.playtime_hours if entry else None,
        entry is not None and entry.status == LibraryEntry.Status.COMPLETED,
        frozenset(owned.values_list("achievement_id", flat=True)),
    )


def fold(item, who):
    """Por qué se le pliega item (una publicación, un hilo, una pregunta o una respuesta) a who, un Reader: FOLD_ACHIEVEMENT
    si destripa hasta un logro que no tiene, FOLD_HOURS si se escribió con AHEAD_HOURS horas o más de las que lleva y no
    lo ha terminado, o "" si nada. Lo de who nunca se le pliega.

    Pantallas: Comunidad del juego, Hilo o pregunta, Ficha del juego (comunidad: lo que va plegado).
    """
    if who.user.is_authenticated and item.user_id == who.user.pk:
        return ""
    limit = getattr(item, "spoiler_achievement_id", None)
    if limit and limit not in who.achievements:
        return FOLD_ACHIEVEMENT
    if (
        not who.finished and item.hours is not None and who.hours is not None
        and float(item.hours) >= who.hours + AHEAD_HOURS
    ):
        return FOLD_HOURS
    return ""


def mark_folded(items, who):
    """Apunta en cada uno de items por qué se le pliega a who (fold) y cuántas horas lleva él (reader_hours), para
    decirlo al plegarlo; devuelve la misma lista.

    Pantallas: Comunidad del juego, Hilo o pregunta, Ficha del juego (comunidad: lo que va plegado).
    """
    for item in items:
        item.fold = fold(item, who)
        item.reader_hours = who.hours
    return items


def _visible(viewer, game):
    """Lo de la comunidad de game que puede ver viewer: lo de los perfiles públicos y lo suyo.

    Pantallas: Comunidad del juego, Ficha del juego (comunidad), Listas del juego (cuántas cosas tiene su comunidad).
    """
    return Post.objects.filter(Profile.visible_filter(viewer), game=game)


def _with_images():
    """Condición de las publicaciones que llevan alguna imagen.

    Pantallas: Comunidad del juego (pestaña «Con imágenes»).
    """
    return Exists(PostImage.objects.filter(post=OuterRef("pk")))


def _in_tab(posts, tab):
    """posts, solo las de la pestaña tab de TABS: los hilos, las preguntas o las que llevan imágenes (o todas).

    Pantallas: Comunidad del juego (pestañas).
    """
    if tab == "hilos":
        return posts.filter(kind=Post.Kind.THREAD)
    if tab == "preguntas":
        return posts.filter(kind=Post.Kind.QUESTION)
    if tab == "imagenes":
        return posts.filter(_with_images())
    return posts


def tab_counts(viewer, game):
    """Cuántas cosas de la comunidad de game puede ver viewer en cada pestaña de TABS: {valor de ?ver=: cuántas}.

    Pantallas: Comunidad del juego (lo que hay en cada pestaña y en la barra del juego).
    """
    visible = _visible(viewer, game)
    counts = visible.aggregate(
        all=Count("pk"),
        threads=Count("pk", filter=Q(kind=Post.Kind.THREAD)),
        questions=Count("pk", filter=Q(kind=Post.Kind.QUESTION)),
    )
    return {
        "": counts["all"], "hilos": counts["threads"], "preguntas": counts["questions"],
        "imagenes": visible.filter(_with_images()).count(),
    }


def community_count(viewer, game):
    """Cuántas cosas (publicaciones, hilos y preguntas) de la comunidad de game puede ver viewer.

    Pantallas: Ficha del juego (barra del juego y comunidad), Listas del juego (barra del juego).
    """
    return _visible(viewer, game).count()


def head_context(viewer, game, count=None):
    """Variables de la cabecera de la comunidad de game y de su barra Ficha · Comunidad · Listas (games/_tabs.html):
    cuántas cosas de su comunidad puede ver viewer (community_count; count, si ya se sabe) y en cuántas de las listas que
    puede ver está (lists_count).

    Pantallas: Comunidad del juego, Hilo o pregunta, Nuevo hilo o pregunta (cabecera y barra del juego).
    """
    return {
        "game": game,
        "community_count": community_count(viewer, game) if count is None else count,
        "lists_count": _lists_with(game, viewer).count(),
    }


def community(request, slug):
    """Comunidad del juego slug: lo que la gente publica sobre él, sus hilos y sus preguntas, de lo que tiene actividad más
    reciente a lo que menos (Post.last_activity_at), en tandas de PAGE (?antes=, el cursor de «Cargar más»), con las
    pestañas de TABS (?ver=) y cuántas cosas hay en cada una. Lo que un administrador ha fijado va arriba, en la primera
    tanda (salvo en «Con imágenes»).

    Solo sale lo de los perfiles que puede ver quien mira, y se le pliega lo que va por delante de lo que lleva jugado
    (mark_folded), que le explica «Lo que llevas». Con HTMX y ?antes= devuelve solo la tanda siguiente. La comunidad de
    una edición lleva a la de su juego.

    Pantallas: Comunidad del juego.
    """
    game = get_object_or_404(Game.objects.select_related("parent"), slug=slug)
    if game.parent_id:
        return redirect("social:community", game.parent.slug)
    viewer = request.user
    tab = request.GET.get("ver", "")
    if tab not in TABS:
        tab = ""
    items = _in_tab(_posts().filter(Profile.visible_filter(viewer), game=game), tab)
    cursor = request.GET.get("antes", "")
    pinned = []
    if not cursor and tab != "imagenes":
        pinned = list(items.filter(pinned=True).order_by("-last_activity_at", "-pk"))
        mark_saved_posts(mark_liked_posts(pinned, viewer), viewer)
    batch, next_cursor = post_batch(items.filter(pinned=False), viewer, cursor, size=PAGE, field="last_activity_at")
    who = reader(viewer, game)
    mark_folded(pinned + batch, who)
    base = reverse("social:community", args=[game.slug])
    context = {"game": game, "posts": batch, "pinned": pinned, "show_user": True, "hide_game": True, "reader": who}
    if next_cursor:
        query = {"antes": next_cursor, **({"ver": tab} if tab else {})}
        context["more_url"] = context["more_href"] = f"{base}?{urlencode(query)}"
    if cursor and request.headers.get("HX-Request"):
        return render(request, "social/_community_items.html", context)
    counts = tab_counts(viewer, game)
    context.update(head_context(viewer, game, counts[""]))
    context.update({
        "tab": tab,
        "tabs": [
            (label, f"{base}?{urlencode({'ver': value})}" if value else base, value == tab, counts[value])
            for value, label in TABS.items()
        ],
    })
    return render(request, "social/community.html", context)


def game_posts(request, slug):
    """Lo último de la comunidad de slug para su ficha: las PREVIEW cosas con actividad más reciente (publicaciones,
    hilos y preguntas) que puede ver quien mira, plegado lo que va por delante de él, y el enlace a toda la comunidad.
    La ficha lo pide con HTMX al llegar a ello; sin HTMX lleva a la comunidad.

    Pantallas: Ficha del juego (comunidad).
    """
    game = get_object_or_404(Game, slug=slug)
    if not request.headers.get("HX-Request"):
        return redirect("social:community", game.slug)
    viewer = request.user
    posts = list(_posts().filter(Profile.visible_filter(viewer), game=game).order_by("-last_activity_at", "-pk")[:PREVIEW])
    mark_saved_posts(mark_liked_posts(posts, viewer), viewer)
    mark_folded(posts, reader(viewer, game))
    return render(request, "social/_game_posts.html", {
        "game": game, "posts": posts, "show_user": True, "hide_game": True,
        "community_count": community_count(viewer, game),
    })


def replies_context(post, viewer, who):
    """Variables de social/_replies.html para las respuestas de post (un hilo o una pregunta) tal como las ve viewer (who
    es su Reader): en orden y, en las preguntas, primero la que le ha servido a quien pregunta (is_answer). En cada una,
    a qué contesta (reply_to, con su autor), si viewer ya la ha denunciado (reported), por qué se le pliega (fold, ver
    mark_folded) y si se le pliega también la cita de lo que contesta (quote_fold); en las preguntas por un logro, si su
    autor lo tiene (has_achievement).

    Pantallas: Hilo o pregunta (respuestas).
    """
    replies = list(
        post.comments.select_related(
            "post", "user__profile", "user__psn_account", "user__xbox_account", "reply_to__user__profile",
        ).prefetch_related("user__steam_accounts")
    )
    reported, holders = set(), set()
    if viewer.is_authenticated and replies:
        reported = set(
            Report.objects.filter(
                reporter=viewer, content_type=ContentType.objects.get_for_model(PostComment),
                object_id__in=[reply.pk for reply in replies],
            ).values_list("object_id", flat=True)
        )
    if post.achievement_id and replies:
        holders = set(
            UnlockedAchievement.objects.filter(
                achievement_id=post.achievement_id, imported__user__in={reply.user_id for reply in replies},
            ).values_list("imported__user_id", flat=True)
        )
    mark_folded(replies, who)
    folds = {reply.pk: reply.fold for reply in replies}
    for reply in replies:
        reply.reported = reply.pk in reported
        reply.has_achievement = reply.user_id in holders
        reply.is_answer = reply.pk == post.answer_id
        reply.quote_fold = folds.get(reply.reply_to_id, "")
    # La que le ha servido, la primera; las demás, en su orden.
    replies.sort(key=lambda reply: not reply.is_answer)
    return {"replies": replies, "reply_count": len(replies)}


def thread(request, slug, post_id):
    """Página de un hilo o de una pregunta de la comunidad de un juego: su título, quién lo abrió y cómo iba con el
    juego, el logro por el que pregunta (si es un «¿Cómo se consigue?»), el texto y las imágenes (plegados si van por
    delante de quien lee), sus «me gusta», guardar, copiar el enlace, borrar o denunciar, y sus respuestas
    (replies_context) con el formulario para responder.

    La ve quien puede ver el perfil de su autor (si no, 404). Una publicación, o una dirección con otro juego, lleva a
    su página. Con ?responder= el formulario llega contestando a esa respuesta, para quien no tiene JavaScript.

    Pantallas: Hilo o pregunta.
    """
    post = _readable_post(request, post_id)
    if not post.is_thread or post.game_id is None or post.game.slug != slug:
        return redirect(post)
    viewer = request.user
    mark_saved_posts(mark_liked_posts([post], viewer), viewer)
    who = reader(viewer, post.game)
    mark_folded([post], who)
    form = ReplyForm(post=post)
    target = None
    raw = request.GET.get("responder", "")
    if raw.isdigit():
        target = post.comments.select_related("user__profile").filter(pk=raw).first()
        if target:
            form = ReplyForm(post=post, initial={"reply_to": target.pk})
    is_author = viewer == post.user
    reported = viewer.is_authenticated and not is_author and Report.objects.filter(
        content_type=ContentType.objects.get_for_model(Post), object_id=post.pk, reporter=viewer
    ).exists()
    return render(request, "social/thread.html", {
        **head_context(viewer, post.game),
        "post": post, "reader": who, "is_author": is_author, "reported": reported,
        "open_report": "denunciar" in request.GET, "reply_form": form, "reply_target": target,
        "has_achievement": post.achievement_id in who.achievements,
        **replies_context(post, viewer, who),
    })


def _thread_initial(request, game):
    """Lo que trae ya puesto el formulario de abrir un hilo: el tipo (?tipo=pregunta o, si no, hilo) y, desde «¿Cómo se
    consigue?», el logro por el que pregunta (?logro=, uno del juego) con su título.

    Pantallas: Nuevo hilo o pregunta.
    """
    initial = {"kind": Post.Kind.QUESTION if request.GET.get("tipo") == "pregunta" else Post.Kind.THREAD}
    raw = request.GET.get("logro", "")
    achievement = Achievement.objects.filter(pk=raw, title__game=game).first() if raw.isdigit() else None
    if achievement:
        name = "este logro oculto" if achievement.hidden else f"«{achievement.name}»"
        initial.update(kind=Post.Kind.QUESTION, achievement=achievement.pk, title=f"¿Cómo se consigue {name}?")
    return initial


@login_required
def thread_new(request, slug):
    """Abre un hilo o hace una pregunta en la comunidad del juego slug (POST) o enseña el formulario (GET), con lo que
    traiga ya puesto (_thread_initial). Al publicarlo se apunta en la actividad de su autor (social.activity.post_published),
    como las publicaciones, y lleva a su página; si falta algo, vuelve a enseñar el formulario con los errores. El de una
    edición lleva al de su juego.

    Pantallas: Nuevo hilo o pregunta, Hilo o pregunta.
    """
    game = get_object_or_404(Game.objects.select_related("parent"), slug=slug)
    if game.parent_id:
        query = request.GET.urlencode()
        return redirect(f"{reverse('social:thread_new', args=[game.parent.slug])}{'?' + query if query else ''}")
    form = ThreadForm(
        request.POST or None, request.FILES or None, user=request.user, game=game,
        initial=_thread_initial(request, game),
    )
    if request.method == "POST" and form.is_valid():
        post = form.save(request.user)
        post_published(post)
        messages.success(request, "Pregunta publicada." if post.is_question else "Hilo abierto.")
        return redirect(post)
    chosen = form["achievement"].value()
    achievement = Achievement.objects.filter(pk=chosen, title__game=game).first() if str(chosen or "").isdigit() else None
    return render(request, "social/thread_form.html", {
        **head_context(request.user, game),
        "form": form,
        "kind": form["kind"].value(),
        "achievement": achievement,
        "can_mark_achievement": form.fields["spoiler_achievement"].queryset.exists(),
        "post_image_kind": images.KINDS["post"],
        "post_max_images": POST_MAX_IMAGES,
    })


def achievement_question(request, slug, achievement_id):
    """«¿Cómo se consigue?» de un logro que te falta en «Tus logros»: lleva a la pregunta sobre ese logro con actividad
    más reciente que puedes ver o, si aún no la hay, a hacerla (con sesión; sin ella, a iniciarla).

    Pantallas: Ficha del juego («Tus logros»: «¿Cómo se consigue?»), Hilo o pregunta, Nuevo hilo o pregunta.
    """
    game = get_object_or_404(Game, slug=slug)
    achievement = get_object_or_404(Achievement, pk=achievement_id, title__game=game)
    question = (
        _visible(request.user, game).filter(kind=Post.Kind.QUESTION, achievement=achievement)
        .select_related("game", "user").order_by("-last_activity_at", "-pk").first()
    )
    if question:
        return redirect(question)
    query = urlencode({"tipo": "pregunta", "logro": achievement.pk})
    return redirect(f"{reverse('social:thread_new', args=[game.slug])}?{query}")


def _replies_response(request, post, form):
    """Las respuestas de post ya cambiadas, para HTMX: social/_replies.html entero, con el contador de la página.

    Pantallas: Hilo o pregunta (respuestas, al responder, borrar o elegir la que ha servido).
    """
    who = reader(request.user, post.game)
    return render(request, "social/_replies.html", {
        "post": post, "reply_form": form, "reader": who, "oob": True, **replies_context(post, request.user, who),
    })


@require_POST
@login_required
def thread_reply(request, post_id):
    """Responde en un hilo o en una pregunta (y, con reply_to, a una de sus respuestas): guarda cómo iba con el juego
    quien responde, sube el hilo en su comunidad (last_activity_at) y avisa a su autor y a quien contesta
    (_notify_reply). Los hilos cerrados no admiten respuestas (403) y las publicaciones se comentan desde su página (400).

    Con HTMX devuelve las respuestas ya con la nueva (o el formulario con el error) y el contador de la página; sin HTMX
    vuelve al hilo, a la respuesta nueva.

    Pantallas: Hilo o pregunta (responder), Comunidad del juego (lo último arriba), Avisos (aviso a su autor y a quien
    contestas).
    """
    post = _readable_post(request, post_id)
    if not post.is_thread:
        return HttpResponseBadRequest("Las publicaciones se comentan desde su página.")
    if post.closed:
        return HttpResponseForbidden("Este hilo está cerrado y ya no admite respuestas.")
    form = ReplyForm(request.POST, post=post)
    reply = None
    if form.is_valid():
        reply = form.save(commit=False)
        reply.post, reply.user = post, request.user
        reply.take_snapshot(request.user, post.game)
        reply.save()
        Post.objects.filter(pk=post.pk).update(last_activity_at=reply.created_at)
        _notify_reply(post, reply)
        form = ReplyForm(post=post)
    elif not request.headers.get("HX-Request"):
        errors = [error for field in ("text", "reply_to") for error in form.errors.get(field, [])]
        messages.error(request, " ".join(errors) or "La respuesta no es válida.")
    if request.headers.get("HX-Request"):
        return _replies_response(request, post, form)
    return redirect(reply.get_absolute_url() if reply else f"{post.get_absolute_url()}#responder")


def _notify_reply(post, reply):
    """Avisa de la respuesta reply en post: a su autor, de que alguien ha respondido en su hilo o a su pregunta, y, si
    contesta a otra respuesta, a quien la escribió (salvo si es el mismo autor del hilo, que ya tiene el suyo). Nadie
    recibe avisos de lo suyo, y varias respuestas seguidas de la misma persona son un aviso (accounts.notices).

    Pantallas: Avisos (respuestas en tus hilos y preguntas y a tus mensajes), todas (el número de la campana).
    """
    actor = reply.user
    name = actor.profile.name
    url = f"{post.get_absolute_url()}#respuestas"
    if post.is_question:
        kind, text = Notification.Kind.QUESTION_ANSWER, f"{name} ha respondido a tu pregunta «{post.title}»."
    else:
        kind, text = Notification.Kind.THREAD_REPLY, f"{name} ha respondido en tu hilo «{post.title}»."
    notices.notify(post.user, kind, text, url, game=post.game, actor=actor)
    target = reply.reply_to
    if target is not None and target.user_id != post.user_id:
        notices.notify(
            target.user, Notification.Kind.REPLY, f"{name} te ha respondido en «{post.title}».", url,
            game=post.game, actor=actor,
        )


@require_POST
@login_required
def choose_answer(request, comment_id):
    """Marca la respuesta comment_id como la que le ha servido a quien preguntó o, si ya lo era, la desmarca: solo quien
    hizo la pregunta (si no, 403). Sale la primera de las respuestas y la pregunta queda resuelta en la comunidad; su
    autor recibe un aviso la primera vez.

    Con HTMX devuelve las respuestas reordenadas; sin HTMX vuelve a la respuesta.

    Pantallas: Hilo o pregunta («Me ha servido»), Comunidad del juego (preguntas resueltas), Avisos (aviso a quien
    respondió).
    """
    reply = get_object_or_404(PostComment.objects.select_related("user__profile"), pk=comment_id)
    post = _readable_post(request, reply.post_id)
    if not post.is_question or post.user_id != request.user.pk:
        return HttpResponseForbidden("Solo quien pregunta elige la respuesta que le ha servido.")
    chosen = post.answer_id != reply.pk
    post.answer_id = reply.pk if chosen else None
    Post.objects.filter(pk=post.pk).update(answer=reply if chosen else None)
    if chosen:
        notices.notify(
            reply.user, Notification.Kind.ANSWER_CHOSEN,
            f"A {post.user.profile.name} le ha servido tu respuesta en «{post.title}».", reply.get_absolute_url(),
            game=post.game, actor=request.user,
        )
    if request.headers.get("HX-Request"):
        return _replies_response(request, post, ReplyForm(post=post))
    return redirect(reply.get_absolute_url())


@require_POST
@login_required
def reply_delete(request, comment_id):
    """Borra una respuesta de un hilo o de una pregunta: solo quien la escribió o el autor del hilo (si no, 403). Si era
    la que le servía a quien preguntó, la pregunta vuelve a estar sin resolver, y las que la citaban se quedan sin cita.

    Con HTMX devuelve las respuestas sin ella; sin HTMX vuelve a las respuestas.

    Pantallas: Hilo o pregunta (botón «Borrar» de una respuesta).
    """
    reply = get_object_or_404(PostComment.objects.select_related("post"), pk=comment_id)
    if not reply.can_delete(request.user):
        return HttpResponseForbidden("No puedes borrar esta respuesta.")
    _readable_post(request, reply.post_id)
    reply.delete()
    post = _readable_post(request, reply.post_id)
    if request.headers.get("HX-Request"):
        return _replies_response(request, post, ReplyForm(post=post))
    return redirect(f"{post.get_absolute_url()}#respuestas")
