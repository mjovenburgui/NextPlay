"""Avisos a los autores de reseñas que se han quedado atrás: «Llevas 40 h más en Hades: ¿actualizas tu reseña?».

Cada reseña guarda las horas que llevaba su autor al publicarla (Review.hours). Cuando una importación de
Steam, PlayStation o Xbox le sube mucho las horas de ese juego (ver Review.hours_since), se le avisa en la
campana para que la actualice. Solo cuenta la última reseña publicada de cada juego: las anteriores son de
otras partidas y se quedan como estaban.
"""

from django.urls import reverse

from accounts import notices
from accounts.models import Notification

from .models import LibraryEntry, Review


def latest_reviews(user, games=None):
    """Última reseña publicada de user de cada juego (de esos juegos, si se dan), como {id del juego: reseña}.

    Pantallas: Página de la reseña (propuesta de actualizarla), Avisos (reseñas que se han quedado atrás).
    """
    reviews = Review.objects.filter(user=user, is_draft=False).select_related("game").order_by("-published_at", "-id")
    if games is not None:
        reviews = reviews.filter(game__in=games)
    latest = {}
    for review in reviews:
        latest.setdefault(review.game_id, review)
    return latest


def remind_outdated_reviews(user):
    """Avisa a user en la campana de sus reseñas que se han quedado atrás y devuelve cuántos avisos ha creado.

    Un aviso por reseña hasta que la vuelva a guardar: si sigue jugando sin tocarla, no se repite. Si ha apagado
    estos avisos en Editar perfil, no se crea ninguno (accounts.notices).

    Pantallas: Avisos (reseñas que se han quedado atrás), todas (el número de la campana), Editar perfil (Cuentas
    vinculadas: se mira al importar horas), Perfil (modal «Vincular cuentas» de su dueño).
    """
    latest = latest_reviews(user)
    entries = LibraryEntry.objects.filter(user=user, game_id__in=list(latest))
    created = 0
    for entry in entries:
        review = latest[entry.game_id]
        extra = review.hours_since(entry)
        if extra is None:
            continue
        text = f"Llevas {extra} h más en {review.game.name} que cuando escribiste tu reseña: ¿la actualizas?"
        url = reverse("library:review_edit", args=[review.pk])
        if notices.notify(user, Notification.Kind.REVIEW_UPDATE, text, url, game=review.game, since=review.updated_at):
            created += 1
    return created
