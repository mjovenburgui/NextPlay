from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import images
from accounts.models import Notification
from games import media
from games.models import LANGUAGE_FILTERS, Game, language_filter

from . import points
from .autofill import play_details
from .forms import CommentForm, ReportForm, ReviewForm, ReviewImagesForm
from .models import (
    MAX_IMAGES, REVIEW_MAX_LENGTH, LibraryEntry, Report, Review, ReviewComment, ReviewLike, WishlistItem,
)
from .reminders import latest_reviews


REVIEWS_SHOWN = 30
# Orden de las reseñas de la ficha (?resenas=); el primero es el de por defecto.
REVIEW_SORTS = {"": "Recientes", "gustadas": "Más gustadas"}
# Filtros de las reseñas de la ficha (?filtro=).
REVIEW_FILTERS = {"": "Todas", "terminado": "Lo han terminado", "horas": "Con más de 10 h"}
LONG_PLAY_HOURS = 10
# Si una reseña se escribió con estas horas más de las que lleva quien la lee (y no lo ha terminado), se
# pliega: puede contar lo que le queda.
AHEAD_HOURS = 10
# Lo que se puede denunciar: clave → (cómo se nombra en el botón, cómo se nombra en el aviso a los administradores).
REPORT_TARGETS = {"resena": ("esta reseña", "una reseña"), "comentario": ("este comentario", "un comentario")}


def mark_liked(reviews, user):
    """Apunta en cada reseña de reviews (liked) si user le ha dado «me gusta»; devuelve la misma lista."""
    liked = set()
    if user.is_authenticated:
        liked = set(ReviewLike.objects.filter(user=user, review__in=reviews).values_list("review_id", flat=True))
    for review in reviews:
        review.liked = review.pk in liked
    return reviews


def review_context(request, game, entry):
    """Variables de la sección de reseñas de la ficha de game para quien la ve (entry: su juego en la biblioteca).

    Las reseñas que puede leer, ordenadas con ?resenas= (recientes o más gustadas) y filtradas con ?filtro=
    (las de quien lo ha terminado o las escritas con más de LONG_PLAY_HOURS horas); las suyas del juego,
    borradores incluidos, con las horas que lleva de más desde la última (extra_hours, ver
    Review.hours_since) para proponerle actualizarla; lo mejor y lo peor que más se repite (consensus, ver
    library.points); y en cada reseña si le ha dado «me gusta» (liked) y si va más adelante que él en el
    juego (ahead, ver AHEAD_HOURS) para plegarla.
    """
    viewer = request.user
    reviews = Review.objects.filter(game=game).visible_to(viewer)
    total = reviews.count()
    sort = request.GET.get("resenas", "")
    if sort not in REVIEW_SORTS:
        sort = ""
    chosen = request.GET.get("filtro", "")
    if chosen not in REVIEW_FILTERS:
        chosen = ""
    if sort == "gustadas":
        reviews = reviews.order_by("-like_count", "-published_at", "-id")
    if chosen == "terminado":
        reviews = reviews.filter(status=LibraryEntry.Status.COMPLETED)
    elif chosen == "horas":
        reviews = reviews.filter(hours__gte=LONG_PLAY_HOURS)
    shown = mark_liked(list(reviews[:REVIEWS_SHOWN]), viewer)
    viewer_hours = entry.playtime_hours if entry else None
    finished = entry is not None and entry.status == LibraryEntry.Status.COMPLETED
    for review in shown:
        review.ahead = (
            not finished and review.user_id != viewer.pk and review.hours is not None and viewer_hours is not None
            and float(review.hours) >= viewer_hours + AHEAD_HOURS
        )
    own = list(Review.objects.filter(game=game, user=viewer)) if viewer.is_authenticated else []
    # Solo a la última publicada se le propone actualizarla: las anteriores son de otras partidas.
    published = [review for review in own if not review.is_draft]
    latest = max(published, key=lambda review: (review.published_at, review.pk), default=None)
    for review in own:
        review.extra_hours = review.hours_since(entry) if review is latest else None

    def link(new_sort, new_filter):
        """URL de la ficha con ese orden y ese filtro de reseñas, y el resto de la consulta como estaba."""
        query = {key: value for key, value in request.GET.items() if key not in ("resenas", "filtro")}
        query.update({key: value for key, value in (("resenas", new_sort), ("filtro", new_filter)) if value})
        return f"{game.get_absolute_url()}{'?' + urlencode(query) if query else ''}#resenas"

    return {
        "reviews": shown,
        "review_count": total,
        "filtered_count": reviews.count() if chosen else total,
        "review_sorts": [(label, link(value, chosen), value == sort) for value, label in REVIEW_SORTS.items()],
        "review_filters": [(label, link(sort, value), value == chosen) for value, label in REVIEW_FILTERS.items()],
        "own_reviews": own,
        "has_published_review": any(not review.is_draft for review in own),
        # Lo mejor y lo peor que más se repite en las reseñas del juego.
        "consensus": points.consensus(game, viewer),
        "can_review": entry is not None,
        "viewer_hours": viewer_hours,
    }


