"""Ediciones juntadas con su juego: lo que la gente tiene de una edición («Batman: Arkham Asylum - Game of the Year
Edition», Game.parent) pasa a su juego, para que sus horas, notas, reseñas, listas y logros cuenten en una sola ficha.

Desde que las ediciones se enlazan con su juego (games.igdb.link_editions), lo que se importa de una edición ya va al
juego (accounts.matching.save_titles). Esto es para lo que se guardó antes, y para las ediciones que se enlacen después
(una pasada a IGDB que descubre una edición, o un administrador que la enlaza a mano): lo lanza el comando
merge_editions, también cada noche.
"""

from django.contrib.auth import get_user_model
from django.db import transaction

from accounts.matching import update_library
from accounts.models import ExternalTitle, FavoriteGame, ImportedTitle, Notification, Profile
from challenges.models import Challenge
from library.models import LibraryEntry, PlaySession, Review, WishlistItem
from lists.models import ListItem
from social.models import Activity, Post

from .models import Game

# De más a menos avanzado: al juntar la entrada de un jugador en la edición con la suya en el juego, se queda el estado
# más avanzado de las dos.
STATUS_RANK = {
    LibraryEntry.Status.COMPLETED: 5,
    LibraryEntry.Status.PLAYING: 4,
    LibraryEntry.Status.PLAYED: 3,
    LibraryEntry.Status.DROPPED: 2,
    LibraryEntry.Status.BACKLOG: 1,
}
# Lo que se cuenta de cada edición antes de juntarla, con cómo se dice en el resumen del comando (uno y varios).
KINDS = (
    ("library", "en 1 biblioteca", "en {n} bibliotecas"),
    ("reviews", "1 reseña", "{n} reseñas"),
    ("wishlist", "en 1 lista de deseos", "en {n} listas de deseos"),
    ("lists", "en 1 lista", "en {n} listas"),
    ("favorites", "en los favoritos de 1 perfil", "en los favoritos de {n} perfiles"),
    ("sessions", "1 sesión de juego", "{n} sesiones de juego"),
    ("titles", "1 título importado", "{n} títulos importados"),
    ("activity", "1 actividad", "{n} actividades"),
    ("posts", "1 publicación", "{n} publicaciones"),
    ("notifications", "1 aviso", "{n} avisos"),
    ("challenges", "en 1 reto", "en {n} retos"),
    ("banners", "de cabecera en 1 perfil", "de cabecera en {n} perfiles"),
)


def holdings(edition):
    """Cuánto tiene la gente de una edición, {clave de KINDS: número}, solo lo que no es cero.

    Pantallas: ninguna (resumen del comando merge_editions).
    """
    counts = {
        "library": LibraryEntry.objects.filter(game=edition).count(),
        "reviews": Review.objects.filter(game=edition).count(),
        "wishlist": WishlistItem.objects.filter(game=edition).count(),
        "lists": ListItem.objects.filter(game=edition).count(),
        "favorites": FavoriteGame.objects.filter(game=edition).count(),
        "sessions": PlaySession.objects.filter(game=edition).count(),
        "titles": ExternalTitle.objects.filter(game=edition).count(),
        "activity": Activity.objects.filter(game=edition).count(),
        "posts": Post.objects.filter(game=edition).count(),
        "notifications": Notification.objects.filter(game=edition).count(),
        "challenges": Challenge.objects.filter(games=edition).count(),
        "banners": Profile.objects.filter(banner_game=edition).count(),
    }
    return {key: count for key, count in counts.items() if count}


def describe(counts):
    """Resumen legible de holdings, en el orden de KINDS: «en 2 bibliotecas, 1 reseña, 1 título importado».

    Pantallas: ninguna (resumen del comando merge_editions).
    """
    return ", ".join(
        (one if counts[key] == 1 else many).format(n=counts[key]) for key, one, many in KINDS if counts.get(key)
    )


def pending():
    """Ediciones enlazadas con su juego a las que aún les queda algo de algún jugador: [(edición, holdings)].

    Pantallas: ninguna (comando merge_editions).
    """
    editions = Game.objects.filter(parent__isnull=False, parent__parent__isnull=True).select_related("parent")
    found = []
    for edition in editions.order_by("name"):
        counts = holdings(edition)
        if counts:
            found.append((edition, counts))
    return found


