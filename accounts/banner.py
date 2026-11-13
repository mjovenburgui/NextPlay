"""Cabecera y color automáticos del perfil: la cabecera «Lo que estoy jugando» (Profile.BannerMode.PLAYING), que sigue
sola al juego al que estás jugando, y el color del perfil sacado de la imagen de la cabecera
(Profile.accent_from_banner).

El juego es el de «Jugando» con la última partida más reciente (playing_game) y su imagen, el mejor arte apaisado de
IGDB (games.media.best_art). Se ponen al día al importar (accounts.matching.update_library), al pasar un juego a
«Jugando» en su ficha y al guardar Editar perfil, nunca al abrir el perfil, que así no espera a nadie. El color sale de
una miniatura de la imagen (dominant_color), se guarda en caché por imagen y se ajusta para que se lea sobre el fondo
oscuro de NextPlay.

Pantallas: Perfil (cabecera y color), Editar perfil (Apariencia › Cabecera y Color del perfil), Ficha del juego (al
pasar un juego a «Jugando»).
"""

import colorsys
import logging
from datetime import timedelta
from io import BytesIO

import requests
from django.core.cache import cache
from django.db.models import F
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from games import igdb, media
from games.models import igdb_image_url
from games.spanish import USER_AGENT
from library.models import LibraryEntry

from .models import Profile

logger = logging.getLogger(__name__)
# Sin ningún juego en «Jugando», vale el último que se ha jugado de toda la biblioteca si es de estos últimos días.
RECENT_DAYS = 30
# Cuánto se guarda el color de cada imagen: las de IGDB no cambian, y una subida nueva lleva otro nombre.
COLOR_CACHE_DAYS = 30
TIMEOUT = 8
# El color del perfil sacado de una imagen, en HLS: lo bastante claro para leerse sobre el fondo casi negro y lo
# bastante vivo para no parecer gris.
MIN_LIGHTNESS, MAX_LIGHTNESS, MIN_SATURATION = 0.55, 0.72, 0.45
# Los colores de la imagen que no dicen nada de ella: grises, casi negros y casi blancos. En una imagen apagada (todo
# grises con algo de tono, como muchas de fantasía oscura) vale un tono más leve, TINT_SATURATION; si ni eso, ninguno.
GREY_SATURATION, TINT_SATURATION, DARKEST, LIGHTEST = 0.2, 0.03, 0.1, 0.95
# Los encuadres de la cabecera, que se vuelven a centrar cuando cambia el juego: el de una imagen no vale para otra.
FRAMING = {"banner_x": 50, "banner_y": 50, "banner_zoom": 100, "banner_fit": False}


def playing_game(user):
    """El juego de la cabecera «Lo que estoy jugando»: de los de «Jugando», el de lo último que ha pasado con él, que es
    su última partida o, si es después, cuando lo pusiste en «Jugando» desde su ficha (LibraryEntry.marked_playing_at);
    sin ninguna de las dos, el último que ha cambiado. Si no hay ninguno en «Jugando», el último jugado de toda la
    biblioteca, si es de los últimos RECENT_DAYS días. None si no hay ninguno.

    Pantallas: Perfil (cabecera), Editar perfil (Apariencia › Cabecera: el juego que saldría).
    """
    entries = LibraryEntry.objects.filter(user=user).select_related("game")
    # La más reciente de las dos fechas (en SQLite, Greatest con un nulo da nulo: por eso cada una cubre a la otra).
    latest = Greatest(Coalesce("last_played", "marked_playing_at"), Coalesce("marked_playing_at", "last_played"))
    entry = (
        entries.filter(status=LibraryEntry.Status.PLAYING).annotate(latest=latest)
        .order_by(F("latest").desc(nulls_last=True), "-updated_at").first()
    )
    if entry is None:
        since = timezone.now() - timedelta(days=RECENT_DAYS)
        entry = entries.filter(last_played__gte=since).order_by("-last_played").first()
    return entry.game if entry else None