def _respond(request, game, entry):
    """Respuesta común de las acciones de biblioteca.

    Si la petición viene de HTMX devuelve el widget de biblioteca ya actualizado junto con el
    recuadro de valoración de la comunidad y la sección de reseñas (que HTMX cambia en su sitio);
    si no, redirige a la ficha del juego.
    """
    # HTMX swaps the widget in place; without JS the form falls back to a full reload.
    if request.headers.get("HX-Request"):
        # The community rating box sits elsewhere on the page; it is swapped out of band.
        return render(
            request,
            "library/_widget.html",
            {
                "game": game,
                "entry": entry,
                "statuses": LibraryEntry.Status.choices,
                "community": game.community_rating(),
                "community_oob": True,
                **review_context(request, game, entry),
            },
        )
    return redirect(game)


@require_POST
@login_required
def set_status(request, game_id):
    """Añade el juego a la biblioteca del usuario con el estado enviado, o cambia el que tenía.

    Responde 400 si el estado no es uno de los permitidos.
    """
    game = get_object_or_404(Game, pk=game_id)
    status = request.POST.get("status")
    if status not in LibraryEntry.Status.values:
        return HttpResponseBadRequest("Estado no válido.")
    entry, _ = LibraryEntry.objects.update_or_create(
        user=request.user, game=game, defaults={"status": status}
    )
    return _respond(request, game, entry)


@require_POST
@login_required
def remove(request, game_id):
    """Quita el juego de la biblioteca del usuario (no da error si no estaba)."""
    game = get_object_or_404(Game, pk=game_id)
    LibraryEntry.objects.filter(user=request.user, game=game).delete()
    return _respond(request, game, None)


@require_POST
@login_required
def rate(request, game_id):
    """Guarda la valoración del usuario (de 1 a 10 medias estrellas) para un juego de su biblioteca.

    Con rating=0 borra la valoración. Responde 404 si el juego no está en su biblioteca y 400 si
    la valoración no es un número del 0 al 10.
    """
    game = get_object_or_404(Game, pk=game_id)
    entry = get_object_or_404(LibraryEntry, user=request.user, game=game)
    try:
        rating = int(request.POST.get("rating", ""))
    except ValueError:
        rating = -1
    if not 0 <= rating <= LibraryEntry.MAX_RATING:
        return HttpResponseBadRequest("Valoración no válida.")
    entry.rating = rating or None
    entry.save(update_fields=["rating", "updated_at"])
    return _respond(request, game, entry)


@login_required
def review_new(request, game_id):
    """Editor de una reseña nueva de un juego de la biblioteca del usuario.

    Empieza con la nota que ya le había puesto y, si ya tiene alguna reseña publicada del juego, marcada
    como rejugada. Si el juego no está en su biblioteca, vuelve a la ficha avisando.
    """
    game = get_object_or_404(Game.objects.prefetch_related("platforms"), pk=game_id)
    entry = LibraryEntry.objects.filter(user=request.user, game=game).first()
    if entry is None:
        messages.error(request, "Añade el juego a tu biblioteca para escribir una reseña.")
        return redirect(game)
    replay = Review.objects.filter(user=request.user, game=game, is_draft=False).exists()
    return _review_editor(request, Review(user=request.user, game=game, rating=entry.rating, is_replay=replay), entry)


@login_required
def review_edit(request, review_id):
    """Editor de una reseña ya escrita (o de un borrador) del usuario; las de otros dan 404."""
    review = get_object_or_404(Review.objects.select_related("game"), pk=review_id, user=request.user)
    entry = LibraryEntry.objects.filter(user=request.user, game=review.game).first()
    return _review_editor(request, review, entry)