@transaction.atomic
def merge(edition):
    """Pasa al juego de una edición todo lo que la gente tiene de ella y devuelve lo que había (holdings).

    - Los títulos importados que apuntaban a la edición apuntan al juego, y las horas de quien los tiene se recolocan
      con accounts.matching.update_library, que las suma a las del juego en cada plataforma.
    - Las entradas de biblioteca pasan al juego; si el jugador ya lo tenía, se juntan en la suya (_combine) y se borra
      la de la edición.
    - Reseñas, actividad, publicaciones y avisos pasan al juego tal cual; lo que no se puede repetir (lista de deseos,
      listas, favoritos y sesiones del mismo día) pasa si el juego no estaba ya y si estaba se junta o se quita.
    - Los retos que la tenían pasan a tener el juego, y las cabeceras de perfil con su arte, el del juego.

    Pantallas: Perfil (biblioteca, horas, reseñas, listas, favoritos y retos), Ficha del juego (las horas, notas y
    reseñas de las ediciones cuentan en la del juego), Lista de deseos, Página de la lista, Página del reto, Inicio
    (feed), Avisos, Juegos importados.
    """
    parent = edition.parent
    if parent is None or parent.parent_id:
        return {}
    counts = holdings(edition)
    titles = ExternalTitle.objects.filter(game=edition)
    imports = set(ImportedTitle.objects.filter(title__in=titles).values_list("user_id", "title__platform"))
    titles.update(game=parent)

    for entry in LibraryEntry.objects.filter(game=edition):
        target = LibraryEntry.objects.filter(user_id=entry.user_id, game=parent).first()
        if target is None:
            entry.game = parent
            entry.save(update_fields=["game"])
        else:
            _combine(target, entry)
            entry.delete()
    Review.objects.filter(game=edition).update(game=parent)
    for model, owner in ((WishlistItem, "user_id"), (FavoriteGame, "user_id"), (ListItem, "game_list_id")):
        for item in model.objects.filter(game=edition):
            if model.objects.filter(**{owner: getattr(item, owner)}, game=parent).exists():
                item.delete()
            else:
                item.game = parent
                item.save(update_fields=["game"])
    for session in PlaySession.objects.filter(game=edition):
        same = PlaySession.objects.filter(
            user_id=session.user_id, game=parent, platform=session.platform, day=session.day,
        ).first()
        if same is None:
            session.game = parent
            session.save(update_fields=["game"])
        else:
            same.minutes += session.minutes
            same.save(update_fields=["minutes"])
            session.delete()
    for model in (Activity, Post, Notification):
        model.objects.filter(game=edition).update(game=parent)
    for challenge in Challenge.objects.filter(games=edition):
        challenge.games.add(parent)
        challenge.games.remove(edition)
    Profile.objects.filter(banner_game=edition).update(banner_game=parent)

    users = get_user_model().objects.in_bulk({user_id for user_id, _ in imports})
    for user_id, platform in sorted(imports):
        update_library(users[user_id], platform, opened_only=platform != ExternalTitle.Platform.STEAM)
    return counts


def _combine(target, entry):
    """Junta en target (la entrada de un jugador en el juego) la que tenía en la edición: se queda el estado más
    avanzado (con su fecha de terminado), la nota de target si la tiene y, de cada plataforma, las horas que target
    no tenga (update_library las vuelve a sumar después con los títulos importados).

    Pantallas: Perfil (biblioteca, horas y notas), Ficha del juego (tu estado, tu nota y tus horas).
    """
    fields = []
    if STATUS_RANK.get(entry.status, 0) > STATUS_RANK.get(target.status, 0):
        target.status, target.completed_at = entry.status, entry.completed_at
        fields += ["status", "completed_at"]
    if target.rating is None and entry.rating is not None:
        target.rating = entry.rating
        fields.append("rating")
    if target.reviewed_at is None and entry.reviewed_at is not None:
        target.reviewed_at = entry.reviewed_at
        fields.append("reviewed_at")
    for platform in ("steam", "psn", "xbox"):
        minutes, last = f"{platform}_playtime_minutes", f"{platform}_last_played"
        if getattr(target, minutes) is None and getattr(entry, minutes) is not None:
            setattr(target, minutes, getattr(entry, minutes))
            setattr(target, last, getattr(entry, last))
            fields += [minutes, last]
    if fields:
        target.save(update_fields=fields + ["updated_at"])
