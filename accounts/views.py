import logging
import secrets
from collections import Counter
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from games import igdb, media

from games.models import LANGUAGE_FILTERS, Game, Genre, igdb_image_url, known_shops, language_filter
from library import wishlist
from library.models import LibraryEntry, Review
from library.views import mark_liked
from social.views import activity_page, follow_context

from . import images, matching, psn, steam, xbox
from .forms import IMAGE_FIELDS, PlatformAccountForm, ProfileForm, SignupForm
from .models import (
    ACCENT_PRESETS, SECTIONS, FavoriteGame, ImportedTitle, PlatformAccount, Profile, PSNAccount, SteamAccount,
    XboxAccount,
)

User = get_user_model()
STEAM_STATE_KEY = "steam_openid_state"
STEAM_KEY_MISSING = (
    "La importación de juegos de Steam no está configurada: falta STEAM_API_KEY en el archivo .env."
)
STEAM_PRIVATE_HINT = (
    "En Steam ve a Perfil › Editar perfil › Privacidad, pon «Detalles del juego» en Público y después "
    "pulsa «Sincronizar Steam»."
)
XBOX_NOT_CONFIGURED = (
    "La importación de Xbox no está configurada: faltan XBOX_CLIENT_ID y XBOX_CLIENT_SECRET en el archivo .env."
)
logger = logging.getLogger(__name__)


def signup(request):
    """Registro de cuenta nueva.

    Si el usuario ya ha iniciado sesión lo manda a su perfil. Al registrarse bien inicia su sesión y
    lo lleva al catálogo para que añada sus primeros juegos.
    """
    if request.user.is_authenticated:
        return redirect("profile", username=request.user.username)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Cuenta creada. Añade tus primeros juegos a la biblioteca.")
        return redirect("games:catalog")
    return render(request, "registration/signup.html", {"form": form})


def profile(request, username):
    """Perfil público de un usuario: cabecera personalizada, escaparate, su biblioteca y sus reseñas.

    El escaparate (favoritos, lo que está jugando, reseña destacada, estadísticas y géneros) sale en el
    orden y con las secciones que haya elegido. La biblioteca tiene pestañas por estado (Jugando,
    Completado...) con su contador y se filtra con ?estado= y por idioma con ?idioma= (voces o textos en
    castellano); se ordena con ?orden= (recientes, nota, horas o nombre). Con ?vista=resenas muestra sus
    reseñas en vez de la biblioteca, y con ?vista=actividad lo que ha hecho (desde ?antes=, el cursor de
    «Cargar más»). Un perfil privado da 404 a cualquiera que no sea su dueño.
    """
    owner = get_object_or_404(_profile_owners(), username=username)
    # 404 rather than 403 so private profiles don't confirm that the account exists.
    if not owner.profile.visible_to(request.user):
        raise Http404
    return render(request, "accounts/profile.html", _profile_context(request, owner))


@require_POST
@login_required
def profile_preview(request):
    """Vista previa de Editar perfil: la página del perfil tal como quedaría con lo que hay en el formulario.

    La pide el JavaScript de Editar perfil cada vez que se cambia algo y la enseña a la vez en un marco
    del ancho de un ordenador y en otro del de un móvil. No guarda nada (ver ProfileForm.preview). Sale
    como la ven los demás, sin los botones del dueño, aunque el perfil sea privado.
    """
    owner = _profile_owners().get(pk=request.user.pk)
    form = ProfileForm(request.POST, instance=owner.profile, user=owner)
    _, favorites = form.preview()
    return render(request, "accounts/profile.html", _profile_context(request, owner, favorites, preview=True))


def _profile_owners():
    """Usuarios con lo que enseña la cabecera del perfil ya cargado: perfil, juego de la cabecera y cuentas vinculadas."""
    return User.objects.select_related("profile", "profile__banner_game", "psn_account", "xbox_account").prefetch_related(
        "steam_accounts", "platform_accounts"
    )


