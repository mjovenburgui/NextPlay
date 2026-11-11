"""Que seguir a alguien sirva en toda la web: lo que ha hecho con un juego la gente que sigues (en su ficha, con sus
reseñas delante), lo que tenéis en común dos jugadores (en el perfil del otro) y un aviso cuando alguien a quien sigues
reseña un juego que quieres o tienes pendiente.

Todo respeta quién ve qué (accounts.models.Profile.visible_to y visible_filter): de un perfil privado no sale nada.

Pantallas: Ficha del juego (Gente que sigues; reseñas), Perfil (pestaña En común), Inicio (A quién seguir), Avisos
(reseñas de la gente que sigues), Editar perfil (Avisos).
"""

from collections import Counter

from django.contrib.auth import get_user_model

from accounts import notices
from accounts.models import Notification, Profile
from games.models import Genre
from library.models import LibraryEntry, Review, WishlistItem

from .models import Follow

User = get_user_model()

# Juegos valorados por los dos desde los que se comparan sus notas; con menos, solo cuentan los juegos y los géneros.
MIN_RATED = 5
# Géneros de cada uno que se comparan (los más repetidos de su biblioteca) y cuántos de los compartidos se nombran.
TOP_GENRES = 6
SHARED_GENRES = 3
# Diferencia de nota (en medias estrellas) desde la que un juego cuenta como discrepancia: dos estrellas.
DISCREPANCY = 4
# Gente que sigues que enseña la ficha de un juego.
FRIENDS_SHOWN = 8


def following_ids(viewer):
    """Ids de la gente a la que sigue viewer y cuyo perfil puede ver: de un perfil privado no se enseña nada.

    Pantallas: Ficha del juego (Gente que sigues).
    """
    if not viewer.is_authenticated:
        return []
    return list(
        Follow.objects.filter(follower=viewer).filter(Profile.visible_filter(viewer, path="followed"))
        .values_list("followed_id", flat=True)
    )


def friends_on_game(viewer, game):
    """Lo que ha hecho con game la gente a la que sigue viewer: {"people" (sus entradas de la biblioteca, cada una con su
    usuario, su perfil y review, su reseña publicada del juego o None; primero quien la tiene y después de más a menos
    horas, como mucho FRIENDS_SHOWN), "count" (cuántos lo tienen), "average" (la media de sus notas en estrellas, o None)
    y "rated" (cuántos lo han valorado)}, o None si no lo tiene nadie de los que sigue.

    Pantallas: Ficha del juego (Gente que sigues).
    """
    ids = following_ids(viewer)
    if not ids:
        return None
    entries = list(LibraryEntry.objects.filter(user_id__in=ids, game=game).select_related("user__profile"))
    if not entries:
        return None
    # Si alguien tiene varias (una por partida), la última publicada.
    reviews = {
        review.user_id: review
        for review in Review.objects.filter(game=game, user_id__in=ids, is_draft=False).order_by("published_at", "pk")
    }
    for entry in entries:
        entry.review = reviews.get(entry.user_id)
    entries.sort(key=lambda entry: (entry.review is None, -(entry.playtime_minutes or 0), entry.user.username))
    rated = [entry.stars for entry in entries if entry.rating]
    return {
        "people": entries[:FRIENDS_SHOWN],
        "count": len(entries),
        "average": sum(rated) / len(rated) if rated else None,
        "rated": len(rated),
    }


def common_count(viewer, owner):
    """Cuántos juegos tienen viewer y owner en sus bibliotecas, para el número de la pestaña En común.

    Pantallas: Perfil (pestaña En común).
    """
    return LibraryEntry.objects.filter(
        user=owner, game_id__in=LibraryEntry.objects.filter(user=viewer).values("game_id"),
    ).count()


def _top_genres(user):
    """Los TOP_GENRES géneros más repetidos en la biblioteca de user, del que más al que menos.

    Pantallas: Perfil (pestaña En común: los géneros que os gustan a los dos).
    """
    counts = Counter(Genre.objects.filter(games__library_entries__user=user).values_list("pk", flat=True))
    return [pk for pk, _ in counts.most_common(TOP_GENRES)]


def _decimal(value):
    """Un número con un decimal, como se escribe en España: 0,8.

    Pantallas: Perfil (pestaña En común: cuánto os separan las notas).
    """
    return f"{value:.1f}".replace(".", ",")


