"""Lo que enseñan de las listas otras pantallas: las del Perfil (pestañas Listas y Tier lists y escaparate, con el tablero
en miniatura de las tier lists) y las del feed de Inicio (cuánto has jugado de cada una y tus «me gusta»).
"""

from django.db.models import Count, Exists, F, OuterRef, Q, Window
from django.db.models.functions import RowNumber

from library.models import LibraryEntry

from ..models import GameList, ListCollaborator, ListItem, ListLike

# Listas que salen en el escaparate del perfil; de las tier lists, que llevan su tablero en miniatura, caben menos.
SHOWCASE_LISTS = 3
SHOWCASE_TIER_LISTS = 2
# Portadas de cada nivel en el tablero en miniatura de las tarjetas de las tier lists (lists/_tier_card.html).
BOARD_COVERS = 6
# Estados de la biblioteca que cuentan como jugado en el progreso de una lista: todos menos Pendiente.
PLAYED_STATUSES = [
    LibraryEntry.Status.PLAYING, LibraryEntry.Status.COMPLETED, LibraryEntry.Status.PLAYED, LibraryEntry.Status.DROPPED,
]


def mark_progress(lists, viewer):
    """Apunta en cada lista de lists cuántos de sus juegos ha jugado viewer (viewer_played; None sin sesión).

    Jugado es cualquier estado de la biblioteca menos Pendiente (PLAYED_STATUSES). Devuelve la misma lista.

    Pantallas: Perfil (pestaña Listas), Listas del juego, Inicio (feed: cuántos juegos de cada lista has jugado).
    """
    counts = {}
    if viewer.is_authenticated and lists:
        counts = dict(
            ListItem.objects.filter(
                game_list__in=lists, game__library_entries__user=viewer,
                game__library_entries__status__in=PLAYED_STATUSES,
            ).values_list("game_list_id").annotate(n=Count("id"))
        )
    for game_list in lists:
        game_list.viewer_played = counts.get(game_list.pk, 0) if viewer.is_authenticated else None
    return lists


def mark_liked_lists(lists, viewer):
    """Apunta en cada lista de lists (liked) si viewer le ha dado «me gusta»; devuelve la misma lista.

    Pantallas: Página de la lista, Perfil, Inicio (tus «me gusta» marcados).
    """
    liked = set()
    if viewer.is_authenticated and lists:
        liked = set(ListLike.objects.filter(user=viewer, game_list__in=lists).values_list("game_list_id", flat=True))
    for game_list in lists:
        game_list.liked = game_list.pk in liked
    return lists


def profile_lists(owner, viewer, public_only=False):
    """Listas de owner y en las que colabora que puede ver viewer, de la más reciente a la más antigua, con lo que
    enseñan sus tarjetas (GameListQuerySet.with_preview) y cuántos de sus juegos ha jugado viewer (mark_progress). Las
    que son de otro llevan from_other, para decir de quién son.

    Con public_only salen solo las públicas, como las vería cualquiera (la vista previa de Editar perfil).

    Pantallas: Perfil (pestañas Listas y Tier lists y escaparate).
    """
    joined = ListCollaborator.objects.filter(game_list=OuterRef("pk"), user=owner, accepted_at__isnull=False)
    lists = GameList.objects.filter(Q(user=owner) | Q(Exists(joined)))
    if public_only:
        lists = lists.filter(Q(is_public=True) & (Q(user=owner) | Q(user__profile__is_public=True)))
    else:
        lists = lists.visible_to(viewer)
    lists = mark_progress(list(lists.with_preview()), viewer)
    for game_list in lists:
        game_list.from_other = game_list.user_id != owner.pk
    return lists


def mark_boards(lists):
    """Apunta en cada tier list de lists su tablero en miniatura (lists/_tier_card.html): board, con [(nivel, juegos,
    más)] de cada nivel en su orden (sus BOARD_COVERS primeros juegos y cuántos más tiene), y loose_count, cuántos
    están sin clasificar (también los de un nivel que ya no existe, como en GameList.tier_rows).

    Son dos consultas para todas: los primeros juegos de cada nivel y cuántos tiene cada uno. Devuelve la misma lista.

    Pantallas: Perfil (tablero en miniatura de cada tier list).
    """
    tiered = [game_list for game_list in lists if game_list.is_tiered]
    if not tiered:
        return lists
    items = ListItem.objects.filter(game_list__in=tiered)
    place = Window(RowNumber(), partition_by=[F("game_list"), F("tier")], order_by=[F("position"), F("id")])
    shown = {}
    for item in items.annotate(place=place).filter(place__lte=BOARD_COVERS).select_related("game").order_by("position", "id"):
        shown.setdefault((item.game_list_id, item.tier), []).append(item)
    sizes = {(list_id, tier): n for list_id, tier, n in items.values_list("game_list_id", "tier").annotate(n=Count("id"))}
    for game_list in tiered:
        game_list.board = []
        for level in game_list.tier_list:
            row = shown.get((game_list.pk, level["id"]), [])
            game_list.board.append((level, row, sizes.get((game_list.pk, level["id"]), 0) - len(row)))
        total = sum(n for (list_id, _), n in sizes.items() if list_id == game_list.pk)
        game_list.loose_count = total - sum(len(row) + more for _, row, more in game_list.board)
    return lists
