"""Vista de la Página de la reseña (u/<usuario>/resenas/<id>/) y los botones de cada reseña, que se pulsan también desde la
Ficha del juego, el Perfil y el feed de Inicio: «me gusta», comentar, borrar un comentario y denunciar.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts import notices
from accounts.models import Notification

from ..forms import CommentForm
from ..models import LibraryEntry, Review, ReviewComment, ReviewLike
from ..reminders import latest_reviews
from .comentarios_y_denuncias import _report, comment_thread


def _readable_review(request, review_id):
    """Reseña publicada review_id que el usuario puede leer; 404 si es un borrador o de un perfil privado ajeno.

    Pantallas: Página de la reseña, Ficha del juego, Perfil, Inicio (404 al pulsar algo de una reseña que no se puede
    leer).
    """
    review = get_object_or_404(Review.objects.select_related("user__profile", "game"), pk=review_id, is_draft=False)
    if not review.user.profile.visible_to(request.user):
        raise Http404
    return review


def review_detail(request, username, review_id):
    """Página de una reseña: el juego, la nota, los datos de la partida, sus imágenes, sus «me gusta» y sus comentarios.

    Los borradores y las reseñas de perfiles privados solo los ve su autor; a los demás les da 404. A su
    autor se le propone actualizarla si ha jugado mucho más desde que la escribió (solo en la última que
    ha publicado del juego); los demás pueden denunciarla (con ?denunciar, el formulario llega abierto).

    Pantallas: Página de la reseña.
    """
    review = get_object_or_404(
        Review.objects.select_related("user__profile", "user__psn_account", "game", "platform")
        .prefetch_related("user__steam_accounts", "images"),
        pk=review_id, user__username=username,
    )
    viewer = request.user
    is_author = viewer == review.user
    if not is_author and (review.is_draft or not review.user.profile.visible_to(viewer)):
        raise Http404
    review.like_count = review.likes.count()
    review.liked = viewer.is_authenticated and review.likes.filter(user=viewer).exists()
    extra_hours = None
    if is_author and latest_reviews(viewer, [review.game]).get(review.game_id) == review:
        extra_hours = review.hours_since(LibraryEntry.objects.filter(user=viewer, game=review.game).first())
    return render(request, "library/review_detail.html", {
        "review": review,
        "game": review.game,
        "is_author": is_author,
        "extra_hours": extra_hours,
        "reported": viewer.is_authenticated and not is_author and review.reports.filter(reporter=viewer).exists(),
        "open_report": "denunciar" in request.GET,
        **_comments_context(review, viewer),
        "comment_form": CommentForm(),
    })


def _comments_context(review, viewer):
    """Variables de library/_comments.html para los comentarios de la reseña (ver comment_thread).

    Pantallas: Página de la reseña (comentarios).
    """
    return comment_thread(
        review, review.comments.select_related("review"), viewer,
        comment_url=reverse("library:review_comment", args=[review.pk]),
        delete_url_name="library:comment_delete", report_url_name="library:comment_report",
        count_id=f"comment-count-{review.pk}",
    )


def _notify(review, actor, kind):
    """Avisa al autor de la reseña de que actor le ha dado «me gusta» o la ha comentado.

    No se repite (accounts.notices): un «me gusta» de la misma persona solo avisa una vez aunque lo quite y lo vuelva
    a dar, y un comentario no avisa si ya hay otro suyo sin leer en esa reseña.

    Pantallas: Avisos («me gusta» y comentarios en tus reseñas), todas (el número de la campana).
    """
    name = actor.profile.name
    if kind == Notification.Kind.LIKE:
        text, url = f"A {name} le ha gustado tu reseña de {review.game.name}.", review.get_absolute_url()
    else:
        text, url = f"{name} ha comentado tu reseña de {review.game.name}.", f"{review.get_absolute_url()}#comentarios"
    notices.notify(review.user, kind, text, url, game=review.game, actor=actor)


@require_POST
@login_required
def review_like(request, review_id):
    """Da o quita el «me gusta» del usuario a una reseña de otro (avisando a su autor la primera vez).

    Con HTMX devuelve el botón con el contador nuevo; si no, vuelve a la página de la reseña. Las propias
    no se pueden marcar (400).

    Pantallas: Ficha del juego, Perfil, Inicio, Página de la reseña (botón «me gusta» de las reseñas), Avisos (aviso a
    su autor).
    """
    review = _readable_review(request, review_id)
    if review.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes darle «me gusta» a tu propia reseña.")
    like, created = ReviewLike.objects.get_or_create(user=request.user, review=review)
    if created:
        _notify(review, request.user, Notification.Kind.LIKE)
    else:
        like.delete()
    if request.headers.get("HX-Request"):
        return render(request, "library/_like.html", {"review": review, "liked": created, "like_count": review.likes.count()})
    return redirect(review)


@require_POST
@login_required
def review_comment(request, review_id):
    """Publica un comentario en una reseña (avisando a su autor si no es él) y vuelve a sus comentarios.

    Pantallas: Página de la reseña (comentarios), Avisos (aviso a su autor).
    """
    review = _readable_review(request, review_id)
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.review, comment.user = review, request.user
        comment.save()
        if review.user_id != request.user.pk:
            _notify(review, request.user, Notification.Kind.COMMENT)
        form = CommentForm()
    elif not request.headers.get("HX-Request"):
        messages.error(request, " ".join(form.errors.get("text", ["El comentario no es válido."])))
    if request.headers.get("HX-Request"):
        return render(request, "library/_comments.html", {
            "review": review, "comment_form": form, "oob": True, **_comments_context(review, request.user),
        })
    return redirect(f"{review.get_absolute_url()}#comentarios")


@require_POST
@login_required
def comment_delete(request, comment_id):
    """Borra un comentario: solo quien lo escribió o el autor de la reseña (si no, 403).

    Pantallas: Página de la reseña (botón «Borrar» de un comentario).
    """
    comment = get_object_or_404(ReviewComment.objects.select_related("review__user__profile", "review__game"), pk=comment_id)
    if not comment.can_delete(request.user):
        return HttpResponseForbidden("No puedes borrar este comentario.")
    review = comment.review
    comment.delete()
    if request.headers.get("HX-Request"):
        return render(request, "library/_comments.html", {
            "review": review, "comment_form": CommentForm(), "oob": True, **_comments_context(review, request.user),
        })
    return redirect(f"{review.get_absolute_url()}#comentarios")


@require_POST
@login_required
def review_report(request, review_id):
    """Denuncia una reseña de otro para que la revise un administrador; las propias no se pueden denunciar (400).

    Pantallas: Página de la reseña, Ficha del juego, Perfil (botón «Denunciar» de las reseñas),
    Panel de administración (denuncias).
    """
    review = _readable_review(request, review_id)
    if review.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propia reseña.")
    return _report(request, review, "resena", review.game, review.get_absolute_url())


@require_POST
@login_required
def comment_report(request, comment_id):
    """Denuncia un comentario de otro para que lo revise un administrador; los propios no se pueden denunciar (400).

    Como con la reseña, da 404 si la reseña del comentario es un borrador o de un perfil privado ajeno.

    Pantallas: Página de la reseña (denunciar un comentario), Panel de administración (denuncias).
    """
    comment = get_object_or_404(ReviewComment.objects.select_related("user", "review__game"), pk=comment_id)
    _readable_review(request, comment.review_id)
    if comment.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propio comentario.")
    return _report(request, comment, "comentario", comment.review.game, comment.get_absolute_url())
