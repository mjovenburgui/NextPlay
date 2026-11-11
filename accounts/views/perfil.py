"""Vista del Perfil (u/<usuario>/): cabecera, escaparate y pestañas Biblioteca, Reseñas, Listas, Tier lists y Actividad. Lo
mismo pinta la vista previa de Editar perfil (editar_perfil.profile_preview).
"""

from collections import Counter
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from challenges import progress as challenge_progress
from games.models import LANGUAGE_FILTERS, Game, Genre, language_filter
from library import backlog, diary, stats as library_stats
from library.models import HOURS_SOURCES, LibraryEntry, Review
from library.views import mark_liked
from lists.views import SHOWCASE_LISTS, SHOWCASE_TIER_LISTS, mark_boards, profile_lists
from social import affinity
from social.views import activity_context, follow_context

from .. import achievements
from ..models import FavoriteGame
from .cuentas_vinculadas import linking_context

User = get_user_model()


def profile(request, username):
    """Perfil público de un usuario: cabecera personalizada, escaparate, su biblioteca y sus reseñas.

    El escaparate (favoritos, lo que está jugando, reseña destacada, estadísticas y géneros) sale en el
    orden y con las secciones que haya elegido. La biblioteca tiene pestañas por estado (Jugando,
    Completado...) con su contador y se filtra con ?estado= y por idioma con ?idioma= (voces o textos en
    castellano); se ordena con ?orden= (recientes, nota, horas o nombre). Con ?vista=resenas muestra sus
    reseñas en vez de la biblioteca, con ?vista=listas sus listas, con ?vista=tier-lists sus tier lists, con
    ?vista=actividad lo que ha hecho (desde ?antes=, el cursor de «Cargar más»), con ?vista=diario su diario de
    partidas del año ?anio= (si no lo ha ocultado), con ?vista=estadisticas sus estadísticas y con ?vista=comun, a quien
    mira con sesión, lo que tiene en común con él. Un perfil privado da 404 a cualquiera que no sea su dueño.

    Pantallas: Perfil.
    """
    owner = get_object_or_404(_profile_owners(), username=username)
    # 404 rather than 403 so private profiles don't confirm that the account exists.
    if not owner.profile.visible_to(request.user):
        raise Http404
    return render(request, "accounts/profile.html", _profile_context(request, owner))


def _profile_owners():
    """Usuarios con lo que enseña la cabecera del perfil ya cargado: perfil, juego de la cabecera y cuentas vinculadas.

    Pantallas: Perfil (cabecera: perfil, juego de la cabecera y cuentas), Editar perfil (vista previa).
    """
    return User.objects.select_related("profile", "profile__banner_game", "psn_account", "xbox_account").prefetch_related(
        "steam_accounts", "platform_accounts"
    )