def _profile_context(request, owner, favorites=None, preview=False):
    """Variables de la plantilla del perfil de owner, con la biblioteca filtrada y ordenada según ?estado=, ?idioma= y ?orden=.

    favorites son sus juegos favoritos en orden (si no se dan, los guardados). Con preview es la vista
    previa de Editar perfil: se ve como la ven los demás, el usuario de la barra superior es owner con los
    cambios sin guardar y la biblioteca se corta en PREVIEW_LIBRARY juegos. También lleva cuántos seguidores
    y seguidos tiene owner y si quien mira sigue a owner o al revés (social.views.follow_context).
    """
    is_owner = request.user == owner and not preview
    entries = list(
        LibraryEntry.objects.filter(user=owner).select_related("game").order_by("-updated_at")
    )
    counts = {value: 0 for value, _ in LibraryEntry.Status.choices}
    for entry in entries:
        counts[entry.status] += 1

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
    if view not in ("resenas", "actividad"):
        view = ""
    activities, next_cursor = [], ""
    if view == "actividad":
        # La actividad solo se pide en su pestaña.
        activities, next_cursor = activity_page(owner, request.user, request.GET.get("antes", ""))

    tabs = [(value, label, counts[value]) for value, label in LibraryEntry.Status.choices]
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
        "activities": activities,
        "next_cursor": next_cursor,
        # El escaparate solo va en la vista principal: al filtrar u ordenar, la biblioteca sube.
        "show_showcase": not (status or sort or view or language),
        "favorites": list(owner.favorite_games.select_related("game")) if favorites is None else favorites,
        "max_favorites": FavoriteGame.MAX_PER_USER,
        "playing": [e for e in entries if e.status == LibraryEntry.Status.PLAYING][:PLAYING_SHOWN],
        "pinned": next((review for review in reviews if review.pk == owner.profile.pinned_review_id), None),
        "stats": _profile_stats(owner, entries, counts, len(reviews)),
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
# Marcos del editor de encuadre de Editar perfil: (nombre, icono, proporción ancho/alto) de cada sitio donde
# se ve la imagen. La cabecera mide unos 1066 × 190 en un ordenador y 340 × 140 en un móvil (ver
# .profile-banner en app.css) y el fondo ocupa toda la ventana: 16:9 en un ordenador y 390 × 844 en un móvil.
FRAMER_VIEWS = {
    "avatar": [("", "", "1")],
    "banner": [("Escritorio", "monitor", f"{1066 / 190:.3f}"), ("Móvil", "phone", f"{340 / 140:.3f}")],
    "background": [("Escritorio", "monitor", f"{16 / 9:.3f}"), ("Móvil", "phone", f"{390 / 844:.3f}")],
}

# Valor de ?orden= → (etiqueta, clave de orden). Los juegos sin nota o sin horas jugadas van al final.
PROFILE_SORTS = {
    "nota": ("Mejor valorados", lambda e: -(e.rating or 0)),
    "horas": ("Más horas", lambda e: -(e.playtime_minutes or 0)),
    "nombre": ("Nombre", lambda e: e.game.name.casefold()),
}


def _profile_stats(owner, entries, counts, reviews):
    """Cifras de la cabecera del perfil: juegos, completados, horas jugadas (Steam + PlayStation + Xbox), nota media
    (en estrellas: las notas van en medias estrellas) y reseñas publicadas (reviews, ya contadas).

    También calcula los tres géneros más repetidos de su biblioteca.
    """
    ratings = [e.stars for e in entries if e.rating]
    minutes = sum(e.playtime_minutes or 0 for e in entries)
    # Una fila por (género, juego de la biblioteca), así que cada género cuenta una vez por juego.
    genres = Counter(Genre.objects.filter(games__library_entries__user=owner).values_list("name", flat=True))
    return {
        "games": len(entries),
        "completed": counts[LibraryEntry.Status.COMPLETED],
        "hours": round(minutes / 60) if minutes else None,
        "average_rating": sum(ratings) / len(ratings) if ratings else None,
        "reviews": reviews,
        "top_genres": [name for name, _ in genres.most_common(3)],
    }


@login_required
def edit_profile(request):
    """Editar perfil: datos, apariencia (foto, cabecera, fondo, color y diseño), escaparate, redes y privacidad.

    El formulario trabaja con una copia del perfil para que, si no es válido, la página (las imágenes
    subidas, la barra superior) siga enseñando lo guardado. Al lado va la vista previa del perfil en un
    ordenador y en un móvil, que pinta profile_preview con lo que hay en el formulario. Las tiendas y las
    cuentas vinculadas tienen sus propios formularios en la misma página.
    """
    profile = request.user.profile
    form = ProfileForm(
        request.POST or None, request.FILES or None, instance=Profile.objects.get(pk=profile.pk), user=request.user
    )
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Perfil actualizado.")
            return redirect("profile", username=request.user.username)
        messages.error(request, "No se ha guardado: revisa los campos marcados en rojo.")
    chosen = set(profile.shops)
    return render(
        request,
        "accounts/edit_profile.html",
        {
            "form": form,
            "profile": profile,
            "favorites": form.favorites,
            "max_favorites": FavoriteGame.MAX_PER_USER,
            "accent_presets": ACCENT_PRESETS,
            # Para el encuadre y para que el navegador reduzca antes de subirlas las fotos que no caben.
            "image_kinds": images.KINDS,
            "framer_views": FRAMER_VIEWS,
            "uploads": {name: getattr(profile, name).url if getattr(profile, name) else "" for name in IMAGE_FIELDS},
            "sections": _posted_sections(form) or profile.section_layout,
            "shop_choices": [(shop_id, name, shop_id in chosen) for shop_id, name in known_shops()],
            **linking_context(),
        },
    )


