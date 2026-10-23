"""Imágenes y vídeos de cada juego para la cabecera y el carrusel de la ficha.

Los artes, capturas, logos y vídeos se piden a IGDB en una sola consulta la primera vez que alguien abre
la ficha (y otra vez pasado MAX_AGE) y se guardan en Game.media:

    {"arts": [{"id", "type", "w", "h"}], "logos": [{"id", "type", "crop"}],
     "shots": [{"id", "w", "h"}], "videos": [{"id", "name"}],
     "steam": {"app_id", "hero", "logo"}}

«arts» son solo ilustraciones (sin logos, iconos, portadas ni infografías) con el arte principal primero;
«logos», el blanco y el de color, que pueden ir encima de la cabecera; «videos», tráileres de YouTube con
el nombre que les da IGDB (en inglés). Si el juego está en Steam, «steam» dice si su biblioteca tiene
imagen de cabecera (True, False o None si no se pudo mirar) y el recorte de su logo. Los recortes
(«crop», ver _logo_crop) quitan el margen transparente de los logos para que encajen en la cabecera.
"""

import io
from datetime import timedelta

import requests
from django.utils import timezone
from PIL import Image

from . import igdb
from .models import igdb_image_url
from .spanish import USER_AGENT

MAX_AGE = timedelta(days=30)
TIMEOUT = 8
STEAM_ASSET_URL = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/{name}"
YOUTUBE_THUMB_URL = "https://i.ytimg.com/vi/{video_id}/{name}.jpg"
# Tipos de arte con nombre propio en el carrusel; los demás son «Arte».
ART_LABELS = {2: "Arte principal", 3: "Arte principal", 4: "Arte conceptual"}
# Filtros del carrusel: tipo de las miniaturas → nombre del botón.
KINDS = {"video": "Vídeos", "captura": "Capturas", "arte": "Arte"}
SOURCES = {"Steam": "biblioteca de Steam", "IGDB": "IGDB"}


def steam_app_id(game):
    """ID de Steam de la imagen y el logo de la cabecera: el manual si lo hay; None si no está en Steam."""
    return game.steam_app_id_override or game.steam_app_id


def needs_update(game, now=None):
    """Indica si hay que pedir a IGDB las imágenes y vídeos del juego.

    Hay que pedirlos si el juego viene de IGDB y nunca se pidieron, tienen más de MAX_AGE o se pidieron
    con otro ID de Steam (o sin él).
    """
    if not game.igdb_id:
        return False
    now = now or timezone.now()
    steam = (game.media or {}).get("steam") or {}
    moved = bool(steam_app_id(game)) and steam.get("app_id") != steam_app_id(game)
    return game.media_updated_at is None or game.media_updated_at < now - MAX_AGE or moved


def update_media(game, client=None, now=None):
    """Pide a IGDB los artes, capturas, logos y vídeos del juego y los guarda en game.media (formato arriba).

    Si el juego está en Steam, mira también si su biblioteca tiene imagen de cabecera y logo. Del logo que
    se vaya a usar (el de Steam o, si no hay, el de IGDB) guarda el recorte sin el margen transparente.
    """
    data = (client or igdb.IGDBClient()).game_media(game.igdb_id)
    artworks = [art for art in data.get("artworks") or [] if art.get("image_id")]
    media = {
        "arts": [{**_size(art), "type": art.get("artwork_type")} for art in igdb.sort_arts(artworks)],
        # Solo los logos con transparencia: los que traen fondo taparían la cabecera con un recuadro.
        "logos": [
            {"id": art["image_id"], "type": kind}
            for kind in igdb.LOGO_TYPES for art in artworks
            if art.get("artwork_type") == kind and art.get("alpha_channel", True)
        ],
        "shots": [_size(shot) for shot in data.get("screenshots") or [] if shot.get("image_id")],
        "videos": [
            {"id": video["video_id"], "name": (video.get("name") or "").strip() or "Vídeo"}
            for video in sorted(data.get("videos") or [], key=_video_order) if video.get("video_id")
        ],
    }
    app_id = steam_app_id(game)
    if app_id:
        media["steam"] = {
            "app_id": app_id,
            "hero": _exists(STEAM_ASSET_URL.format(app_id=app_id, name="library_hero.jpg")),
            "logo": _logo_crop(STEAM_ASSET_URL.format(app_id=app_id, name="logo.png")),
        }
    if media["logos"] and not (media.get("steam") or {}).get("logo"):
        media["logos"][0]["crop"] = _logo_crop(_igdb_logo_url(media["logos"][0]))
    game.media = media
    game.media_updated_at = now or timezone.now()
    game.save(update_fields=["media", "media_updated_at"])


