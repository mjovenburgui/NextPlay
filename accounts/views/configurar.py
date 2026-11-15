"""Configurar NextPlay (cuenta/configurar/): lo primero tras crear la cuenta, en tres pasos y un final.

1. Dónde juegas: las plataformas de «¿Dónde juegas?» (Profile.play_platforms).
2. Tus juegos: la forma recomendada de traer los juegos, con sus horas, de cada sitio en el que juega (lo mismo que la
   ventana «Vincular cuentas» del perfil). Los formularios de vincular vuelven aquí.
3. Dónde compras: las tiendas de las que quiere precios, historial y avisos (Profile.shops), marcadas de entrada según
   dónde juega, y si quiere las bajadas de su lista de deseos también por correo.

El final (Listo) resume lo que ha dejado configurado y apunta que ha terminado (Profile.setup_done_at): Inicio deja de
recordárselo. Cada paso se puede saltar, todo se puede volver a hacer cuando se quiera y todo está también en Editar
perfil.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.models import NINTENDO_SHOP_ID, STEAM_SHOP_ID, XBOX_SHOP_ID, known_shops
from library.models import LibraryEntry

from .. import notices
from ..models import PLAY_PLATFORMS
from .comun import shop_picker
from .cuentas_vinculadas import linking_context

# Los pasos, en orden: (clave, nombre en la barra de pasos, nombre de su URL). Después de ellos va el final, Listo.
STEPS = [
    ("donde", "Dónde juegas", "setup"),
    ("juegos", "Tus juegos", "setup_games"),
    ("tiendas", "Dónde compras", "setup_shops"),
]
# Tiendas que se proponen según dónde juega (claves de PLAY_PLATFORMS → ids de GamePrice.shop_id). De PlayStation Store
# aún no hay precios, así que PlayStation no propone ninguna.
SUGGESTED_SHOPS = {
    "steam": (STEAM_SHOP_ID,),
    # Epic, GOG, EA, Ubisoft y Blizzard.
    "pc": (16, 35, 52, 62, 4),
    # Xbox y Microsoft Store.
    "xbox": (XBOX_SHOP_ID, 48),
    "switch": (NINTENDO_SHOP_ID,),
}


def _context(request, current):
    """Lo que comparten las pantallas de Configurar NextPlay: el paso en que está (step: una clave de STEPS o «listo»),
    la barra de pasos (steps: número, nombre, dirección, si es el actual y si ya está hecho) y las direcciones del paso
    anterior y del siguiente (back_url y next_url; el siguiente del último es Listo).

    Un paso está hecho si ya hay lo que pide: dónde juega, algún juego en la biblioteca o alguna tienda elegida.

    Pantallas: Configurar NextPlay (barra de pasos y botones Atrás, Saltar y Seguir).
    """
    profile = request.user.profile
    done = {
        "donde": bool(profile.where_you_play),
        "juegos": LibraryEntry.objects.filter(user=request.user).exists(),
        "tiendas": bool(profile.shops),
    }
    keys = [key for key, _, _ in STEPS]
    position = keys.index(current) if current in keys else len(keys)
    urls = [reverse(url_name) for _, _, url_name in STEPS] + [reverse("setup_done")]
    return {
        "step": current,
        "steps": [
            (number, label, urls[number - 1], key == current, done[key])
            for number, (key, label, _) in enumerate(STEPS, start=1)
        ],
        "back_url": urls[position - 1] if position else "",
        "next_url": urls[position + 1] if position < len(keys) else "",
    }


def next_step_url(user):
    """Dónde seguir Configurar NextPlay: en Dónde juegas si aún no lo ha dicho, en Tus juegos si su biblioteca está vacía
    y, si no, en Dónde compras.

    Pantallas: Inicio (el recordatorio de terminar de configurar NextPlay).
    """
    if not user.profile.where_you_play:
        return reverse("setup")
    if not LibraryEntry.objects.filter(user=user).exists():
        return reverse("setup_games")
    return reverse("setup_shops")


@login_required
def setup(request):
    """Paso 1, Dónde juegas: las plataformas en las que juega (PLAY_PLATFORMS), marcadas las que ya dijo o ya tiene
    vinculadas (Profile.where_you_play). Al seguir las guarda y pasa a Tus juegos; si no marca ninguna, se le pide que
    elija o que se lo salte. Las claves que no existen se ignoran.

    Pantallas: Configurar NextPlay (Dónde juegas).
    """
    profile = request.user.profile
    error = False
    if request.method == "POST":
        picked = set(request.POST.getlist("donde"))
        chosen = [key for key, _, _ in PLAY_PLATFORMS if key in picked]
        if chosen:
            profile.play_platforms = chosen
            profile.save(update_fields=["play_platforms"])
            return redirect("setup_games")
        error = True
    return render(request, "accounts/setup/donde.html", {
        **_context(request, "donde"),
        "profile": profile,
        "where": profile.where_you_play,
        "where_error": error,
        "play_platform_choices": PLAY_PLATFORMS,
    })


@login_required
def setup_games(request):
    """Paso 2, Tus juegos: la forma recomendada de traer los juegos de cada sitio en el que juega (Profile.where_steps,
    lo mismo que la ventana «Vincular cuentas»), con los formularios de vincular volviendo aquí, y cuántos juegos tiene
    ya. Si no ha dicho dónde juega, se lo recuerda y ofrece las otras formas. Enseña una vez el código de Playnite recién
    generado.

    Pantallas: Configurar NextPlay (Tus juegos).
    """
    return render(request, "accounts/setup/juegos.html", {
        **_context(request, "juegos"),
        **linking_context(),
        "profile": request.user.profile,
        "next": request.path,
        "game_count": LibraryEntry.objects.filter(user=request.user).count(),
        "playnite_token": request.session.pop("playnite_token", None),
    })


@login_required
def setup_shops(request):
    """Paso 3, Dónde compras: las tiendas de las que quiere precios, historial y avisos (Profile.shops, con el selector
    de accounts/_shop_picker.html) y si quiere las bajadas de su lista de deseos también por correo. Si aún no ha elegido
    tiendas, van marcadas las de los sitios en los que juega (SUGGESTED_SHOPS). Al terminar lo guarda como Editar perfil
    (ninguna o todas = todas; solo cuentan las tiendas con precios) y pasa a Listo.

    Pantallas: Configurar NextPlay (Dónde compras), Ficha del juego (Dónde comprarlo: primero tus tiendas), Lista de
    deseos (mejor precio en tus tiendas), Avisos (bajadas de precio por correo).
    """
    profile = request.user.profile
    known = {shop_id for shop_id, _ in known_shops()}
    if request.method == "POST":
        chosen = sorted({int(value) for value in request.POST.getlist("shops") if value.isdigit()} & known)
        profile.shops = [] if set(chosen) == known else chosen
        prefs = dict(profile.notification_prefs or {})
        prefs["precios"] = {**prefs.get("precios", {}), notices.EMAIL: bool(request.POST.get("correo"))}
        profile.notification_prefs = prefs
        profile.save(update_fields=["shops", "notification_prefs"])
        return redirect("setup_done")
    marked = profile.shops or [shop for key in profile.where_you_play for shop in SUGGESTED_SHOPS.get(key, ())]
    return render(request, "accounts/setup/tiendas.html", {
        **_context(request, "tiendas"),
        **shop_picker(marked),
        "profile": profile,
        "suggested": not profile.shops and any(shop in known for shop in marked),
        "plays_playstation": "playstation" in profile.where_you_play,
        "price_email": notices.enabled(profile.notification_prefs, "precios", notices.EMAIL),
    })


@login_required
def setup_done(request):
    """El final de Configurar NextPlay: lo que ha dejado configurado (dónde juega, cuántos juegos tiene, de qué tiendas
    verá precios y si le llegarán las bajadas por correo) y adónde ir ahora. Apunta que ha terminado
    (Profile.setup_done_at), así que Inicio deja de recordárselo.

    Pantallas: Configurar NextPlay (Listo), Inicio (ya no recuerda terminar de configurar).
    """
    profile = request.user.profile
    if profile.setup_done_at is None:
        profile.setup_done_at = timezone.now()
        profile.save(update_fields=["setup_done_at"])
    chosen = set(profile.shops)
    return render(request, "accounts/setup/listo.html", {
        **_context(request, "listo"),
        "profile": profile,
        "where_names": [name for key, name, _ in PLAY_PLATFORMS if key in profile.where_you_play],
        "game_count": LibraryEntry.objects.filter(user=request.user).count(),
        # En el orden de siempre: las destacadas primero.
        "shop_names": [name for shop_id, name in known_shops() if shop_id in chosen],
        "price_email": notices.enabled(profile.notification_prefs, "precios", notices.EMAIL),
    })


@require_POST
@login_required
def setup_later(request):
    """«No me hace falta» del recordatorio de Inicio: da por terminada la configuración sin hacerla, así que Inicio deja
    de recordarla. Se puede hacer cuando se quiera desde Editar perfil.

    Pantallas: Inicio (el recordatorio de terminar de configurar NextPlay).
    """
    profile = request.user.profile
    profile.setup_done_at = timezone.now()
    profile.save(update_fields=["setup_done_at"])
    messages.info(request, "Vale. Si cambias de idea, lo tienes en Editar perfil.")
    return redirect("home")