def _posted_sections(form):
    """Secciones tal como venían en un envío no válido (orden y visibles), para no perder lo que movió; [] si no hay."""
    if not form.is_bound or "secciones" not in form.data:
        return []
    names = dict(SECTIONS)
    visible = set(form.data.getlist("secciones_visibles"))
    keys = [key for key in dict.fromkeys(form.data.getlist("secciones")) if key in names]
    return [(key, names[key], key in visible) for key in keys]


def favorite_context(user, game, error=""):
    """Variables del botón «Añadir a mis favoritos» de la ficha de game para user."""
    favorites = list(user.favorite_games.values_list("game_id", flat=True))
    return {
        "is_favorite": game.pk in favorites,
        "favorites_count": len(favorites),
        "max_favorites": FavoriteGame.MAX_PER_USER,
        "favorite_error": error,
    }


@require_POST
@login_required
def favorite_toggle(request, game_id):
    """Añade el juego a los favoritos del perfil del usuario o lo quita si ya estaba.

    Como mucho FavoriteGame.MAX_PER_USER: con el cupo lleno avisa de que quite alguno. Los nuevos van al
    final. Con HTMX devuelve el botón actualizado; si no, vuelve a la ficha del juego.
    """
    game = get_object_or_404(Game, pk=game_id)
    favorites = request.user.favorite_games
    error = ""
    removed, _ = favorites.filter(game=game).delete()
    if not removed:
        if favorites.count() >= FavoriteGame.MAX_PER_USER:
            error = f"Ya tienes {FavoriteGame.MAX_PER_USER} favoritos: quita alguno en Editar perfil › Escaparate."
        else:
            last = favorites.aggregate(last=Max("position"))["last"]
            FavoriteGame.objects.create(user=request.user, game=game, position=0 if last is None else last + 1)
    if request.headers.get("HX-Request"):
        return render(request, "accounts/_favorite.html", {"game": game, **favorite_context(request.user, game, error)})
    if error:
        messages.error(request, error)
    return redirect(game)


# Horas que se guardan en caché las imágenes de IGDB de cada juego para las cabeceras.
ARTS_CACHE_HOURS = 24


def game_arts(game):
    """Imágenes apaisadas de IGDB (arte y capturas) de un juego para la cabecera: [{"id", "thumb", "full"}].

    «thumb» es la miniatura para elegirla y «full», el tamaño con el que se ve en la cabecera.

    Se guardan un día en caché. Lista vacía si no hay juego, el juego no viene de IGDB, IGDB no está
    configurado o no responde.
    """
    if game is None or not game.igdb_id or not igdb.is_configured():
        return []
    key = f"igdb:arts:{game.igdb_id}"
    ids = cache.get(key)
    if ids is None:
        try:
            ids = igdb.IGDBClient().artworks(game.igdb_id)
        except igdb.IGDBError:
            logger.exception("No se pudieron pedir a IGDB las imágenes de %s", game.slug)
            return []
        cache.set(key, ids, ARTS_CACHE_HOURS * 3600)
    return [
        {"id": image_id, "thumb": igdb_image_url(image_id, "t_screenshot_med"), "full": igdb_image_url(image_id, "t_1080p")}
        for image_id in ids
    ]


@login_required
def banner_arts(request):
    """Opciones de imagen para la cabecera con el juego elegido en Editar perfil (fragmento HTML).

    El formulario lo pide con HTMX al cargar y al cambiar de juego (?banner_game=). Solo vale con juegos
    de la biblioteca del usuario o con el que ya tiene en la cabecera.
    """
    profile = request.user.profile
    game_id = request.GET.get("banner_game", "")
    game = None
    if game_id.isdigit():
        game = Game.objects.filter(
            Q(library_entries__user=request.user) | Q(pk=profile.banner_game_id or 0), pk=int(game_id)
        ).first()
    selected = profile.banner_art if game is not None and game.pk == profile.banner_game_id else ""
    return render(
        request,
        "accounts/_banner_arts.html",
        {
            "game": game,
            "arts": game_arts(game),
            "selected": selected,
            "cover_full": igdb_image_url(game.cover_image_id, "t_1080p") if game else "",
            "igdb_configured": igdb.is_configured(),
        },
    )