def _review_editor(request, review, entry):
    """Muestra y guarda el editor de review. «Publicar» la publica y «Guardar borrador» la guarda sin que la vea nadie más.

    Al publicarla se apuntan las horas y el estado que tiene ahora en la biblioteca (Review.snapshot) y, si
    lleva nota, pasa a ser también la nota del juego en su biblioteca, que es la que cuenta en la media de
    la comunidad. La fecha de publicación es la de la primera vez. En una reseña nueva, la plataforma y las
    fechas vienen ya rellenas con lo de las importaciones (library.autofill) y «Lo mejor» y «Lo peor»
    sugieren lo que han escrito otros (library.points). Las imágenes (subidas o capturas oficiales del
    juego) van en un formulario aparte, ReviewImagesForm, que se guarda a la vez.
    """
    game = review.game
    # En una reseña nueva, la plataforma y las fechas se rellenan solas con lo que dejaron las importaciones.
    fill = play_details(request.user, game, entry) if review.pk is None else {"initial": {}, "notes": {}}
    form = ReviewForm(request.POST or None, instance=review, game=game, initial=fill["initial"])
    images_form = ReviewImagesForm(
        request.POST or None, request.FILES or None, review=review, official=media.official_images(game)
    )
    # Se comprueban los dos aunque falle el primero, para enseñar todos los errores a la vez.
    if request.method == "POST" and all([form.is_valid(), images_form.is_valid()]):
        review = form.save(commit=False)
        publish = "borrador" not in request.POST
        # is_draft no está en el formulario: aún es el de antes de guardar.
        was_published = review.pk is not None and not review.is_draft
        review.is_draft = not publish
        if publish:
            review.published_at = review.published_at or timezone.now()
            review.snapshot(entry)
        review.save()
        images_form.save(review)
        if publish and review.rating and entry is not None and entry.rating != review.rating:
            entry.rating = review.rating
            entry.save(update_fields=["rating", "updated_at"])
        if not publish:
            messages.success(request, "Borrador guardado. Solo lo ves tú.")
            return redirect("library:review_edit", review.pk)
        messages.success(request, "Reseña actualizada." if was_published else "Reseña publicada.")
        return redirect(review)
    return render(request, "library/review_form.html", {
        "form": form,
        "images_form": images_form,
        "review": review,
        "game": game,
        "entry": entry,
        "rating_steps": LibraryEntry.RATING_STEPS,
        "max_length": REVIEW_MAX_LENGTH,
        "max_images": MAX_IMAGES,
        # Medidas que el navegador usa para reducir antes de subirlas las imágenes demasiado grandes.
        "image_kind": images.KINDS["review"],
        "fill_notes": fill["notes"],
        "suggestions": points.suggestions(game, request.user),
    })


@require_POST
@login_required
def review_delete(request, review_id):
    """Borra una reseña del usuario (con sus «me gusta» y comentarios) y vuelve a las reseñas del juego."""
    review = get_object_or_404(Review.objects.select_related("game"), pk=review_id, user=request.user)
    game = review.game
    review.delete()
    messages.success(request, "Reseña borrada.")
    return redirect(f"{game.get_absolute_url()}#resenas")


@require_POST
@login_required
def review_preview(request):
    """Vista previa del texto de una reseña o un comentario con su formato (fragmento HTML para el editor)."""
    return render(request, "library/_review_preview.html", {"text": request.POST.get("text", "")[:REVIEW_MAX_LENGTH]})


def _readable_review(request, review_id):
    """Reseña publicada review_id que el usuario puede leer; 404 si es un borrador o de un perfil privado ajeno."""
    review = get_object_or_404(Review.objects.select_related("user__profile", "game"), pk=review_id, is_draft=False)
    if not review.user.profile.visible_to(request.user):
        raise Http404
    return review


