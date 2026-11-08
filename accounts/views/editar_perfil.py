"""Vistas de Editar perfil (cuenta/perfil/): el formulario de datos, apariencia, escaparate, redes, privacidad y
avisos, su vista previa en directo, las imágenes de un juego para la cabecera y las tiendas en las que compras. Las
cuentas vinculadas van aparte, en cuentas_vinculadas.py.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import redirect, render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.views.decorators.http import require_POST

from games import igdb
from games.models import Game, igdb_image_url, known_shops

from .. import images
from ..forms import IMAGE_FIELDS, ProfileForm
from ..models import ACCENT_PRESETS, SECTIONS, FavoriteGame, Profile
from .cuentas_vinculadas import linking_context
from .perfil import _profile_context, _profile_owners

logger = logging.getLogger(__name__)


@require_POST
@login_required
def profile_preview(request):
    """Vista previa de Editar perfil: la página del perfil tal como quedaría con lo que hay en el formulario.

    La pide el JavaScript de Editar perfil cada vez que se cambia algo y la enseña a la vez en un marco
    del ancho de un ordenador y en otro del de un móvil. No guarda nada (ver ProfileForm.preview). Sale
    como la ven los demás, sin los botones del dueño, aunque el perfil sea privado.

    Pantallas: Editar perfil (vista previa del perfil).
    """
    owner = _profile_owners().get(pk=request.user.pk)
    form = ProfileForm(request.POST, instance=owner.profile, user=owner)
    _, favorites = form.preview()
    return render(request, "accounts/profile.html", _profile_context(request, owner, favorites, preview=True))


# Marcos del editor de encuadre de Editar perfil: (nombre, icono, proporción ancho/alto) de cada sitio donde
# se ve la imagen. La cabecera mide unos 1066 × 190 en un ordenador y 340 × 140 en un móvil (ver
# .profile-banner en app.css) y el fondo ocupa toda la ventana: 16:9 en un ordenador y 390 × 844 en un móvil.
FRAMER_VIEWS = {
    "avatar": [("", "", "1")],
    "banner": [("Escritorio", "monitor", f"{1066 / 190:.3f}"), ("Móvil", "phone", f"{340 / 140:.3f}")],
    "background": [("Escritorio", "monitor", f"{16 / 9:.3f}"), ("Móvil", "phone", f"{390 / 844:.3f}")],
}


@login_required
def edit_profile(request):
    """Editar perfil: datos, apariencia (foto, cabecera, fondo, color y diseño), escaparate, redes, privacidad y avisos.

    El formulario trabaja con una copia del perfil para que, si no es válido, la página (las imágenes
    subidas, la barra superior) siga enseñando lo guardado. Al lado va la vista previa del perfil en un
    ordenador y en un móvil, que pinta profile_preview con lo que hay en el formulario. Las tiendas y las
    cuentas vinculadas tienen sus propios formularios en la misma página.

    Pantallas: Editar perfil.
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
    """Secciones tal como venían en un envío no válido (orden y visibles), para no perder lo que movió; [] si no hay.

    Pantallas: Editar perfil (Escaparate › Secciones del perfil, cuando no se ha podido guardar).
    """
    if not form.is_bound or "secciones" not in form.data:
        return []
    names = dict(SECTIONS)
    visible = set(form.data.getlist("secciones_visibles"))
    keys = [key for key in dict.fromkeys(form.data.getlist("secciones")) if key in names]
    return [(key, names[key], key in visible) for key in keys]


# Horas que se guardan en caché las imágenes de IGDB de cada juego para las cabeceras.
ARTS_CACHE_HOURS = 24


def game_arts(game):
    """Imágenes apaisadas de IGDB (arte y capturas) de un juego para la cabecera: [{"id", "thumb", "full"}].

    «thumb» es la miniatura para elegirla y «full», el tamaño con el que se ve en la cabecera.

    Se guardan un día en caché. Lista vacía si no hay juego, el juego no viene de IGDB, IGDB no está
    configurado o no responde.

    Pantallas: Editar perfil (Apariencia › Cabecera: imágenes del juego elegido).
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

    Pantallas: Editar perfil (Apariencia › Cabecera: imágenes del juego elegido).
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
def shops_update(request):
    """Guarda las tiendas en las que compra el usuario (casillas «shops»). Ninguna o todas = enseñar todas,
    así las tiendas que aparezcan más adelante también se ven.

    Pantallas: Editar perfil (Tiendas en las que compras), Ficha del juego (Dónde comprarlo: primero tus tiendas),
    Lista de deseos (mejor precio en tus tiendas).
    """
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