@require_POST
@login_required
def banner_from_game(request, game_id):
    """Pone de cabecera del perfil una imagen de la ficha de un juego (el botón del visor del carrusel).

    Como en Editar perfil, solo con juegos de la biblioteca del usuario, y solo con imágenes apaisadas de
    la ficha (games.media.banner_arts). El encuadre vuelve al centro, como con cualquier imagen nueva. Con
    HTMX devuelve el aviso del visor; si no, vuelve a la ficha con un mensaje.
    """
    game = get_object_or_404(Game, pk=game_id)
    art = request.POST.get("art", "")
    error = ""
    if not LibraryEntry.objects.filter(user=request.user, game=game).exists():
        error = "Añade el juego a tu biblioteca para usar sus imágenes de cabecera."
    elif art not in media.banner_arts(game):
        error = "Esa imagen no se puede poner de cabecera."
    else:
        profile = request.user.profile
        profile.banner_mode = Profile.BannerMode.GAME
        profile.banner_game = game
        profile.banner_art = art
        profile.banner_x = profile.banner_y = 50
        profile.banner_zoom = 100
        profile.banner_fit = False
        profile.save(update_fields=[
            "banner_mode", "banner_game", "banner_art", "banner_x", "banner_y", "banner_zoom", "banner_fit",
        ])
    if request.headers.get("HX-Request"):
        return render(request, "accounts/_banner_toast.html", {"error": error})
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Hecho: es la nueva cabecera de tu perfil.")
    return redirect(game)


def linking_context():
    """Variables que necesitan los paneles de vinculación de cuentas (en Editar perfil y en el modal del perfil)."""
    return {
        "steam_configured": steam.is_configured(),
        "xbox_configured": xbox.is_configured(),
        "max_steam_accounts": SteamAccount.MAX_PER_USER,
        "psn_npsso_url": psn.NPSSO_URL,
        # (valor, plataforma, etiqueta del campo, ayuda) para el formulario de otras plataformas.
        "platform_rules": [
            (value, label, *PlatformAccount.RULES[value][:2])
            for value, label in PlatformAccount.Platform.choices
        ],
    }


def _back(request, default):
    """Redirige a la página de la que vino el formulario (campo «next») si es de esta web; si no, a default.

    Así las acciones lanzadas desde el modal del perfil vuelven al perfil y no a Editar perfil.
    """
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(default)


def _steam_return_to(request, state):
    """URL absoluta a la que Steam devuelve al usuario tras iniciar sesión, con el código anti-CSRF state."""
    return request.build_absolute_uri(f"{reverse('steam_callback')}?{urlencode({'state': state})}")


def _report_import(request, result):
    """Avisa al usuario de cuántos juegos se han importado de Steam, cuántos no estaban en el catálogo
    y qué cuentas se han saltado por ser privadas."""
    text = f"Steam: {result['added']} juego{pluralize(result['added'])} añadido{pluralize(result['added'])}"
    if result["updated"]:
        text += f", {result['updated']} con horas actualizadas"
    messages.success(request, _with_unmatched(text, result["unmatched"]))
    if result["private"]:
        messages.error(
            request,
            f"No se pudieron leer los juegos de {', '.join(result['private'])}. {STEAM_PRIVATE_HINT}",
        )


def _with_unmatched(text, unmatched):
    """Mensaje de resultado de una importación, con el aviso de los juegos no encontrados y un enlace para
    emparejarlos a mano (HTML seguro: el texto se escapa)."""
    if not unmatched:
        return f"{text}."
    return format_html(
        '{}. {} no se encontraron en el catálogo: <a href="{}">empareja a mano los que sean juegos</a>.',
        text, unmatched, reverse("imported_titles"),
    )


def _report_wishlist(request, result):
    """Avisa de lo que se ha traído de la lista de deseos de Steam y, con los precios de hoy, de cuántos de sus
    deseados están en su mínimo histórico o son más baratos fuera de Steam (HTML seguro: el texto se escapa).

    Los precios de PC de los juegos nuevos se piden en ese momento (library.wishlist.fetch_missing_prices).
    """
    if result["unreadable"]:
        messages.info(
            request,
            f"La lista de deseos de Steam de {', '.join(result['unreadable'])} está vacía o es privada: "
            "de ahí no se ha traído nada a «Lo quiero».",
        )
    total = result["total"]
    if not total:
        return
    wishlist.fetch_missing_prices([item.game for item in result["added"]])
    count, at_low, cheaper = wishlist.steam_deals(request.user)
    added, removed, owned, unmatched = len(result["added"]), result["removed"], result["owned"], result["unmatched"]
    details = [f"{added} nuevo{pluralize(added)} en «Lo quiero»"]
    if removed:
        details.append(f"{removed} quitado{pluralize(removed)} porque ya no está{pluralize(removed, 'n')} en Steam")
    if owned:
        details.append(f"{owned} que ya tienes")
    if unmatched:
        details.append(f"{unmatched} que no está{pluralize(unmatched, 'n')} en el catálogo")
    text = f"Lista de deseos de Steam: {total} juego{pluralize(total)} ({', '.join(details)})"
    if count:
        text += (
            f". Hoy, {at_low} está{pluralize(at_low, 'n')} en su mínimo histórico y {cheaper} "
            f"{'es' if cheaper == 1 else 'son'} más barato{pluralize(cheaper)} fuera de Steam"
        )
    messages.success(request, format_html('{}. <a href="{}">Ver tu lista de deseos</a>', text, reverse("library:wishlist")))