def _size(image):
    """ID y tamaño de una imagen de IGDB tal como se guardan en Game.media."""
    return {"id": image["image_id"], "w": image.get("width") or 0, "h": image.get("height") or 0}


def _video_order(video):
    """Orden de los vídeos: el tráiler de lanzamiento (o el de la fecha de salida), los demás tráileres, los de
    gameplay y el resto; dentro de cada grupo, el que antes subió IGDB."""
    name = (video.get("name") or "").casefold()
    if "launch" in name or "release" in name:
        rank = 0
    elif "trailer" in name:
        rank = 2 if "gameplay" in name else 1
    else:
        rank = 3
    return rank, video.get("id") or 0


def _exists(url):
    """Indica si la imagen de url existe (True o False), o None si no se pudo comprobar."""
    try:
        with requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, stream=True) as response:
            return response.status_code == 200
    except requests.RequestException:
        return None


def _logo_crop(url):
    """Recorte del logo de url sin su margen transparente, o None si no existe, no se puede leer o no tiene
    transparencia (lleva fondo y taparía la cabecera con un recuadro).

    Son fracciones de la imagen entera: izquierda «l», arriba «t», ancho «w» y alto «h» de lo que no es
    transparente, y la proporción ancho/alto de ese trozo («ratio»). Los logos de la biblioteca de Steam
    vienen en un lienzo de 16:9 con mucho margen arriba y abajo.
    """
    try:
        response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            return None
        image = Image.open(io.BytesIO(response.content))
        image.load()
    except (requests.RequestException, OSError):
        return None
    alpha = image.convert("RGBA").getchannel("A")
    if alpha.getextrema()[0] > 8:
        return None
    box = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
    if not box:
        return None
    left, top, right, bottom = box
    width, height = image.size
    return {
        "l": round(left / width, 4),
        "t": round(top / height, 4),
        "w": round((right - left) / width, 4),
        "h": round((bottom - top) / height, 4),
        "ratio": round((right - left) / (bottom - top), 4),
    }


def _igdb_logo_url(logo):
    """URL del logo de IGDB en PNG (con su transparencia), a un tamaño que se ve nítido en la cabecera."""
    return igdb_image_url(logo["id"], "t_logo_med_2x", ext="png")


def _is_wide(image):
    """Indica si la imagen es apaisada (o no se sabe su tamaño); las verticales se enseñan enteras, sin recortar."""
    return not (image.get("w") and image.get("h")) or image["w"] >= image["h"]


def hero(game):
    """Imagen y logo de la cabecera de la ficha: {"image", "fallback", "credit", "fallback_credit", "logo", "logo_style"}.

    La imagen es la de la biblioteca de Steam si el juego está en Steam y la tiene (o aún no se sabe: la
    página pasa a «fallback» si no carga); si no, el arte principal de IGDB sin logo, el arte apaisado más
    ancho o la primera captura. Sin imagen, la ficha se queda con la carátula desenfocada. El logo, el de
    Steam o el blanco o de color de IGDB, solo va cuando se sabe su recorte (logo_style, variables CSS);
    si no, el nombre en texto. «credit» dice de dónde salen; «fallback_credit», si se pasa a «fallback».
    """
    media = game.media or {}
    igdb_image = _hero_image(media)
    steam = media.get("steam") or {}
    app_id = steam_app_id(game)
    known = bool(app_id) and steam.get("app_id") == app_id
    if app_id and steam.get("hero") is not False:
        image, source, fallback = STEAM_ASSET_URL.format(app_id=app_id, name="library_hero.jpg"), "Steam", igdb_image
    else:
        image, source, fallback = igdb_image, "IGDB" if igdb_image else "", ""
    logo, logo_source, crop = "", "", None
    logos = media.get("logos") or []
    if known and steam.get("logo"):
        logo, logo_source, crop = STEAM_ASSET_URL.format(app_id=app_id, name="logo.png"), "Steam", steam["logo"]
    elif logos and logos[0].get("crop"):
        logo, logo_source, crop = _igdb_logo_url(logos[0]), "IGDB", logos[0]["crop"]
    return {
        "image": image,
        "fallback": fallback,
        "credit": _credit(source, logo_source),
        "fallback_credit": _credit("IGDB" if fallback else "", logo_source),
        "logo": logo,
        # Con punto decimal aunque la web esté en español: es CSS.
        "logo_style": "".join(f"--crop-{key}: {crop[key]:.4f}; " for key in ("l", "t", "w", "h", "ratio")).strip()
        if crop else "",
    }


