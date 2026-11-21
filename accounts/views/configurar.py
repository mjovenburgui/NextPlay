"""Configurar NextPlay (cuenta/configurar/): lo primero tras crear la cuenta, en cuatro pasos y un final.

1. Dónde juegas: las plataformas de «¿Dónde juegas?» (Profile.play_platforms).
2. Tus juegos: la forma recomendada de traer los juegos, con sus horas, de cada sitio en el que juega (lo mismo que la
   ventana «Vincular cuentas» del perfil). Los formularios de vincular vuelven aquí.
3. Dónde compras: las tiendas de las que quiere precios, historial y avisos (Profile.shops), marcadas de entrada según
   dónde juega, y si quiere las bajadas de su lista de deseos también por correo.
4. Tu perfil: cómo le ven los demás (la foto, el nombre, la biografía y los favoritos, con SetupProfileForm) y los
   avisos en el móvil o el ordenador desde el que está, con el mismo botón de Editar perfil.

El final (Listo) resume lo que ha dejado configurado, le propone gente a la que seguir que juega a lo mismo y apunta que
ha terminado (Profile.setup_done_at): Inicio deja de recordárselo. Cada paso se puede saltar, todo se puede volver a
hacer cuando se quiera y todo está también en Editar perfil.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.models import NINTENDO_SHOP_ID, STEAM_SHOP_ID, XBOX_SHOP_ID, known_shops
from library.models import LibraryEntry
from social.views import name_shared_games, suggested_people

from .. import images, notices, push
from ..forms import SetupProfileForm
from ..models import PLAY_PLATFORMS, FavoriteGame, Profile
from .aplicacion import devices_context
from .comun import shop_picker
from .cuentas_vinculadas import linking_context

# Los pasos, en orden: (clave, nombre en la barra de pasos, nombre de su URL). Después de ellos va el final, Listo.
STEPS = [
    ("donde", "Dónde juegas", "setup"),
    ("juegos", "Tus juegos", "setup_games"),
    ("tiendas", "Dónde compras", "setup_shops"),
    ("perfil", "Tu perfil", "setup_profile"),
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
# Gente que se le propone seguir en el final: la que juega a lo mismo o, si aún no tiene juegos, la más activa.
PEOPLE = 6


def _context(request, current):
    """Lo que comparten las pantallas de Configurar NextPlay: el paso en que está (step: una clave de STEPS o «listo»),
    la barra de pasos (steps: número, nombre, dirección, si es el actual y si ya está hecho) y las direcciones del paso
    anterior y del siguiente (back_url y next_url; el siguiente del último es Listo).

    Un paso está hecho si ya hay lo que pide: dónde juega, algún juego en la biblioteca, las tiendas elegidas (ver
    _shops_chosen) o algo de su perfil (el nombre, la biografía, una foto subida o algún favorito).

    Pantallas: Configurar NextPlay (barra de pasos y botones Atrás, Saltar y Seguir).
    """
    profile = request.user.profile
    done = {
        "donde": bool(profile.where_you_play),
        "juegos": LibraryEntry.objects.filter(user=request.user).exists(),
        "tiendas": _shops_chosen(profile),
        "perfil": bool(profile.display_name or profile.bio or profile.avatar)
        or FavoriteGame.objects.filter(user=request.user).exists(),
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


def _shops_chosen(profile):
    """Indica si ya ha pasado por Dónde compras: tiene tiendas elegidas o ha dicho si quiere las bajadas por correo (con
    todas las tiendas, Profile.shops se queda vacío).

    Pantallas: Configurar NextPlay (barra de pasos), Inicio (el recordatorio de terminar de configurar NextPlay).
    """
    return bool(profile.shops) or notices.EMAIL in (profile.notification_prefs or {}).get("precios", {})


def next_step_url(user):
    """Dónde seguir Configurar NextPlay: en Dónde juegas si aún no lo ha dicho, en Tus juegos si su biblioteca está
    vacía, en Dónde compras si aún no ha pasado por ahí y, si no, en Tu perfil.

    Pantallas: Inicio (el recordatorio de terminar de configurar NextPlay).
    """
    profile = user.profile
    if not profile.where_you_play:
        return reverse("setup")
    if not LibraryEntry.objects.filter(user=user).exists():
        return reverse("setup_games")
    if not _shops_chosen(profile):
        return reverse("setup_shops")
    return reverse("setup_profile")


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
    tiendas, van marcadas las de los sitios en los que juega (SUGGESTED_SHOPS). Al seguir lo guarda como Editar perfil
    (ninguna o todas = todas; solo cuentan las tiendas con precios) y pasa a Tu perfil.

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
        return redirect("setup_profile")
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
def setup_profile(request):
    """Paso 4, Tu perfil: cómo le ven los demás (la foto, el nombre visible, la biografía y los favoritos, con
    SetupProfileForm) y los avisos en el dispositivo desde el que está (la tarjeta de Editar perfil, en pequeño y sin
    recargar la página, para no perder lo escrito). Al terminar lo guarda y pasa a Listo. El formulario trabaja con una
    copia del perfil, como Editar perfil, para que si no es válido la barra de arriba siga enseñando lo guardado.

    Pantallas: Configurar NextPlay (Tu perfil), Perfil (nombre, foto, biografía y favoritos), todas (tu nombre y tu foto
    en la barra de arriba), Avisos (las notificaciones en el móvil o el ordenador).
    """
    profile = request.user.profile
    form = SetupProfileForm(
        request.POST or None, request.FILES or None, instance=Profile.objects.get(pk=profile.pk), user=request.user
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("setup_done")
    return render(request, "accounts/setup/perfil.html", {
        **_context(request, "perfil"),
        **devices_context(request.user),
        "form": form,
        "profile": profile,
        # Para que el navegador reduzca antes de subirla la foto que no cabe, como en Editar perfil.
        "avatar_kind": images.KINDS["avatar"],
        "max_favorites": FavoriteGame.MAX_PER_USER,
    })


@login_required
def setup_done(request):
    """El final de Configurar NextPlay: lo que ha dejado configurado (dónde juega, cuántos juegos tiene, de qué tiendas
    verá precios, si le llegarán las bajadas por correo, cómo le ven, sus favoritos y en cuántos dispositivos tiene los
    avisos), gente a la que seguir (social.views.suggested_people: primero quien tiene más juegos en común, nombrados
    con name_shared_games, y si no, quien más ha hecho estos días) y adónde ir ahora. Apunta que ha terminado
    (Profile.setup_done_at), así que Inicio deja de recordárselo.

    Pantallas: Configurar NextPlay (Listo), Inicio (ya no recuerda terminar de configurar; en «Siguiendo», lo de quien
    sigas desde aquí).
    """
    profile = request.user.profile
    if profile.setup_done_at is None:
        profile.setup_done_at = timezone.now()
        profile.save(update_fields=["setup_done_at"])
    chosen = set(profile.shops)
    people = name_shared_games(request.user, suggested_people(request.user, limit=PEOPLE))
    return render(request, "accounts/setup/listo.html", {
        **_context(request, "listo"),
        "profile": profile,
        "where_names": [name for key, name, _ in PLAY_PLATFORMS if key in profile.where_you_play],
        "game_count": LibraryEntry.objects.filter(user=request.user).count(),
        # En el orden de siempre: las destacadas primero.
        "shop_names": [name for shop_id, name in known_shops() if shop_id in chosen],
        "price_email": notices.enabled(profile.notification_prefs, "precios", notices.EMAIL),
        "favorite_names": [favorite.game.name for favorite in request.user.favorite_games.select_related("game")],
        "push_configured": push.is_configured(),
        "push_count": request.user.push_subscriptions.count(),
        "people": people,
        "people_share_games": any(person.shared for person in people),
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