def _import_steam(request, include_unplayed=False):
    """Lanza la importación de todas las cuentas de Steam del usuario y le avisa del resultado o del fallo.

    Si lo tiene activado (Profile.steam_wishlist), trae también su lista de deseos de Steam a «Lo quiero»;
    que falle eso no estropea lo de la biblioteca. Si falla por Steam o IGDB deja el error completo en el
    log del servidor para poder revisarlo.
    """
    if not steam.is_configured():
        messages.error(request, STEAM_KEY_MISSING)
        return
    try:
        _report_import(request, steam.import_library(request.user, include_unplayed=include_unplayed))
    except steam.SteamPrivateError:
        messages.error(request, f"Steam no deja ver tus juegos. {STEAM_PRIVATE_HINT}")
    except (steam.SteamError, igdb.IGDBError):
        logger.exception("Falló la importación de Steam de %s", request.user.username)
        messages.error(request, "No se pudieron importar tus juegos de Steam. Inténtalo de nuevo más tarde.")
    if not request.user.profile.steam_wishlist:
        return
    try:
        _report_wishlist(request, steam.import_wishlist(request.user))
    except (steam.SteamError, igdb.IGDBError):
        logger.exception("Falló la importación de la lista de deseos de Steam de %s", request.user.username)
        messages.error(request, "No se pudo traer tu lista de deseos de Steam. Inténtalo de nuevo más tarde.")


@require_POST
@login_required
def steam_link(request):
    """Manda al usuario a iniciar sesión en Steam para vincular una cuenta más (Steam nunca nos da su contraseña).

    No deja pasar de SteamAccount.MAX_PER_USER cuentas.
    """
    if request.user.steam_accounts.count() >= SteamAccount.MAX_PER_USER:
        messages.error(request, f"Puedes vincular como mucho {SteamAccount.MAX_PER_USER} cuentas de Steam.")
        return redirect("edit_profile")
    state = request.session[STEAM_STATE_KEY] = secrets.token_urlsafe(16)
    return redirect(steam.login_url(_steam_return_to(request, state), request.build_absolute_uri("/")))


@login_required
def steam_callback(request):
    """Vuelta desde Steam: verifica el inicio de sesión, añade la cuenta a las del usuario e importa sus juegos.

    Rechaza la vuelta si el código anti-CSRF no coincide, si Steam no la confirma o si esa cuenta de
    Steam ya está vinculada a otro usuario. Si ya era suya, solo avisa.
    """
    expected = request.session.pop(STEAM_STATE_KEY, None)
    if not expected or request.GET.get("state") != expected:
        messages.error(request, "El enlace de Steam ha caducado. Vuelve a intentarlo.")
        return redirect("edit_profile")
    try:
        steam_id = steam.verify_login(request.GET, _steam_return_to(request, expected))
    except steam.SteamError:
        steam_id = None
    if not steam_id:
        messages.error(request, "No se pudo verificar tu cuenta de Steam.")
        return redirect("edit_profile")

    account = SteamAccount.objects.filter(steam_id=steam_id).first()
    if account is not None:
        if account.user_id != request.user.id:
            messages.error(request, "Esa cuenta de Steam ya está vinculada a otro usuario de NextPlay.")
        else:
            messages.info(
                request,
                f"La cuenta {account.name} ya estaba vinculada. Para añadir otra, cierra sesión en Steam "
                "(o pulsa «¿No eres tú?») antes de iniciar sesión.",
            )
        return redirect("edit_profile")
    if request.user.steam_accounts.count() >= SteamAccount.MAX_PER_USER:
        messages.error(request, f"Puedes vincular como mucho {SteamAccount.MAX_PER_USER} cuentas de Steam.")
        return redirect("edit_profile")

    account = SteamAccount.objects.create(user=request.user, steam_id=steam_id)
    steam.refresh_account(account)
    messages.success(request, f"Cuenta de Steam vinculada: {account.name}.")
    _import_steam(request)
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def steam_import(request):
    """Vuelve a importar los juegos de todas las cuentas de Steam vinculadas (con «unplayed» también los no jugados)."""
    accounts = list(request.user.steam_accounts.all())
    if not accounts:
        messages.error(request, "Primero vincula una cuenta de Steam.")
        return redirect("edit_profile")
    for account in accounts:
        steam.refresh_account(account)
    _import_steam(request, include_unplayed=bool(request.POST.get("unplayed")))
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def steam_auto_sync(request):
    """Activa (auto_sync=1) o desactiva la sincronización nocturna de la biblioteca de Steam del usuario."""
    profile = request.user.profile
    profile.auto_sync = request.POST.get("auto_sync") == "1"
    profile.save(update_fields=["auto_sync"])
    if profile.auto_sync:
        messages.success(request, "Tu biblioteca de Steam se sincronizará sola cada noche.")
    else:
        messages.success(request, "Sincronización nocturna desactivada: Steam solo se importará cuando lo pidas.")
    return _back(request, f"{reverse('edit_profile')}#steam")