def _credit(image_source, logo_source):
    """Texto que dice de dónde salen la imagen y el logo de la cabecera («Imagen y logo: biblioteca de Steam»)."""
    if image_source and image_source == logo_source:
        return f"Imagen y logo: {SOURCES[image_source]}"
    parts = [f"Imagen: {SOURCES[image_source]}" if image_source else "", f"Logo: {SOURCES[logo_source]}" if logo_source else ""]
    return " · ".join(part for part in parts if part)


def _hero_image(media):
    """URL de la mejor imagen de IGDB para la cabecera, o vacía si no hay ninguna.

    Es el arte principal sin logo; si no hay, el arte apaisado más ancho que no lleve el logo (repetiría
    el título); y si tampoco, la primera captura.
    """
    arts = [art for art in media.get("arts") or [] if _is_wide(art)]
    best = next((art for art in arts if art.get("type") == igdb.KEY_ART_WITHOUT_LOGO), None)
    if best is None:
        plain = [art for art in arts if art.get("type") not in igdb.KEY_ART_TYPES]
        best = max(plain, key=lambda art: art["w"], default=None)
    if best is None:
        best = next(iter(media.get("shots") or []), None)
    return igdb_image_url(best["id"], "t_1080p") if best else ""


def carousel(game):
    """Miniaturas y filtros del carrusel de la ficha: {"items": [...], "filters": [(tipo, nombre, cuántas)]}.

    Orden: el primer vídeo, el arte principal, las capturas, el resto de artes y el resto de vídeos. Cada
    miniatura es un dict con kind («video», «captura» o «arte»), tag (su etiqueta), heading y detail (el
    título del visor), label (para lectores de pantalla), thumb y full (URLs de la miniatura y de la
    imagen grande), tall (vertical: se enseña entera), youtube (ID del vídeo) y art (ID de IGDB que se
    puede poner de cabecera del perfil; vacío en los vídeos y en las imágenes verticales). Solo hay
    filtros si hay más de un tipo.
    """
    media = game.media or {}
    videos, arts, shots = media.get("videos") or [], media.get("arts") or [], media.get("shots") or []
    video_items = [_video_item(video, n, len(videos)) for n, video in enumerate(videos, 1)]
    art_items = [_image_item("arte", art, n, len(arts)) for n, art in enumerate(arts, 1)]
    shot_items = [_image_item("captura", shot, n, len(shots)) for n, shot in enumerate(shots, 1)]
    items = video_items[:1] + art_items[:1] + shot_items + art_items[1:] + video_items[1:]
    counts = [(kind, label, len(group)) for (kind, label), group in zip(KINDS.items(), (videos, shots, arts)) if group]
    return {"items": items, "filters": counts if len(counts) > 1 else []}


def _image_item(kind, image, n, total):
    """Miniatura del carrusel de un arte o una captura (ver carousel)."""
    tall = not _is_wide(image)
    name = ART_LABELS.get(image.get("type"), "Arte") if kind == "arte" else "Captura"
    return {
        "kind": kind,
        "tag": name,
        "heading": f"{name} {n} de {total}",
        "detail": "IGDB",
        "label": f"Ampliar: {name.lower()} {n} de {total}",
        # Las apaisadas, recortadas a 16:9 como la miniatura; las verticales, enteras.
        "thumb": igdb_image_url(image["id"], "t_720p" if tall else "t_screenshot_big"),
        "full": igdb_image_url(image["id"], "t_1080p"),
        "tall": tall,
        "youtube": "",
        "art": "" if tall else image["id"],
    }


def _video_item(video, n, total):
    """Miniatura del carrusel de un vídeo de YouTube (ver carousel); el vídeo se carga al pulsarlo en el visor."""
    return {
        "kind": "video",
        "tag": video["name"],
        "heading": f"Vídeo {n} de {total}",
        "detail": f"{video['name']} · YouTube",
        "label": f"Ver el vídeo «{video['name']}»",
        # hqdefault existe siempre; maxresdefault, más nítida para el visor, no en todos los vídeos.
        "thumb": YOUTUBE_THUMB_URL.format(video_id=video["id"], name="hqdefault"),
        "full": YOUTUBE_THUMB_URL.format(video_id=video["id"], name="maxresdefault"),
        "tall": False,
        "youtube": video["id"],
        "art": "",
    }


def banner_arts(game):
    """IDs de IGDB de las imágenes de la ficha que valen de cabecera del perfil: artes y capturas apaisados."""
    media = game.media or {}
    return {image["id"] for image in (media.get("arts") or []) + (media.get("shots") or []) if _is_wide(image)}