def _summary(count, rated, distance, genres):
    """La frase que resume lo compatibles que son dos jugadores diciendo con qué se ha calculado: sus notas si han valorado
    MIN_RATED juegos en común o más; si no, los juegos que tienen los dos y si juegan a los mismos géneros (cuáles, lo
    dicen las etiquetas que van debajo).

    Pantallas: Perfil (pestaña En común).
    """
    if not count:
        return "Aún no tenéis ningún juego en común."
    if distance is not None:
        if distance <= 0.5:
            verdict = "Puntuáis casi igual"
        elif distance <= 1:
            verdict = "Puntuáis parecido"
        elif distance <= 1.5:
            verdict = "Coincidís a medias"
        else:
            verdict = "Puntuáis bastante distinto"
        return (
            f"{verdict}: de media os separan {_decimal(distance)} estrellas en los {rated} juegos que habéis valorado "
            "los dos."
        )
    games = f"{count} juego{'s' if count != 1 else ''} en común"
    # Los géneros salen debajo, como etiquetas: la frase solo dice que los hay.
    taste = " y os van los mismos géneros" if genres else ""
    return (
        f"Tenéis {games}{taste}. Para comparar vuestras notas hacen falta {MIN_RATED} juegos valorados por los dos "
        f"(lleváis {rated})."
    )


def in_common(viewer, owner):
    """Lo que tienen en común viewer y owner, para la pestaña En común del perfil de owner: {"games" (los juegos de los
    dos como {"game", "mine", "theirs"}, con la entrada de la biblioteca de cada uno, de más a menos horas sumadas),
    "count", "rated" (cuántos han valorado los dos), "distance" (cuántas estrellas separan de media sus notas, solo con
    MIN_RATED o más), "genres" (los géneros que más juegan los dos), "discrepancy" (el juego de los dos en el que más
    distintas son sus notas, si se separan DISCREPANCY medias estrellas o más, o None) y "summary" (la frase que lo
    resume, _summary)}.

    Pantallas: Perfil (pestaña En común).
    """
    mine = {entry.game_id: entry for entry in LibraryEntry.objects.filter(user=viewer).select_related("game")}
    pairs = [
        {"game": mine[entry.game_id].game, "mine": mine[entry.game_id], "theirs": entry}
        for entry in LibraryEntry.objects.filter(user=owner, game_id__in=list(mine))
    ]
    pairs.sort(key=lambda pair: (
        -((pair["mine"].playtime_minutes or 0) + (pair["theirs"].playtime_minutes or 0)), pair["game"].name.casefold(),
    ))
    rated = [pair for pair in pairs if pair["mine"].rating and pair["theirs"].rating]
    distance = None
    if len(rated) >= MIN_RATED:
        distance = sum(abs(pair["mine"].stars - pair["theirs"].stars) for pair in rated) / len(rated)
    discrepancy = max(rated, key=lambda pair: abs(pair["mine"].rating - pair["theirs"].rating), default=None)
    if discrepancy is not None and abs(discrepancy["mine"].rating - discrepancy["theirs"].rating) < DISCREPANCY:
        discrepancy = None
    theirs_top = set(_top_genres(owner))
    shared = [pk for pk in _top_genres(viewer) if pk in theirs_top][:SHARED_GENRES]
    names = Genre.objects.in_bulk(shared)
    genres = [names[pk] for pk in shared]
    return {
        "games": pairs,
        "count": len(pairs),
        "rated": len(rated),
        "distance": distance,
        "genres": genres,
        "discrepancy": discrepancy,
        "summary": _summary(len(pairs), len(rated), distance, genres),
    }


def notify_followers(review):
    """Avisa a quien sigue al autor de review y tiene su juego en «Lo quiero» o en su pendiente de que lo ha reseñado
    (Notification.Kind.FOLLOWED_REVIEW, una vez por persona y reseña). Si el perfil del autor es privado no avisa a
    nadie: nadie más puede leerla.

    Pantallas: Escribir reseña (al publicar), Avisos (reseñas de la gente que sigues), todas (el número de la campana).
    """
    author, game = review.user, review.game
    if not author.profile.is_public:
        return
    followers = Follow.objects.filter(followed=author).values("follower_id")
    wanting = set(WishlistItem.objects.filter(game=game, user_id__in=followers).values_list("user_id", flat=True))
    pending = set(
        LibraryEntry.objects.filter(game=game, user_id__in=followers, status=LibraryEntry.Status.BACKLOG)
        .values_list("user_id", flat=True)
    )
    for user in User.objects.filter(pk__in=wanting | pending).select_related("profile"):
        where = "«Lo quiero»" if user.pk in wanting else "tu pendiente"
        notices.notify(
            user, Notification.Kind.FOLLOWED_REVIEW,
            f"{author.profile.name} ha reseñado {game.name}, que tienes en {where}.",
            review.get_absolute_url(), game=game, actor=author,
        )