@require_POST
@login_required
def steam_wishlist(request):
    """Activa (steam_wishlist=1) o desactiva que la importación de Steam traiga la lista de deseos a «Lo quiero».

    Con restore=1, olvida los juegos que quitó a mano para que la próxima importación los vuelva a traer.
    """
    profile = request.user.profile
    if request.POST.get("restore") == "1":
        profile.steam_wishlist_skip = []
        profile.save(update_fields=["steam_wishlist_skip"])
        messages.success(request, "Hecho: la próxima vez que sincronices Steam se traerán también los que habías quitado.")
    else:
        profile.steam_wishlist = request.POST.get("steam_wishlist") == "1"
        profile.save(update_fields=["steam_wishlist"])
        if profile.steam_wishlist:
            messages.success(request, "Tu lista de deseos de Steam se traerá a «Lo quiero» cada vez que se sincronice Steam.")
        else:
            messages.success(request, "Ya no se traerá tu lista de deseos de Steam. Lo que ya está en «Lo quiero» se queda.")
    return _back(request, f"{reverse('edit_profile')}#steam")


@require_POST
@login_required
def steam_unlink(request, account_id):
    """Desvincula una cuenta de Steam del usuario; los juegos importados se quedan en su biblioteca.

    Solo puede desvincular cuentas suyas: con el ID de una cuenta ajena responde 404.
    """
    account = get_object_or_404(SteamAccount, pk=account_id, user=request.user)
    account.delete()
    messages.success(
        request, f"Cuenta de Steam {account.name} desvinculada. Tus juegos importados siguen en tu biblioteca."
    )
    return _back(request, "edit_profile")


def _psn_error(request, text):
    """Avisa del error de PlayStation y vuelve a donde estaba el formulario (por defecto, Editar perfil)."""
    messages.error(request, text)
    return _back(request, f"{reverse('edit_profile')}#psn")


@require_POST
@login_required
def psn_sync(request):
    """Vincula la cuenta de PlayStation del código NPSSO pegado (o la actualiza) e importa sus juegos de PS4/PS5.

    El código solo se usa durante esta petición: no se guarda. Avisa si el código no tiene el formato
    esperado, si Sony no lo acepta o si esa cuenta de PlayStation ya es de otro usuario.
    """
    npsso = psn.clean_npsso(request.POST.get("npsso"))
    if not npsso:
        return _psn_error(request, "Ese no parece un código NPSSO: son 64 letras y números. Cópialo otra vez.")
    try:
        result = psn.import_library(request.user, npsso)
    except psn.PSNAuthError:
        return _psn_error(
            request,
            "PlayStation no ha aceptado el código: puede haber caducado. Inicia sesión otra vez en "
            "playstation.com, copia un código nuevo y vuelve a intentarlo.",
        )
    except ValueError as exc:
        return _psn_error(request, str(exc))
    except (psn.PSNError, igdb.IGDBError):
        logger.exception("Falló la importación de PlayStation de %s", request.user.username)
        return _psn_error(request, "No se pudieron importar tus juegos de PlayStation. Inténtalo de nuevo más tarde.")

    added, updated = result["added"], result["updated"]
    text = f"PlayStation ({result['account'].name}): {added} juego{pluralize(added)} añadido{pluralize(added)}"
    if updated:
        text += f", {updated} con horas actualizadas"
    messages.success(request, _with_unmatched(text, result["unmatched"]))
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def psn_unlink(request):
    """Desvincula la cuenta de PlayStation del usuario; los juegos importados se quedan en su biblioteca."""
    PSNAccount.objects.filter(user=request.user).delete()
    messages.success(request, "Cuenta de PlayStation desvinculada. Tus juegos importados siguen en tu biblioteca.")
    return _back(request, f"{reverse('edit_profile')}#psn")


