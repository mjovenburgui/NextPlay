"""«Tu biblioteca» en la Ficha del juego: los botones de estado, nota y quitar, y la sección de reseñas del juego, con los
«me gusta» marcados (mark_liked, que usan también el Perfil y el feed de Inicio).
"""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from challenges import progress as challenge_progress
from games.models import Game
from social import activity

from .. import points
from ..models import LibraryEntry, Review, ReviewLike

REVIEWS_SHOWN = 30
# Orden de las reseñas de la ficha (?resenas=); el primero es el de por defecto.
REVIEW_SORTS = {"": "Recientes", "gustadas": "Más gustadas"}
# Filtros de las reseñas de la ficha (?filtro=).
REVIEW_FILTERS = {"": "Todas", "terminado": "Lo han terminado", "horas": "Con más de 10 h"}
LONG_PLAY_HOURS = 10
# Si una reseña se escribió con estas horas más de las que lleva quien la lee (y no lo ha terminado), se
# pliega: puede contar lo que le queda.
AHEAD_HOURS = 10


def mark_liked(reviews, user):
    """Apunta en cada reseña de reviews (liked) si user le ha dado «me gusta»; devuelve la misma lista.

    Pantallas: Ficha del juego (reseñas), Perfil (pestaña Reseñas), Inicio (feed: tus «me gusta» marcados).
    """
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

    Pantallas: Ficha del juego (reseñas).
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

    Pantallas: Ficha del juego (Tu biblioteca: lo que cambia tras cada botón).
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

    Si el estado es nuevo, lo apunta en su actividad (social.activity) y, al pasarlo a «Completado», apunta cuándo
    (completed_at, que se vacía si después cambia de estado) y mira si con eso ha conseguido algún reto
    (challenges.progress.check). Responde 400 si el estado no es uno de los permitidos.

    Pantallas: Ficha del juego (Tu biblioteca: estado), Perfil (biblioteca), Catálogo (estado de cada tarjeta),
    Página de la lista (estado de cada juego), Inicio (feed y tu resumen), Retos (progreso), Página del reto
    (progreso).
    """
    game = get_object_or_404(Game, pk=game_id)
    status = request.POST.get("status")
    if status not in LibraryEntry.Status.values:
        return HttpResponseBadRequest("Estado no válido.")
    previous = LibraryEntry.objects.filter(user=request.user, game=game).values_list("status", flat=True).first()
    defaults = {"status": status}
    if status != previous:
        defaults["completed_at"] = timezone.now() if status == LibraryEntry.Status.COMPLETED else None
    entry, _ = LibraryEntry.objects.update_or_create(user=request.user, game=game, defaults=defaults)
    if status != previous:
        activity.status_changed(request.user, game, status)
        if status == LibraryEntry.Status.COMPLETED:
            challenge_progress.check(request.user)
    return _respond(request, game, entry)


@require_POST
@login_required
def remove(request, game_id):
    """Quita el juego de la biblioteca del usuario (no da error si no estaba) y olvida su actividad con él, salvo las reseñas.

    Pantallas: Ficha del juego (Tu biblioteca: quitar el juego), Perfil (biblioteca), Catálogo (estado de cada
    tarjeta), Página de la lista (estado de cada juego), Inicio (feed y tu resumen), Retos (progreso), Página del reto
    (progreso).
    """
    game = get_object_or_404(Game, pk=game_id)
    deleted, _ = LibraryEntry.objects.filter(user=request.user, game=game).delete()
    if deleted:
        activity.game_removed(request.user, game)
    return _respond(request, game, None)


@require_POST
@login_required
def rate(request, game_id):
    """Guarda la valoración del usuario (de 1 a 10 medias estrellas) para un juego de su biblioteca.

    Con rating=0 borra la valoración. Si cambia, lo apunta en su actividad (social.activity). Responde 404
    si el juego no está en su biblioteca y 400 si la valoración no es un número del 0 al 10.

    Pantallas: Ficha del juego (tu nota y la valoración de la comunidad), Perfil (notas de la biblioteca), Inicio
    (feed).
    """
    game = get_object_or_404(Game, pk=game_id)
    entry = get_object_or_404(LibraryEntry, user=request.user, game=game)
    try:
        rating = int(request.POST.get("rating", ""))
    except ValueError:
        rating = -1
    if not 0 <= rating <= LibraryEntry.MAX_RATING:
        return HttpResponseBadRequest("Valoración no válida.")
    changed = entry.rating != (rating or None)
    entry.rating = rating or None
    entry.save(update_fields=["rating", "updated_at"])
    if changed:
        activity.rated(request.user, game, entry.rating)
    return _respond(request, game, entry)