def _profile_context(request, owner, favorites=None, preview=False):
    """Variables de la plantilla del perfil de owner, con la biblioteca filtrada y ordenada según ?estado=, ?idioma= y ?orden=.

    favorites son sus juegos favoritos en orden (si no se dan, los guardados). Con preview es la vista
    previa de Editar perfil: se ve como la ven los demás (también sus listas: solo las públicas), el usuario
    de la barra superior es owner con los cambios sin guardar y la biblioteca se corta en PREVIEW_LIBRARY
    juegos. También lleva cuántos seguidores y seguidos tiene owner y si quien mira sigue a owner o al revés
    (social.views.follow_context), y sus listas y, aparte, sus tier lists (lists.views.profile_lists), para sus
    pestañas y el escaparate; las tier lists que se enseñan llevan su tablero en miniatura (lists.views.mark_boards).
    Con sus logros importados (accounts.achievements), cada juego de la biblioteca lleva su insignia de «Platino» o
    «100 %» (achievement_badge) y las cifras de la cabecera, sus platinos y sus juegos al 100 %; si el escaparate
    enseña la sección «Logros», lleva también sus últimos logros y el más raro (achievements.showcase). Si enseña la
    sección «Retos», sus insignias y los retos que lleva en marcha, solo los que quien mira puede ver
    (challenges.progress.showcase). A su dueño le dice cuántos juegos le quedan por repasar (library.backlog.pending,
    para el aviso que lleva a Repasar la biblioteca) y, en la pestaña Pendiente, lo que cuesta hoy (backlog.value).
    La pestaña Diario solo sale si quien mira puede verla (library.diary.can_see; en la vista previa, como la verían
    los demás) y lleva el calendario del año de ?anio=, la última partida y las últimas sesiones (diary.page). La
    pestaña En común solo sale a quien mira con sesión un perfil que no es el suyo, con cuántos juegos tienen los dos
    (social.affinity.common_count) y, en ella, lo compatibles que son y esos juegos (affinity.in_common).

    Pantallas: Perfil (cabecera, escaparate y pestañas), Editar perfil (vista previa).
    """
    is_owner = request.user == owner and not preview
    entries = list(
        LibraryEntry.objects.filter(user=owner).select_related("game").order_by("-updated_at")
    )
    counts = {value: 0 for value, _ in LibraryEntry.Status.choices}
    for entry in entries:
        counts[entry.status] += 1
    # Sus logros importados: la insignia de «Platino» o «100 %» de cada portada y las cifras de la cabecera.
    progress = achievements.progress_by_game(owner, [entry.game_id for entry in entries])
    for entry in entries:
        entry.achievement_badge = achievements.badge(progress.get(entry.game_id, []))

    status = request.GET.get("estado")
    if status in counts:
        shown = [e for e in entries if e.status == status]
    else:
        status = ""
        shown = entries

    sort = request.GET.get("orden", "")
    if sort not in PROFILE_SORTS:
        sort = ""
    if sort:
        shown = sorted(shown, key=PROFILE_SORTS[sort][1])

    language = request.GET.get("idioma", "")
    if language not in LANGUAGE_FILTERS:
        language = ""
    if language:
        allowed = set(
            Game.objects.filter(language_filter(language), pk__in={e.game_id for e in shown}).values_list("pk", flat=True)
        )
        shown = [e for e in shown if e.game_id in allowed]

    # Sus reseñas publicadas (con «me gusta» y comentarios contados) y, si es él quien mira, sus borradores.
    reviews = mark_liked(list(Review.objects.filter(user=owner).visible_to(request.user)), request.user)
    drafts = list(Review.objects.filter(user=owner, is_draft=True).select_related("game")) if is_owner else []
    view = request.GET.get("vista", "")
    # En la vista previa, el diario como lo verían los demás: sin él si lo ha ocultado.
    show_diary = diary.can_see(owner, AnonymousUser() if preview else request.user)
    # «En común» solo tiene sentido para alguien con sesión que mira el perfil de otro.
    show_common = request.user.is_authenticated and request.user != owner and not preview
    tabs_allowed = ("resenas", "listas", "tier-lists", "actividad", "diario", "estadisticas", "comun")
    if view not in tabs_allowed or (view == "diario" and not show_diary) or (view == "comun" and not show_common):
        view = ""
    # La actividad solo se pide en su pestaña.
    activity_tab = activity_context(owner, request.user, request.GET.get("antes", "")) if view == "actividad" else {}
    # En la vista previa, las listas como las vería cualquiera: solo las públicas y sin el progreso de quien mira.
    lists = profile_lists(owner, AnonymousUser() if preview else request.user, public_only=preview)
    # Las tier lists van aparte; el tablero en miniatura, solo el de las que se enseñan (todas en su pestaña).
    tier_lists = [game_list for game_list in lists if game_list.is_tiered]
    lists = [game_list for game_list in lists if not game_list.is_tiered]
    mark_boards(tier_lists if view == "tier-lists" else tier_lists[:SHOWCASE_TIER_LISTS])

    tabs = [(value, label, counts[value]) for value, label in LibraryEntry.Status.choices]
    # El escaparate solo va en la vista principal: al filtrar u ordenar, la biblioteca sube.
    show_showcase = not (status or sort or view or language)
    return {
        "owner": owner,
        "entries": shown[:PREVIEW_LIBRARY] if preview else shown,
        "total": len(entries),
        "tabs": tabs,
        "active_status": status,
        "sorts": [(value, label) for value, (label, _) in PROFILE_SORTS.items()],
        "active_sort": sort,
        "language_filters": LANGUAGE_FILTERS.items(),
        "active_language": language,
        # Orden e idioma elegidos, para no perderlos al cambiar de pestaña de estado.
        "keep_qs": urlencode({k: v for k, v in (("orden", sort), ("idioma", language)) if v}),
        "view": view,
        "reviews": reviews,
        "drafts": drafts,
        "lists": lists,
        "showcase_lists": lists[:SHOWCASE_LISTS],
        "tier_lists": tier_lists,
        "showcase_tier_lists": tier_lists[:SHOWCASE_TIER_LISTS],
        "activities": [],
        "next_cursor": "",
        **activity_tab,
        "show_diary": show_diary,
        "diary": diary.page(owner, _year(request.GET.get("anio", ""))) if view == "diario" else None,
        # La pestaña Estadísticas, con sus barras y los años que tienen resumen (library.stats.overview).
        "full_stats": library_stats.overview(owner) if view == "estadisticas" else None,
        "show_common": show_common,
        "common_count": affinity.common_count(request.user, owner) if show_common else 0,
        "common": affinity.in_common(request.user, owner) if view == "comun" else None,
        "show_showcase": show_showcase,
        "achievement_showcase": (
            achievements.showcase(owner) if show_showcase and "logros" in owner.profile.visible_sections else None
        ),
        # Lo que le queda por repasar, para el aviso que lleva a Repasar la biblioteca: solo para su dueño.
        "tidy_counts": dict(zip(("playing", "played"), map(len, backlog.pending(entries)))) if is_owner else None,
        "backlog_value": backlog.value(owner) if status == LibraryEntry.Status.BACKLOG else None,
        # En la vista previa, los retos como los vería cualquiera: sin los privados.
        "challenge_showcase": (
            challenge_progress.showcase(owner, AnonymousUser() if preview else request.user)
            if show_showcase and "retos" in owner.profile.visible_sections else None
        ),
        "favorites": list(owner.favorite_games.select_related("game")) if favorites is None else favorites,
        "max_favorites": FavoriteGame.MAX_PER_USER,
        "playing": [e for e in entries if e.status == LibraryEntry.Status.PLAYING][:PLAYING_SHOWN],
        "pinned": next((review for review in reviews if review.pk == owner.profile.pinned_review_id), None),
        "stats": {**_profile_stats(owner, entries, counts, len(reviews)), **achievements.totals(progress)},
        "is_owner": is_owner,
        "unmatched_count": (
            owner.imported_titles.filter(title__game__isnull=True, hidden=False).count() if is_owner else 0
        ),
        **(linking_context() if is_owner else {}),
        **follow_context(request.user, owner),
        # En la vista previa, la barra superior también enseña la foto y el nombre sin guardar.
        **({"preview": True, "user": owner} if preview else {}),
    }