@require_POST
@login_required
def platform_add(request):
    """Añade la cuenta de otra plataforma (Xbox, Switch, Epic, GOG, Battle.net) o cambia la que ya tenía.

    Si el formato no es el de esa plataforma avisa del error y no guarda nada.
    """
    form = PlatformAccountForm(request.POST)
    if form.is_valid():
        account, created = PlatformAccount.objects.update_or_create(
            user=request.user,
            platform=form.cleaned_data["platform"],
            defaults={"handle": form.cleaned_data["handle"]},
        )
        verb = "añadida" if created else "actualizada"
        messages.success(request, f"Cuenta de {account.get_platform_display()} {verb}: {account.handle}.")
    else:
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
    return _back(request, f"{reverse('edit_profile')}#plataformas")


@require_POST
@login_required
def platform_remove(request, platform):
    """Quita del perfil del usuario su cuenta de esa plataforma (no da error si no la tenía)."""
    PlatformAccount.objects.filter(user=request.user, platform=platform).delete()
    messages.success(request, "Cuenta quitada de tu perfil.")
    return _back(request, f"{reverse('edit_profile')}#plataformas")


XBOX_STATE_KEY = "xbox_oauth_state"


def _xbox_redirect_uri(request):
    """URL absoluta de vuelta desde Microsoft; tiene que coincidir con la registrada en la aplicación de Azure."""
    return request.build_absolute_uri(reverse("xbox_callback"))


@require_POST
@login_required
def xbox_link(request):
    """Manda al usuario a iniciar sesión con su cuenta Microsoft para vincular Xbox o volver a sincronizarla.

    Sirve para las dos cosas porque no se guarda ningún token: cada sincronización pide iniciar sesión.
    """
    if not xbox.is_configured():
        messages.error(request, XBOX_NOT_CONFIGURED)
        return _back(request, f"{reverse('edit_profile')}#xbox")
    state = request.session[XBOX_STATE_KEY] = secrets.token_urlsafe(16)
    return redirect(xbox.login_url(_xbox_redirect_uri(request), state))


@login_required
def xbox_callback(request):
    """Vuelta desde Microsoft: comprueba el código anti-CSRF, vincula la cuenta de Xbox e importa sus juegos.

    Si el usuario canceló el inicio de sesión, si el código no coincide o si Xbox Live falla, avisa y
    vuelve a Editar perfil.
    """
    expected = request.session.pop(XBOX_STATE_KEY, None)
    back = f"{reverse('edit_profile')}#xbox"
    if request.GET.get("error"):
        messages.error(request, "Has cancelado el inicio de sesión con Microsoft.")
        return redirect(back)
    if not expected or request.GET.get("state") != expected or not request.GET.get("code"):
        messages.error(request, "El enlace de Microsoft ha caducado. Vuelve a intentarlo.")
        return redirect(back)
    try:
        result = xbox.import_library(request.user, request.GET["code"], _xbox_redirect_uri(request))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    except (xbox.XboxError, igdb.IGDBError) as exc:
        logger.exception("Falló la importación de Xbox de %s", request.user.username)
        messages.error(request, f"No se pudieron importar tus juegos de Xbox. {exc}")
        return redirect(back)

    added, updated = result["added"], result["updated"]
    text = f"Xbox ({result['account'].name}): {added} juego{pluralize(added)} añadido{pluralize(added)}"
    if updated:
        text += f", {updated} con horas actualizadas"
    messages.success(request, _with_unmatched(text, result["unmatched"]))
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def xbox_unlink(request):
    """Desvincula la cuenta de Xbox del usuario; los juegos importados se quedan en su biblioteca."""
    XboxAccount.objects.filter(user=request.user).delete()
    messages.success(request, "Cuenta de Xbox desvinculada. Tus juegos importados siguen en tu biblioteca.")
    return _back(request, f"{reverse('edit_profile')}#xbox")


# Resultados como máximo al buscar el juego de un título importado; si en el catálogo salen menos de
# IMPORTED_SEARCH_IGDB_BELOW, también se busca en IGDB.
IMPORTED_SEARCH_RESULTS = 12
IMPORTED_SEARCH_IGDB_BELOW = 5


def _search_games(query):
    """Juegos del catálogo cuyo nombre contiene query, de más a menos popular, completando con IGDB si salen pocos."""
    games = list(Game.objects.filter(name__icontains=query).prefetch_related("platforms")[:IMPORTED_SEARCH_RESULTS])
    if len(games) < IMPORTED_SEARCH_IGDB_BELOW and igdb.is_configured():
        try:
            found = igdb.upsert_games(igdb.IGDBClient().search(query, limit=IMPORTED_SEARCH_RESULTS))
        except igdb.IGDBError:
            logger.exception("Falló la búsqueda en IGDB de «%s»", query)
            found = []
        games += [game for game in found if game not in games]
    return games[:IMPORTED_SEARCH_RESULTS]