def known_art(game):
    """El ID de la mejor imagen que ya se conoce de game (games.media.best_art), sin pedir nada a IGDB; vacío si no hay.

    Pantallas: Editar perfil (Apariencia › Cabecera: la vista previa y el encuadre de «Lo que estoy jugando»).
    """
    best = media.best_art(game.media or {})
    return best["id"] if best else ""


def playing_image(game):
    """La imagen con la que saldría game en la cabecera, sin pedir nada a IGDB: {"src", "blurred"}, con su portada
    desenfocada si aún no se conoce su arte. Vacío si no hay juego.

    Pantallas: Editar perfil (Apariencia › Cabecera: el encuadre de «Lo que estoy jugando»).
    """
    if game is None:
        return {"src": "", "blurred": False}
    art = known_art(game)
    image_id = art or game.cover_image_id
    return {"src": igdb_image_url(image_id, "t_1080p") if image_id else "", "blurred": not art}


def art_for(game):
    """El ID de IGDB de la mejor imagen apaisada de game para la cabecera (games.media.best_art), pidiendo antes sus
    imágenes a IGDB si nunca se han pedido o son viejas. Vacío si no tiene: la cabecera se queda con su portada
    desenfocada, como cuando se elige un juego a mano.

    Pantallas: Perfil (cabecera), Editar perfil (Apariencia › Cabecera).
    """
    if igdb.is_configured() and media.needs_update(game):
        try:
            media.update_media(game)
        except (igdb.IGDBError, requests.RequestException):
            logger.exception("No se pudieron pedir a IGDB las imágenes de %s para una cabecera", game.slug)
    return known_art(game)


def refresh_playing(profile, save=True):
    """Pone al día la cabecera «Lo que estoy jugando» de profile: si el juego de playing_game ha cambiado (o aún no tenía
    imagen), le pone ese juego, su imagen y el encuadre de serie y, con accent_from_banner, el color que sale de ella.
    Con save lo guarda. No hace nada si la cabecera es de otro tipo o si no hay ningún juego. Devuelve si ha cambiado.

    Pantallas: Perfil (cabecera y color), Editar perfil (Apariencia › Cabecera), Ficha del juego (al pasar un juego a
    «Jugando»).
    """
    if profile.banner_mode != Profile.BannerMode.PLAYING:
        return False
    game = playing_game(profile.user)
    if game is None or (game.pk == profile.banner_game_id and profile.banner_art):
        return False
    profile.banner_game = game
    profile.banner_art = art_for(game)
    for name, value in FRAMING.items():
        setattr(profile, name, value)
    fields = ["banner_game", "banner_art", *FRAMING]
    if apply_color(profile, save=False):
        fields.append("accent_color")
    if save:
        profile.save(update_fields=fields)
    return True


def refresh_user(user):
    """refresh_playing con el perfil de user tal como está guardado, solo si su cabecera es «Lo que estoy jugando»: el de
    user.profile puede ser una copia de antes de cambiarla. Lo llaman la importación y el cambio de estado de un juego.

    Pantallas: Perfil (cabecera y color), Ficha del juego (al pasar un juego a «Jugando»), Editar perfil (Cuentas
    vinculadas: al importar).
    """
    profile = Profile.objects.filter(user=user, banner_mode=Profile.BannerMode.PLAYING).first()
    return refresh_playing(profile) if profile else False


def preview(profile):
    """Para la vista previa de Editar perfil: pone en profile, sin guardar, el juego de «Lo que estoy jugando» con la
    imagen que ya se conoce de él (sin pedir nada a IGDB, para que la vista previa no espere) y, si lo pide, el color
    de la cabecera.

    Pantallas: Editar perfil (vista previa).
    """
    if profile.banner_mode == Profile.BannerMode.PLAYING and (game := playing_game(profile.user)):
        if game.pk != profile.banner_game_id or not profile.banner_art:
            profile.banner_game, profile.banner_art = game, known_art(game)
            for name, value in FRAMING.items():
                setattr(profile, name, value)
    apply_color(profile, save=False)