def review_detail(request, username, review_id):
    """Página de una reseña: el juego, la nota, los datos de la partida, sus imágenes, sus «me gusta» y sus comentarios.

    Los borradores y las reseñas de perfiles privados solo los ve su autor; a los demás les da 404. A su
    autor se le propone actualizarla si ha jugado mucho más desde que la escribió (solo en la última que
    ha publicado del juego); los demás pueden denunciarla (con ?denunciar, el formulario llega abierto).
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
    """Comentarios de la reseña con sus autores, en orden, y en cada uno si viewer ya lo ha denunciado (reported)."""
    comments = list(
        review.comments.select_related("user__profile", "user__psn_account", "review").prefetch_related("user__steam_accounts")
    )
    reported = set()
    if viewer.is_authenticated and comments:
        reported = set(
            Report.objects.filter(
                reporter=viewer, content_type=ContentType.objects.get_for_model(ReviewComment),
                object_id__in=[comment.pk for comment in comments],
            ).values_list("object_id", flat=True)
        )
    for comment in comments:
        comment.reported = comment.pk in reported
    review.comment_count = len(comments)
    return {"comments": comments}


def _notify(review, actor, kind):
    """Avisa al autor de la reseña de que actor le ha dado «me gusta» o la ha comentado.

    No se repite: un «me gusta» de la misma persona solo avisa una vez aunque lo quite y lo vuelva a dar, y
    un comentario no avisa si ya hay uno igual sin leer.
    """
    name = actor.profile.name
    if kind == Notification.Kind.LIKE:
        text, url = f"A {name} le ha gustado tu reseña de {review.game.name}.", review.get_absolute_url()
        repeated = Notification.objects.filter(user=review.user, kind=kind, text=text, url=url)
    else:
        text, url = f"{name} ha comentado tu reseña de {review.game.name}.", f"{review.get_absolute_url()}#comentarios"
        repeated = Notification.objects.filter(user=review.user, kind=kind, text=text, url=url, read_at=None)
    if not repeated.exists():
        Notification.objects.create(user=review.user, kind=kind, text=text[:300], url=url, game=review.game)


@require_POST
@login_required
def review_like(request, review_id):
    """Da o quita el «me gusta» del usuario a una reseña de otro (avisando a su autor la primera vez).

    Con HTMX devuelve el botón con el contador nuevo; si no, vuelve a la página de la reseña. Las propias
    no se pueden marcar (400).
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
    """Publica un comentario en una reseña (avisando a su autor si no es él) y vuelve a sus comentarios."""
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
    """Borra un comentario: solo quien lo escribió o el autor de la reseña (si no, 403)."""
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
    """Denuncia una reseña de otro para que la revise un administrador; las propias no se pueden denunciar (400)."""
    review = _readable_review(request, review_id)
    if review.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propia reseña.")
    return _report(request, review, "resena", review.game, review.get_absolute_url())


@require_POST
@login_required
def comment_report(request, comment_id):
    """Denuncia un comentario de otro para que lo revise un administrador; los propios no se pueden denunciar (400).

    Como con la reseña, da 404 si la reseña del comentario es un borrador o de un perfil privado ajeno.
    """
    comment = get_object_or_404(ReviewComment.objects.select_related("user", "review__game"), pk=comment_id)
    _readable_review(request, comment.review_id)
    if comment.user_id == request.user.pk:
        return HttpResponseBadRequest("No puedes denunciar tu propio comentario.")
    return _report(request, comment, "comentario", comment.review.game, comment.get_absolute_url())


def _report(request, target, kind, game, back_url):
    """Guarda la denuncia del usuario sobre target (una reseña o un comentario, según kind) y avisa a los administradores.

    Si ya lo había denunciado, cambia el motivo y la vuelve a dejar pendiente. Con HTMX devuelve el botón de
    denunciar (library/_report.html) ya como «Gracias» o, si falta el motivo, abierto con el error; si no,
    vuelve a back_url con un mensaje.
    """
    form = ReportForm(request.POST)
    context = {"kind": kind, "target": target, "noun": REPORT_TARGETS[kind][0], "report_url": request.path}
    if form.is_valid():
        report, _ = Report.objects.update_or_create(
            content_type=ContentType.objects.get_for_model(target), object_id=target.pk, reporter=request.user,
            defaults={"reason": form.cleaned_data["reason"], "detail": form.cleaned_data["detail"], "resolved_at": None},
        )
        _notify_staff(report, f"{REPORT_TARGETS[kind][1]} de {target.user.username}", game)
        if request.headers.get("HX-Request"):
            return render(request, "library/_report.html", {**context, "reported": True, "thanks": True})
        messages.success(request, "Gracias por avisar: un administrador lo revisará.")
        return redirect(back_url)
    if request.headers.get("HX-Request"):
        return render(request, "library/_report.html", {**context, "open": True, "report_error": True})
    messages.error(request, "Elige el motivo de la denuncia.")
    return redirect(back_url)


def _notify_staff(report, what, game):
    """Avisa en la campana a los administradores de la denuncia de what («una reseña de ana») en game, sin
    repetirlo mientras tengan uno igual sin leer.

    El aviso lleva a la denuncia en el admin, desde donde se puede ir a lo denunciado y borrarlo.
    """
    url = reverse("admin:library_report_change", args=[report.pk])
    text = f"Han denunciado {what} en {game.name}: {report.get_reason_display()}."
    staff = get_user_model().objects.filter(is_staff=True, is_active=True).exclude(pk=report.reporter_id)
    for admin_user in staff:
        notices = Notification.objects.filter(user=admin_user, kind=Notification.Kind.REPORT, url=url, read_at=None)
        if not notices.exists():
            Notification.objects.create(user=admin_user, kind=Notification.Kind.REPORT, text=text[:300], url=url, game=game)