# Juegos que se enseñan en la sección «Jugando ahora» del escaparate del perfil.
PLAYING_SHOWN = 6
# Juegos de la biblioteca que salen en la vista previa de Editar perfil: bastan para ver cómo queda y
# así la página pesa poco aunque la biblioteca tenga cientos.
PREVIEW_LIBRARY = 24
# Valor de ?orden= → (etiqueta, clave de orden). Los juegos sin nota o sin horas jugadas van al final.
PROFILE_SORTS = {
    "nota": ("Mejor valorados", lambda e: -(e.rating or 0)),
    "horas": ("Más horas", lambda e: -(e.playtime_minutes or 0)),
    "nombre": ("Nombre", lambda e: e.game.name.casefold()),
}


def _year(value):
    """El año de ?anio= como número, o None si no es un año.

    Pantallas: Perfil (pestaña Diario: el año que se ve).
    """
    return int(value) if value.isdigit() and len(value) == 4 else None


def _profile_stats(owner, entries, counts, reviews):
    """Cifras de la cabecera del perfil: juegos, completados, horas jugadas (Steam + PlayStation + Xbox) y las de cada
    plataforma (hours_by_platform, de la que tiene más a la que menos y solo las que tienen alguna), nota media (en
    estrellas: las notas van en medias estrellas) y reseñas publicadas (reviews, ya contadas).

    También calcula los tres géneros más repetidos de su biblioteca.

    Pantallas: Perfil (cifras de la cabecera: juegos, completados y horas).
    """
    ratings = [e.stars for e in entries if e.rating]
    minutes = sum(e.playtime_minutes or 0 for e in entries)
    by_platform = {key: sum(e.minutes_on(key) or 0 for e in entries) for key in HOURS_SOURCES}
    # Una fila por (género, juego de la biblioteca), así que cada género cuenta una vez por juego.
    genres = Counter(Genre.objects.filter(games__library_entries__user=owner).values_list("name", flat=True))
    return {
        "games": len(entries),
        "completed": counts[LibraryEntry.Status.COMPLETED],
        "hours": round(minutes / 60) if minutes else None,
        "hours_by_platform": [
            {"key": key, "name": HOURS_SOURCES[key], "hours": round(platform_minutes / 60)}
            for key, platform_minutes in sorted(by_platform.items(), key=lambda item: -item[1])
            if round(platform_minutes / 60)
        ],
        "average_rating": sum(ratings) / len(ratings) if ratings else None,
        "reviews": reviews,
        "top_genres": [name for name, _ in genres.most_common(3)],
    }
