"""Vistas de Escribir reseña (<juego>/resenas/nueva/ y resenas/<id>/editar/): el editor con su vista previa, publicar o
guardar el borrador y borrar la reseña.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import images
from games import media
from games.models import Game
from social import activity

from .. import points
from ..autofill import play_details
from ..forms import ReviewForm, ReviewImagesForm
from ..models import MAX_IMAGES, REVIEW_MAX_LENGTH, LibraryEntry, Review


@login_required
def review_new(request, game_id):
    """Editor de una reseña nueva de un juego de la biblioteca del usuario.

    Empieza con la nota que ya le había puesto y, si ya tiene alguna reseña publicada del juego, marcada
    como rejugada. Si el juego no está en su biblioteca, vuelve a la ficha avisando.

    Pantallas: Escribir reseña.
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
    """Editor de una reseña ya escrita (o de un borrador) del usuario; las de otros dan 404.

    Pantallas: Escribir reseña.
    """
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
    juego) van en un formulario aparte, ReviewImagesForm, que se guarda a la vez. La primera vez que se
    publica se apunta en la actividad del autor (social.activity); al editarla, no.

    Pantallas: Escribir reseña (publicar o guardar el borrador), Ficha del juego (reseñas), Perfil (pestaña Reseñas y
    reseña destacada), Página de la reseña, Inicio (feed).
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
        if publish and not was_published:
            activity.review_published(review)
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
    """Borra una reseña del usuario (con sus «me gusta» y comentarios) y vuelve a las reseñas del juego.

    Pantallas: Escribir reseña (botón «Borrar»), Ficha del juego (reseñas), Perfil (pestaña Reseñas).
    """
    review = get_object_or_404(Review.objects.select_related("game"), pk=review_id, user=request.user)
    game = review.game
    review.delete()
    messages.success(request, "Reseña borrada.")
    return redirect(f"{game.get_absolute_url()}#resenas")


@require_POST
@login_required
def review_preview(request):
    """Vista previa del texto de una reseña o un comentario con su formato (fragmento HTML para el editor).

    Pantallas: Escribir reseña (vista previa del texto con formato).
    """
    return render(request, "library/_review_preview.html", {"text": request.POST.get("text", "")[:REVIEW_MAX_LENGTH]})