def apply_color(profile, save=True):
    """Si profile pide el color de la cabecera (accent_from_banner), le pone el que sale de su imagen (banner_color). Con
    save lo guarda. Devuelve si ha cambiado.

    Pantallas: Perfil (color del perfil), Editar perfil (Apariencia › Color del perfil).
    """
    if not profile.accent_from_banner:
        return False
    color = banner_color(profile)
    if not color or color == profile.accent_color:
        return False
    profile.accent_color = color
    if save:
        profile.save(update_fields=["accent_color"])
    return True


def banner_color(profile):
    """El color que sale de la cabecera de profile (dominant_color): de la imagen subida o de la del juego (su arte o, si
    no tiene, su portada, pedidas a IGDB en miniatura). Se guarda en caché por imagen. Vacío con la cabecera de
    degradado, sin imagen o si no se ha podido leer.

    Pantallas: Perfil (color del perfil), Editar perfil (Apariencia › Color del perfil).
    """
    modes = Profile.BannerMode
    if profile.banner_mode == modes.IMAGE and profile.banner:
        key = f"color:subida:{profile.banner.name}"

        def read():
            """La imagen subida, del almacenamiento de los perfiles."""
            with profile.banner.open("rb") as handle:
                return handle.read()
    elif profile.banner_mode in (modes.GAME, modes.PLAYING) and profile.banner_game_id:
        image_id = profile.banner_art or profile.banner_game.cover_image_id
        if not image_id:
            return ""
        key = f"color:igdb:{image_id}"

        def read():
            """La miniatura de la imagen en IGDB."""
            url = igdb_image_url(image_id, "t_screenshot_med" if profile.banner_art else "t_cover_big")
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            response.raise_for_status()
            return response.content
    else:
        return ""
    color = cache.get(key)
    if color is None:
        try:
            color = dominant_color(read())
        except (OSError, requests.RequestException):
            logger.exception("No se pudo leer la cabecera de %s para sacar su color", profile.user)
            return ""
        cache.set(key, color, COLOR_CACHE_DAYS * 86400)
    return color


def dominant_color(content):
    """El color más representativo de una imagen (bytes) como «#rrggbb», o vacío si no se puede leer o es toda gris.

    Reduce la imagen a 8 colores (Pillow, quantize) sobre una miniatura y se queda con el que más pesa contando también
    lo vivo que es, sin los grises, los casi negros ni los casi blancos. Si la imagen es apagada y no tiene ninguno, se
    queda con el que más pesa de los que tienen algo de tono (TINT_SATURATION): el gris azulado de una imagen nocturna
    da un azul. Después lo aclara o lo aviva lo justo para que se lea sobre el fondo oscuro de NextPlay
    (MIN_LIGHTNESS, MAX_LIGHTNESS y MIN_SATURATION).

    Pantallas: Perfil (color del perfil), Editar perfil (Apariencia › Color del perfil).
    """
    try:
        with Image.open(BytesIO(content)) as image:
            image = image.convert("RGB")
            image.thumbnail((96, 96))
    except (UnidentifiedImageError, OSError):
        return ""
    paletted = image.quantize(colors=8)
    palette = paletted.getpalette()
    colors = []  # (cuántos píxeles, tono, luz, saturación) de los que no son casi negros ni casi blancos
    for count, index in paletted.getcolors():
        red, green, blue = (value / 255 for value in palette[index * 3:index * 3 + 3])
        hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
        if DARKEST < lightness < LIGHTEST:
            colors.append((count, hue, lightness, saturation))
    vivid = [color for color in colors if color[3] >= GREY_SATURATION]
    tinted = [color for color in colors if color[3] >= TINT_SATURATION]
    if vivid:
        best = max(vivid, key=lambda color: color[0] * (0.4 + color[3]))[1:]
    elif tinted:
        best = max(tinted, key=lambda color: color[0])[1:]
    else:
        return ""
    hue, lightness, saturation = best
    lightness = min(max(lightness, MIN_LIGHTNESS), MAX_LIGHTNESS)
    saturation = max(saturation, MIN_SATURATION)
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in colorsys.hls_to_rgb(hue, lightness, saturation))