@login_required
def imported_titles(request):
    """Juegos importados para revisar a mano.

    Sin parámetros, los títulos de Steam, PlayStation o Xbox del usuario que no se encontraron en el
    catálogo (con ?ocultos=1, los que ocultó). Con ?juego=<id>, los que han llevado a ese juego de su
    biblioteca, para corregirlo si no es el que jugó. Con ?titulo=<id>&q= busca candidatos para ese
    título (por defecto, por su nombre).
    """
    rows = request.user.imported_titles.select_related("title__game")
    game, hidden = None, request.GET.get("ocultos") == "1"
    if request.GET.get("juego", "").isdigit():
        game = get_object_or_404(Game, pk=request.GET["juego"])
        rows = rows.filter(title__game=game)
        back = f"{request.path}?juego={game.pk}"
    else:
        rows = rows.filter(title__game__isnull=True, hidden=hidden)
        back = f"{request.path}?ocultos=1" if hidden else request.path
    rows = list(rows)

    selected = None
    if request.GET.get("titulo", "").isdigit():
        selected = next((row for row in rows if row.pk == int(request.GET["titulo"])), None)
    search = (request.GET.get("q") or "").strip() or (selected.title.name if selected else "")
    return render(
        request,
        "accounts/imported_titles.html",
        {
            "rows": rows,
            "game": game,
            "hidden": hidden,
            "back": back,
            "selected": selected,
            # «search» y no «query»: query es la caja de búsqueda del catálogo en la cabecera.
            "search": search,
            "results": _search_games(search) if selected and search else [],
            "hidden_count": request.user.imported_titles.filter(title__game__isnull=True, hidden=True).count(),
        },
    )


@require_POST
@login_required
def imported_title_assign(request, pk):
    """Asigna a un título importado del usuario el juego elegido (o ninguno si «game» llega vacío).

    La corrección sirve para todos los usuarios con ese título y sus horas pasan al juego nuevo en su
    biblioteca. Responde 404 si el título no es suyo o el juego no existe.
    """
    imported = get_object_or_404(ImportedTitle.objects.select_related("title"), pk=pk, user=request.user)
    game_id = request.POST.get("game", "")
    game = get_object_or_404(Game, pk=game_id) if game_id.isdigit() else None
    try:
        result = matching.reassign(imported, game, request.user)
    except PermissionError as exc:
        messages.error(request, str(exc))
        return _back(request, reverse("imported_titles"))
    name = imported.title.name
    if game is None:
        messages.success(request, f"«{name}» queda sin emparejar.")
    else:
        text = f"«{name}» es ahora {game.name}, también para quien lo importe después"
        if result["removed"]:
            text += "; el juego anterior se ha quitado de tu biblioteca"
        messages.success(request, text + ".")
    return _back(request, reverse("imported_titles"))


@require_POST
@login_required
def imported_title_hide(request, pk):
    """Oculta (hidden=1) un título sin emparejar del usuario —herramientas, demos, apps— o lo vuelve a mostrar."""
    imported = get_object_or_404(ImportedTitle.objects.select_related("title"), pk=pk, user=request.user)
    imported.hidden = request.POST.get("hidden") == "1"
    imported.save(update_fields=["hidden"])
    verb = "ocultado" if imported.hidden else "vuelto a mostrar"
    messages.success(request, f"Has {verb} «{imported.title.name}».")
    return _back(request, reverse("imported_titles"))


NOTIFICATIONS_SHOWN = 50


@login_required
def notifications(request):
    """Últimos avisos del usuario (bajadas de precio, nuevos seguidores...). Al abrir la página, los no leídos pasan a leídos.

    Los que tienen quién los ha provocado (actor) traen ya lo que hace falta para enseñar su foto.
    """
    shown = list(
        request.user.notifications.select_related("game", "actor__profile", "actor__psn_account", "actor__xbox_account")
        .prefetch_related("actor__steam_accounts")[:NOTIFICATIONS_SHOWN]
    )
    unread = {n.pk for n in shown if n.read_at is None}
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return render(request, "accounts/notifications.html", {"shown": shown, "unread": unread})


@require_POST
@login_required
def shops_update(request):
    """Guarda las tiendas en las que compra el usuario (casillas «shops»). Ninguna o todas = enseñar todas,
    así las tiendas que aparezcan más adelante también se ven."""
    known = {shop_id for shop_id, _ in known_shops()}
    chosen = sorted({int(value) for value in request.POST.getlist("shops") if value.isdigit()} & known)
    profile = request.user.profile
    profile.shops = [] if set(chosen) == known else chosen
    profile.save(update_fields=["shops"])
    if profile.shops:
        messages.success(request, f"Guardado: te enseñaremos precios y avisos de {len(profile.shops)} tienda{pluralize(len(profile.shops))}.")
    else:
        messages.success(request, "Guardado: te enseñaremos todas las tiendas.")
    return redirect(f"{reverse('edit_profile')}#tiendas")
