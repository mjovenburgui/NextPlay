"""Vista de la pestaña Tier lists (tier-lists/): las tuyas, las de la gente que sigues y las abiertas a la comunidad,
de las que tienen más versiones a las últimas.
"""

from django.db.models import Count, Q
from django.shortcuts import render

from ..models import GameList
from .tarjetas import mark_boards, mark_progress, profile_lists

# Tier lists de cada sección de la pestaña: dos filas de tarjetas en el ordenador.
SECTION_SIZE = 8


def explore(request):
    """Pestaña Tier lists: con sesión, las tuyas (también tus versiones y en las que colaboras) y las últimas de la
    gente que sigues; para todos, las abiertas a la comunidad con más versiones y las últimas que han cambiado. Cada
    una con su tablero en miniatura (mark_boards) y, las abiertas, cuántas versiones públicas tiene (version_count).
    Solo salen las que puede ver quien mira y, con sesión, las suyas solo arriba.

    Pantallas: Tier lists.
    """
    viewer = request.user
    tier_lists = GameList.objects.filter(mode=GameList.Mode.TIERS).visible_to(viewer).with_preview()
    open_lists = tier_lists.filter(is_open=True, is_public=True).annotate(
        version_count=Count(
            "versions", filter=Q(versions__is_public=True, versions__user__profile__is_public=True), distinct=True,
        ),
    )
    context = {"mine": [], "followed": []}
    if viewer.is_authenticated:
        mine = [game_list for game_list in profile_lists(viewer, viewer) if game_list.is_tiered]
        context["mine"], context["mine_total"] = mine[:SECTION_SIZE], len(mine)
        others = tier_lists.exclude(user=viewer)
        context["followed"] = list(others.filter(user__followers__follower=viewer)[:SECTION_SIZE])
        open_lists = open_lists.exclude(user=viewer)
    context["popular"] = list(
        open_lists.filter(version_count__gt=0).order_by("-version_count", "-updated_at", "-id")[:SECTION_SIZE]
    )
    context["recent"] = list(open_lists[:SECTION_SIZE])
    others_shown = context["followed"] + context["popular"] + context["recent"]
    mark_boards(context["mine"] + others_shown)
    mark_progress(others_shown, viewer)
    return render(request, "lists/explore.html", context)