def _wish_response(request, game, wish, error=""):
    """Respuesta de las acciones de la lista de deseos: el botón «Lo quiero» actualizado si es HTMX; si no,
    vuelve a la página de la que vino (la ficha por defecto) avisando del error si lo hay."""
    if request.headers.get("HX-Request"):
        return render(request, "library/_wish.html", {"game": game, "wish": wish, "wish_error": error})
    if error:
        messages.error(request, error)
    target = request.POST.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(game)


@require_POST
@login_required
def wish_toggle(request, game_id):
    """Añade el juego a la lista de deseos del usuario o lo quita si ya estaba."""
    game = get_object_or_404(Game, pk=game_id)
    wish, created = WishlistItem.objects.get_or_create(user=request.user, game=game)
    if not created:
        wish.delete()
        wish = None
    return _wish_response(request, game, wish)


@require_POST
@login_required
def wish_target(request, game_id):
    """Guarda el precio por debajo del cual avisar (vacío = solo avisar del mínimo histórico).

    Al cambiarlo se olvida el último aviso, para volver a comparar con el precio nuevo. Responde 404
    si el juego no está en su lista.
    """
    game = get_object_or_404(Game, pk=game_id)
    wish = get_object_or_404(WishlistItem, user=request.user, game=game)
    raw = request.POST.get("target_price", "").strip().replace(",", ".")
    try:
        target = Decimal(raw).quantize(Decimal("0.01")) if raw else None
    except InvalidOperation:
        target = Decimal(-1)
    if target is not None and not Decimal(0) <= target < Decimal(100000):
        return _wish_response(request, game, wish, "Escribe un precio en euros, por ejemplo 19,99.")
    wish.target_price = target
    wish.alerted_price = None
    wish.save(update_fields=["target_price", "alerted_price"])
    return _wish_response(request, game, wish)


# Valor de ?orden= en la lista de deseos → (etiqueta, clave de orden). Los que no tienen precio van al final.
WISHLIST_SORTS = {
    "descuento": ("Mayor descuento", lambda row: -(row["best"].cut if row["best"] else -1)),
    "precio": ("Más baratos", lambda row: (row["best"] is None, row["best"].price if row["best"] else 0)),
    "nombre": ("Nombre", lambda row: row["wish"].game.name.casefold()),
}


@login_required
def wishlist(request):
    """Lista de deseos del usuario con el mejor precio de hoy de cada juego (en sus tiendas, si las ha
    elegido), su mínimo histórico y si está por debajo de su límite. Se ordena con ?orden= (recientes
    por defecto, descuento, precio o nombre) y se filtra por idioma con ?idioma= (ver LANGUAGE_FILTERS)."""
    wishes = WishlistItem.objects.filter(user=request.user).select_related("game").prefetch_related("game__prices")
    total = wishes.count()
    language = request.GET.get("idioma", "")
    if language in LANGUAGE_FILTERS:
        wishes = wishes.filter(language_filter(language, prefix="game__"))
    else:
        language = ""
    owned = set(
        LibraryEntry.objects.filter(user=request.user, game__wishlisted_by__user=request.user)
        .values_list("game_id", flat=True)
    )
    shops = set(request.user.profile.shops) or None
    rows = []
    for wish in wishes:
        best = wish.game.cheapest(shops)
        low = wish.game.historical_low
        rows.append({
            "wish": wish,
            "best": best,
            "owned": wish.game_id in owned,
            "below_target": bool(best and wish.target_price is not None and best.price <= wish.target_price),
            "at_low": bool(best and best.cut and low is not None and best.price <= low),
        })
    sort = request.GET.get("orden", "")
    if sort in WISHLIST_SORTS:
        rows.sort(key=WISHLIST_SORTS[sort][1])
    else:
        sort = ""
    return render(request, "library/wishlist.html", {
        "rows": rows,
        "total": total,
        "sorts": [(value, label) for value, (label, _) in WISHLIST_SORTS.items()],
        "active_sort": sort,
        "language_filters": LANGUAGE_FILTERS.items(),
        "active_language": language,
    })
