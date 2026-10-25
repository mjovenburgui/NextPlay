"""La actividad de cada jugador: lo que hace con los juegos de su biblioteca y lo que importa.

Se apunta desde lo que hace el jugador (cambiar el estado de un juego, valorarlo, publicar una reseña, quitarlo
de la biblioteca) y desde las importaciones de Steam, PlayStation y Xbox, nunca con señales: la sincronización
de cada noche también guarda juegos y les cambia el estado, y eso no lo ha hecho el jugador.

Lo que hace con un mismo juego en poco rato se junta en una sola actividad («Terminó Hades ★★★★½» con su
reseña debajo), y cada importación es una sola aunque traiga cientos de juegos. La lista de deseos no sale:
es privada, y no se enseña en ningún otro sitio.
"""

from datetime import timedelta

from django.utils import timezone

from .models import Activity

# Lo que se hace con un mismo juego, o dos importaciones seguidas, se junta si es lo último que hizo y no hace más de esto.
MERGE_WINDOW = timedelta(hours=1)
# Portadas que se enseñan de una importación: las de los juegos más jugados.
IMPORT_COVERS = 4


def _recent(user):
    """Lo último que ha hecho user, si fue hace menos de MERGE_WINDOW (para juntar con ello lo siguiente); si no, None."""
    latest = Activity.objects.filter(user=user).order_by("-updated_at", "-id").first()
    if latest and latest.updated_at >= timezone.now() - MERGE_WINDOW:
        return latest
    return None


def _game_activity(user, game, merge_only=False, **changes):
    """Junta changes (status, rating o review) con lo último que hizo user si fue con game hace poco; si no, crea otra.

    Con merge_only no crea ninguna: sirve para deshacer algo recién hecho (quitar la nota que acaba de poner).
    Si se queda vacía, se borra. Devuelve la actividad, o None si no hay ninguna.
    """
    now = timezone.now()
    activity = _recent(user)
    if activity is None or activity.kind != Activity.Kind.GAME or activity.game_id != game.pk:
        if merge_only:
            return None
        activity = Activity(user=user, kind=Activity.Kind.GAME, game=game, created_at=now)
    for field, value in changes.items():
        setattr(activity, field, value)
    activity.updated_at = now
    if activity.is_empty:
        if activity.pk:
            activity.delete()
        return None
    activity.save()
    return activity


def status_changed(user, game, status):
    """Apunta que user ha pasado game a status (o que lo ha añadido a su biblioteca con ese estado)."""
    return _game_activity(user, game, status=status)


def rated(user, game, rating):
    """Apunta la nota que user le ha puesto a game. Si la ha quitado (None), solo se deshace la que acababa de poner."""
    if rating:
        return _game_activity(user, game, rating=rating)
    return _game_activity(user, game, merge_only=True, rating=None)


def review_published(review):
    """Apunta que su autor ha publicado review, con su nota si la lleva.

    Solo la primera vez: si la volvió a dejar en borrador y la publica otra vez, ya tenía su actividad.
    """
    if Activity.objects.filter(review=review).exists():
        return None
    changes = {"review": review}
    if review.rating:
        changes["rating"] = review.rating
    return _game_activity(review.user, review.game, **changes)


def game_removed(user, game):
    """user ha quitado game de su biblioteca: se olvida lo que hizo con él, salvo sus reseñas, que siguen publicadas."""
    activities = Activity.objects.filter(user=user, kind=Activity.Kind.GAME, game=game)
    activities.filter(review__isnull=True).delete()
    activities.update(status="", rating=None)


def post_published(post):
    """Apunta la publicación post en la actividad de su autor. Cada una va sola: no se junta con nada."""
    return Activity.objects.create(
        user=post.user, kind=Activity.Kind.POST, post=post, game=post.game,
        created_at=post.created_at, updated_at=post.created_at,
    )


def games_imported(user, platform, entries):
    """Apunta como una sola actividad los juegos que acaban de entrar en la biblioteca de user con una importación.

    platform es la clave de la plataforma ("steam", "psn" o "xbox") y entries, las entradas nuevas de la
    biblioteca; se guardan cuántas son y los ids de los IMPORT_COVERS juegos más jugados. Una importación de la
    misma plataforma justo después (al volver a sincronizar, por ejemplo) se suma a la anterior.
    """
    if not entries:
        return None
    minutes = f"{platform}_playtime_minutes"
    top = [entry.game_id for entry in sorted(entries, key=lambda entry: -(getattr(entry, minutes) or 0))[:IMPORT_COVERS]]
    now = timezone.now()
    activity = _recent(user)
    if activity and activity.kind == Activity.Kind.IMPORT and activity.data.get("platform") == platform:
        previous = activity.data
        activity.data = {
            "platform": platform,
            "count": previous.get("count", 0) + len(entries),
            "games": (previous.get("games", []) + top)[:IMPORT_COVERS],
        }
    else:
        activity = Activity(
            user=user, kind=Activity.Kind.IMPORT, created_at=now,
            data={"platform": platform, "count": len(entries), "games": top},
        )
    activity.updated_at = now
    activity.save()
    return activity
